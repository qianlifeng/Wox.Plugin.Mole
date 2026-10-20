"""Native Wox commands and an in-app Mole cleanup session."""

import asyncio
import codecs
import os
import re
import subprocess
import sys
import time

from wox_plugin import (
    ActionContext,
    ChangeQueryParam,
    Context,
    Plugin,
    PluginInitParams,
    PublicAPI,
    Query,
    QueryLayout,
    QueryResponse,
    QueryType,
    Result,
    ResultAction,
    ResultTail,
    ResultTailType,
    RefreshQueryParam,
    UpdatableResult,
    WoxImage,
    WoxPreview,
)
from .dashboard import LABELS, Session, render, script_update
from .runtime import MoleRuntime
from .commands import CommandSession
from .uninstall import UninstallBackend

COMMANDS = ("clean", "uninstall", "optimize", "analyze", "status")
EXECUTE_ICON = WoxImage.new_svg(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="var(--wox-theme-icon-color)" stroke-width="1.8" stroke-linejoin="round">'
    '<path d="M13 2 4.5 13.5h5.5L9 22l10-13h-6z"/></svg>'
)


class MolePlugin(Plugin, MoleRuntime):
    def __init__(self) -> None:
        self.jobs: dict[str, CommandSession] = {}
        self.status_lock = asyncio.Lock()
        self.command_context: Context | None = None
        self.command_search = ""
        self.api: PublicAPI | None = None
        self.session = Session(dry_run=False)
        self.task: asyncio.Task | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.active_command = ""
        self.clean_context: Context | None = None
        self.labels = LABELS.copy()
        self.colors: dict[str, str] = {}
        self.stop_requested = False
        self.unloading = False
        self.uninstall_backend = UninstallBackend()
        self.apps: list[dict] | None = None
        self.apps_task: asyncio.Task | None = None
        self.apps_error = ""
        self.apps_pending = ""
        self.apps_busy_name = ""
        self.apps_search = ""

    async def init(self, ctx: Context, init_params: PluginInitParams) -> None:
        self.api = init_params.api
        await self.api.on_unload(ctx, self.on_unload)
        await self.api.on_leave_plugin_query(ctx, self.leave_query)
        for key in LABELS:
            value = await self.api.get_translation(ctx, "preview_" + key)
            if value and value not in ("preview_" + key, "i18n:preview_" + key):
                self.labels[key] = value

    async def leave_query(self, ctx: Context) -> None:
        """Status is query-scoped; cleanup operations may continue in the background."""
        await self.set_active_command("")
        self.clean_context = None

    async def set_active_command(self, command: str) -> None:
        async with self.status_lock:
            self.active_command = command
            job = self.jobs.get("status")
            if command != "status" and job:
                task = job.task
                await job.stop()
                if task:
                    await task
            elif command == "status" and not self.unloading:
                await self.command_job("status").start()

    async def query(self, ctx: Context, query: Query) -> QueryResponse:
        self.command_context = ctx
        await self.set_active_command(query.command.lower() if query.trigger_keyword == "mo" else "")
        if query.trigger_keyword != "mo":
            return QueryResponse(results=[])
        command = query.command.lower()
        if not command:
            search = query.search.strip().casefold()
            return QueryResponse(
                results=[
                    Result(
                        id="mole-command-" + name,
                        title=f"i18n:title_{name}",
                        sub_title=f"i18n:subtitle_{name}",
                        icon=WoxImage.new_relative("image/app.png"),
                        score=100 - index,
                        actions=[
                            ResultAction(
                                name="i18n:action_enter",
                                action=self.enter_command,
                                is_default=True,
                                prevent_hide_after_action=True,
                                icon=EXECUTE_ICON,
                                context_data={"command": name},
                            )
                        ],
                    )
                    for index, name in enumerate(COMMANDS)
                    if search in name.casefold()
                ]
            )
        if command not in COMMANDS:
            return QueryResponse(results=[])
        self.command_context = ctx
        self.command_search = query.search.strip()
        if command == "clean":
            self.clean_context = ctx
        if command == "uninstall":
            return self.query_uninstall(ctx, query.search)
        if self.api:
            try:
                theme = await self.api.get_theme_colors(ctx)
                self.colors = {key: getattr(theme, key, "") for key in ("background", "text", "secondary_text", "border", "accent")}
            except Exception:
                pass  # Older hosts retain a readable default palette.
        return QueryResponse(
            results=[
                Result(
                    id="mole-" + command,
                    title=f"i18n:title_{command}",
                    icon=WoxImage.new_relative("image/app.png"),
                    preview=self.preview(command),
                    actions=self.actions(command),
                )
            ],
            layout=QueryLayout(result_preview_width_ratio=0),
        )

    async def enter_command(self, ctx: Context, action_context: ActionContext) -> None:
        command = action_context.context_data.get("command", "")
        if self.api and command in COMMANDS:
            await self.api.change_query(ctx, ChangeQueryParam(query_type=QueryType.INPUT, query_text=f"mo {command} "))

    def preview(self, command: str) -> WoxPreview:
        return render(command, self.session if command == "clean" else self.command_job(command).session, self.labels, self.colors)

    def actions(self, command: str) -> list[ResultAction]:
        def action(key, callback, default=False, data=None, hotkey=""):
            return ResultAction(
                name="i18n:" + key,
                action=callback,
                is_default=default,
                prevent_hide_after_action=True,
                icon=EXECUTE_ICON,
                context_data=data or {},
                hotkey=hotkey,
            )

        actions = []
        if command == "clean":
            running = self.task is not None and not self.task.done()
            if running:
                actions.append(action("action_stop", self.stop_clean, True))
            else:
                actions.extend(
                    [
                        action("action_clean", self.start_clean, True, {"mode": "clean"}),
                        action(
                            "action_preview",
                            self.start_clean,
                            False,
                            {"mode": "preview"},
                            "cmd+enter" if sys.platform == "darwin" else "ctrl+enter",
                        ),
                    ]
                )
        if command in ("optimize", "analyze"):
            job = self.command_job(command)
            if job.task and not job.task.done():
                actions.append(action("action_stop", self.stop_command, True, {"command": command}))
            else:
                actions.append(action("title_" + command, self.start_command, True, {"command": command, "path": self.command_search}))
                if command == "optimize":
                    actions.append(
                        action(
                            "action_preview",
                            self.start_command,
                            False,
                            {"command": command, "mode": "preview"},
                            "cmd+enter" if sys.platform == "darwin" else "ctrl+enter",
                        )
                    )
        actions.append(action("action_back", self.go_home, command == "status"))
        return actions

    def command_job(self, command: str) -> CommandSession:
        if command not in self.jobs:

            async def publish():
                if self.api and self.active_command == command and not self.unloading and self.command_context:
                    await self.api.update_result(
                        self.command_context,
                        UpdatableResult(
                            id="mole-" + command,
                            preview=script_update(self.jobs[command].session, self.labels, command),
                            actions=self.actions(command),
                        ),
                    )

            self.jobs[command] = CommandSession(command, publish)
        return self.jobs[command]

    async def start_command(self, ctx: Context, action_context: ActionContext) -> None:
        command = action_context.context_data.get("command", "")
        if command in ("optimize", "analyze", "status"):
            await self.command_job(command).start(
                action_context.context_data.get("mode") == "preview", action_context.context_data.get("path", "")
            )

    async def stop_command(self, ctx: Context, action_context: ActionContext) -> None:
        command = action_context.context_data.get("command", "")
        if command in self.jobs:
            await self.jobs[command].stop()

    def query_uninstall(self, ctx: Context, search: str) -> QueryResponse:
        if search != self.apps_search:
            self.apps_pending = ""
            self.apps_search = search
        if self.apps is None and self.apps_task is None and not self.apps_error:
            self.apps_task = asyncio.create_task(self.load_apps(ctx))
        icon = WoxImage.new_relative("image/app.png")

        def action(key, callback, data=None, default=False):
            return ResultAction(
                name="i18n:" + key,
                action=callback,
                icon=EXECUTE_ICON,
                prevent_hide_after_action=True,
                is_default=default,
                context_data=data or {},
            )

        back = action("action_back", self.go_home)
        refresh = action("action_refresh_apps", self.refresh_apps)
        if self.apps_task is not None:
            return QueryResponse(
                results=[
                    Result(
                        id="mole-uninstall-busy",
                        title="i18n:apps_uninstalling" if self.apps_busy_name else "i18n:apps_loading",
                        sub_title=self.apps_busy_name,
                        icon=icon,
                        actions=[back],
                    )
                ]
            )
        pending = next((app for app in self.apps or [] if app["key"] == self.apps_pending), None)
        if pending:
            return QueryResponse(
                results=[
                    Result(
                        id="mole-uninstall-confirm",
                        title=pending["name"],
                        sub_title="i18n:apps_confirm",
                        icon=icon,
                        tails=[ResultTail(type=ResultTailType.TEXT, text=pending["size"])],
                        actions=[
                            action("action_confirm_uninstall", self.confirm_uninstall, {"key": pending["key"]}, True),
                            action("action_cancel_uninstall", self.cancel_uninstall),
                        ],
                    )
                ]
            )
        rows = []
        if self.apps_error:
            rows.append(
                Result(
                    id="mole-uninstall-error",
                    title="i18n:apps_error",
                    sub_title=self.apps_error,
                    icon=icon,
                    score=10000,
                    actions=[refresh, back],
                )
            )
        import hashlib

        for index, app in enumerate(self.apps or []):
            if search.strip().casefold() not in app["name"].casefold():
                continue
            actions = [refresh, back]
            if app["key"].startswith(("registry:", "package:")):
                actions.insert(0, action("action_uninstall_app", self.select_uninstall, {"key": app["key"]}, True))
            rows.append(
                Result(
                    id="mole-app-" + hashlib.sha256(app["key"].encode()).hexdigest()[:24],
                    title=app["name"],
                    icon=icon,
                    score=1000 - index,
                    tails=[ResultTail(type=ResultTailType.TEXT, text=app["size"])],
                    actions=actions,
                )
            )
        if not rows:
            rows.append(Result(title="i18n:apps_empty", icon=icon, actions=[refresh, back]))
        return QueryResponse(results=rows)

    async def refresh_apps_view(self, ctx: Context) -> None:
        if self.api and self.active_command == "uninstall" and not self.unloading:
            await self.api.refresh_query(ctx, RefreshQueryParam())

    async def load_apps(self, ctx: Context, key: str = "", refresh: bool = False) -> None:
        try:
            self.apps = await self.uninstall_backend.run("uninstall" if key else "list", key, refresh)
            self.apps_error = ""
        except Exception as exc:
            self.apps_error = str(exc)
            # Never keep an actionable stale snapshot after an attempted uninstall.
            if key:
                self.apps = None
        finally:
            self.apps_pending = ""
            self.apps_busy_name = ""
            self.apps_task = None
            await self.refresh_apps_view(ctx)

    async def refresh_apps(self, ctx: Context, action_context: ActionContext) -> None:
        if self.apps_task is not None:
            return
        self.apps_error = ""
        self.apps_pending = ""
        self.apps_task = asyncio.create_task(self.load_apps(ctx, refresh=True))
        await self.refresh_apps_view(ctx)

    async def select_uninstall(self, ctx: Context, action_context: ActionContext) -> None:
        if self.apps_task is None and any(app["key"] == action_context.context_data.get("key") for app in self.apps or []):
            self.apps_pending = action_context.context_data["key"]
            await self.refresh_apps_view(ctx)

    async def cancel_uninstall(self, ctx: Context, action_context: ActionContext) -> None:
        self.apps_pending = ""
        await self.refresh_apps_view(ctx)

    async def confirm_uninstall(self, ctx: Context, action_context: ActionContext) -> None:
        key = action_context.context_data.get("key", "")
        if self.apps_task is not None or not key or key != self.apps_pending:
            return
        app = next((app for app in self.apps or [] if app["key"] == key), None)
        if not app or not key.startswith(("registry:", "package:")):
            return
        self.apps_pending = ""
        self.apps_busy_name = app["name"]
        self.apps_task = asyncio.create_task(self.load_apps(ctx, key=key))
        await self.refresh_apps_view(ctx)

    async def publish(self, ctx: Context) -> None:
        if self.api and self.active_command == "clean" and not self.unloading:
            await self.api.update_result(
                self.clean_context or ctx,
                UpdatableResult(id="mole-clean", preview=script_update(self.session, self.labels), actions=self.actions("clean")),
            )

    async def go_home(self, ctx: Context, action_context: ActionContext) -> None:
        await self.set_active_command("")
        self.apps_pending = ""
        if self.api:
            await self.api.change_query(ctx, ChangeQueryParam(query_type=QueryType.INPUT, query_text="mo "))

    async def start_clean(self, ctx: Context, action_context: ActionContext) -> None:
        if self.task and not self.task.done():
            return
        self.stop_requested = False
        self.session = Session(state="running", dry_run=action_context.context_data.get("mode") == "preview")
        self.task = asyncio.create_task(self.run_clean(ctx))
        await self.publish(ctx)

    def clean_command(self) -> list[str]:
        executable = self._find_mo_executable()
        if not executable:
            raise OSError("Mole CLI was not found. Install Mole and restart Wox.")
        args = [executable, "clean"] + (["--dry-run"] if self.session.dry_run else [])
        if sys.platform == "win32":
            # Prefer the script beside an actual launcher, for reliable UTF-8.
            script = os.path.join(os.path.dirname(os.path.realpath(executable)), "mole.ps1")
            if os.path.isfile(script):
                args[0] = script
            literal = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
            return [
                self._powershell(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
                + "try { "
                + literal
                + "; exit $LASTEXITCODE } catch { Write-Output $_; exit 1 }",
            ]
        return args

    async def run_clean(self, ctx: Context) -> None:
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(1)
                try:
                    await asyncio.wait_for(self.publish(ctx), 3)
                except Exception:
                    # A transient UI failure must not interrupt Mole.
                    pass

        ticker = asyncio.create_task(heartbeat())
        try:
            env = os.environ.copy()
            env.update(NO_COLOR="1", MOLE_NO_COLOR="1", LC_ALL="C", LANG="C")
            env["MOLE_DRY_RUN"] = "1" if self.session.dry_run else "0"
            self.process = await asyncio.create_subprocess_exec(
                *self.clean_command(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                start_new_session=sys.platform != "win32",
            )
            if self.stop_requested:
                await self.terminate()
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            buffer = ""
            last_update = 0.0
            assert self.process.stdout is not None
            while chunk := await self.process.stdout.read(4096):
                buffer += decoder.decode(chunk)
                lines = re.split(r"[\r\n]+", buffer)
                buffer = lines.pop()
                for line in lines:
                    self.session.feed(line)
                if len(buffer) > 8000:
                    self.session.feed(buffer[:8000])
                    buffer = ""
                    self.session.truncated = True
                if time.monotonic() - last_update >= 0.25:
                    await self.publish(ctx)
                    last_update = time.monotonic()
            self.session.feed(buffer + decoder.decode(b"", final=True))
            code = await self.process.wait()
            self.session.finish(code, self.stop_requested)
        except asyncio.CancelledError:
            self.session.state = "stopped"
            await self.terminate()
            raise
        except Exception as exc:
            self.session.state = "failed"
            self.session.feed(str(exc))
            if self.process and self.process.returncode is None:
                await self.terminate()
        finally:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass
            self.session.ended_at = time.monotonic()
            self.process = None
            self.task = None
            await self.publish(ctx)

    async def terminate(self) -> None:
        process = self.process
        if not process or process.returncode is not None:
            return
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            code = await killer.wait()
            if code and process.returncode is None:
                raise OSError("Could not stop the Mole process tree")
        else:
            import signal

            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), 2)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

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
        await self.terminate()
        if self.task:
            await self.task
        for job in self.jobs.values():
            await job.stop()
            if job.task:
                await job.task
        await self.uninstall_backend.close()
        if self.apps_task:
            await self.apps_task


plugin = MolePlugin()
