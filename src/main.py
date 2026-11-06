"""A single, full-width Mole cleanup scene, opened directly with `mo `."""

import asyncio
import time
from pathlib import Path

from wox_plugin import (
    ActionContext,
    Context,
    CopyParams,
    Plugin,
    PluginInitParams,
    PublicAPI,
    Query,
    QueryLayout,
    QueryResponse,
    QueryType,
    Result,
    ResultAction,
    UpdatableResult,
    WoxImage,
)
from .dashboard import LABELS, Session, summary
from .runner import CleanupRunner
from .scene import HouseScene
from .views import render


class MolePlugin(Plugin, CleanupRunner):
    def __init__(self) -> None:
        self.api: PublicAPI | None = None
        self.session = Session()
        self.scene = HouseScene()
        self.task: asyncio.Task | None = None
        self.scene_task: asyncio.Task | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.clean_context: Context | None = None
        self.labels = LABELS.copy()
        self.assets = Path(__file__).resolve().parent.parent / "image"
        self.active = False
        self.stop_requested = False
        self.unloading = False
        self.publish_lock = asyncio.Lock()
        self._visibility_checked = 0.0
        self._visible = True
        self._published: tuple | None = None
        self._published_actions: tuple | None = None

    async def init(self, ctx: Context, init_params: PluginInitParams) -> None:
        self.api = init_params.api
        self.assets = Path(init_params.plugin_directory) / "image"
        await self.api.on_unload(ctx, self.on_unload)
        await self.api.on_leave_plugin_query(ctx, self.leave_query)
        for key in LABELS:
            value = await self.api.get_translation(ctx, "preview_" + key)
            if value and value not in ("preview_" + key, "i18n:preview_" + key):
                self.labels[key] = value
        # Explicit maintenance actions do not belong in MRU. The companion scene
        # is launcher-only; it does not expose an unattended cleanup tool.

    async def leave_query(self, ctx: Context) -> None:
        self.active = False
        self.clean_context = None

    async def query(self, ctx: Context, query: Query) -> QueryResponse:
        self.active = query.type == QueryType.INPUT and query.trigger_keyword == "mo"
        if not self.active:
            self.clean_context = None
            return QueryResponse(results=[])
        self.clean_context = ctx
        self._published = None
        self._published_actions = None
        self._visible = True
        self._visibility_checked = 0
        return QueryResponse(
            results=[
                Result(
                    id="mole-clean",
                    title="i18n:title_clean",
                    icon=WoxImage.new_relative("image/app.png"),
                    preview=self.preview(),
                    actions=self.actions(),
                )
            ],
            layout=QueryLayout(result_preview_width_ratio=0),
        )

    def preview(self):
        self.scene.update(time.monotonic(), self.session.state)
        if (
            self.session.state in ("running", "completed")
            and not self.scene.frozen
            and self.active
            and not self.unloading
            and self.clean_context
            and (self.scene_task is None or self.scene_task.done())
        ):
            self.scene_task = asyncio.create_task(self.animate_scene(self.clean_context, self.session))
        return render(self.session, self.scene, self.labels, self.assets)

    async def animate_scene(self, ctx: Context, session: Session) -> None:
        """Schedule only choreography boundaries; Wox owns every animation frame."""
        try:
            while self.session is session and self.active and not self.unloading and not self.scene.frozen:
                delay = min(1.0, max(0.02, self.scene.deadline - time.monotonic())) if self._visible else 1.0
                await asyncio.sleep(delay)
                await self.publish(ctx)
        finally:
            if self.scene_task is asyncio.current_task():
                self.scene_task = None

    def actions(self) -> list[ResultAction]:
        running = self.task is not None and not self.task.done()
        name = "action_stop" if running else ("action_clean" if self.session.state == "ready" else "action_again")
        actions = [
            ResultAction(
                name="i18n:" + name,
                action=self.stop_clean if running else self.start_clean,
                is_default=True,
                prevent_hide_after_action=True,
                icon=WoxImage.new_relative("image/stop.svg" if running else "image/execute.svg"),
            )
        ]
        if self.session.state in ("failed", "stopped") and self.session.logs:
            actions.append(
                ResultAction(
                    name="i18n:action_copy_output",
                    action=self.copy_output,
                    prevent_hide_after_action=True,
                    icon=WoxImage.new_relative("image/copy.svg"),
                )
            )
        return actions

    async def copy_output(self, ctx: Context, action_context: ActionContext) -> None:
        if self.api:
            await self.api.copy(ctx, CopyParams(text="\n".join(self.session.logs)))

    async def publish(self, ctx: Context) -> None:
        """Serialize snapshots and scope them to the latest live query."""
        if not self.api or not self.active or self.unloading or not self.clean_context:
            return
        async with self.publish_lock:
            if not self.active or self.unloading or not self.clean_context:
                return
            query_context = self.clean_context
            now = time.monotonic()
            if now - self._visibility_checked >= 1:
                self._visibility_checked = now
                try:
                    self._visible = await asyncio.wait_for(self.api.is_visible(query_context), 0.5)
                except Exception:
                    self._visible = True
            if not self._visible or not self.active or self.unloading or self.clean_context is not query_context:
                return
            self.scene.update(now, self.session.state)
            view = summary(self.session, self.labels, now)
            signature = (
                id(query_context),
                self.session.started_at,
                self.session.state,
                view["title"],
                view["amount"],
                view["message"],
                self.scene.motion_id,
                self.scene.mode,
                self.scene.facing,
                self.scene.frozen,
                self.task is not None,
            )
            if signature == self._published:
                return
            action_state = (
                id(query_context),
                self.session.state,
                self.task is not None,
                bool(self.session.logs) if self.session.state in ("failed", "stopped") else False,
            )
            actions = self.actions() if action_state != self._published_actions else None
            try:
                await asyncio.wait_for(
                    self.api.update_result(
                        query_context,
                        UpdatableResult(id="mole-clean", preview=self.preview(), actions=actions),
                    ),
                    2,
                )
                self._published = signature
                self._published_actions = action_state
            except Exception:
                # UI transport failures must never fail or interrupt actual cleanup.
                pass

    async def start_clean(self, ctx: Context, action_context: ActionContext) -> None:
        if self.unloading or self.task and not self.task.done():
            return
        if self.scene_task:
            self.scene_task.cancel()
            self.scene_task = None
        self.stop_requested = False
        self.session = Session(state="running")
        self.scene = HouseScene()
        self.scene.start(time.monotonic())
        self.task = asyncio.create_task(self.run_clean(ctx))
        await self.publish(ctx)

    async def stop_clean(self, ctx: Context, action_context: ActionContext) -> None:
        self.stop_requested = True
        try:
            await self.terminate()
        except OSError as exc:
            self.stop_requested = False
            self.session.feed(str(exc))
            await self.publish(ctx)

    async def on_unload(self, ctx: Context) -> None:
        self.unloading = True
        self.stop_requested = True
        if self.scene_task:
            self.scene_task.cancel()
            try:
                await self.scene_task
            except asyncio.CancelledError:
                pass
        await self.terminate()
        if self.task:
            await self.task


plugin = MolePlugin()
