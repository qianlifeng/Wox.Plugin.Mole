import asyncio
import os
import re
import shutil
from typing import Iterable

from wox_plugin import (
    ActionContext,
    Context,
    Plugin,
    PluginInitParams,
    PublicAPI,
    Query,
    Result,
    ResultAction,
    UpdatableResult,
    WoxImage,
    WoxImageType,
)

# Regex to extract "Space freed: X.XXGB" or similar from mole output
SPACE_FREED_RE = re.compile(r"Space freed:\s*([\d.]+)\s*(GB|MB|KB|B)", re.IGNORECASE)

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


class MolePlugin(Plugin):
    def __init__(self) -> None:
        self.api: PublicAPI | None = None
        self.ctx: Context | None = None
        self._running: dict[str, asyncio.subprocess.Process] = {}

    async def init(self, ctx: Context, init_params: PluginInitParams) -> None:
        self.api = init_params.api
        self.ctx = ctx

    async def query(self, ctx: Context, query: Query) -> list[Result]:
        icon = WoxImage.new_relative("image/app.png")
        results: list[Result] = []

        if not self._mole_available():
            results.append(
                Result(
                    title="i18n:title_not_installed",
                    sub_title="i18n:subtitle_not_installed",
                    icon=icon,
                )
            )
            return results

        results.append(
            Result(
                title="i18n:title_clean",
                sub_title="i18n:subtitle_clean",
                icon=icon,
                actions=self._build_actions(running=False),
            )
        )

        return results

    async def run_clean(self, action_context: ActionContext) -> None:
        if self.api is None:
            return

        ctx = self.ctx or Context.new()
        dry_run = action_context.context_data == "dry-run"
        result_id = action_context.result_id
        command = ["mole", "clean"]
        if dry_run:
            command.append("--dry-run")

        await self._stop_running_process(result_id)

        if not self._mole_available():
            await self.api.update_result(
                ctx,
                UpdatableResult(
                    id=result_id,
                    title="i18n:title_not_found",
                    sub_title="i18n:subtitle_not_found",
                ),
            )
            return

        await self._update_actions(ctx, result_id, running=True, dry_run=dry_run)
        await self._update_running_status(ctx, result_id, "i18n:subtitle_starting", running=True, dry_run=dry_run)
        await self._stream_mole_output(ctx, result_id, command)

    async def _stream_mole_output(self, ctx: Context, result_id: str, command: list[str]) -> None:
        assert self.api is not None
        dry_run = "--dry-run" in command

        env = os.environ.copy()
        env.update(
            {
                "TERM": env.get("TERM", "xterm-256color"),
                "MOLE_NO_COLOR": "1",
                "NO_COLOR": "1",
                "CLICOLOR": "0",
                "LC_ALL": "C",
                "LANG": "C",
            }
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except FileNotFoundError:
            await self.api.update_result(
                ctx,
                UpdatableResult(
                    id=result_id,
                    title="i18n:title_failed",
                    sub_title="i18n:subtitle_cmd_not_found",
                ),
            )
            return

        buffer = ""
        last_message = ""
        space_freed = ""
        visible = True
        self._running[result_id] = process

        if process.stdout is not None:
            while True:
                chunk = await process.stdout.read(1024)
                if not chunk:
                    break

                buffer += chunk.decode(errors="ignore")
                lines = re.split(r"[\r\n]+", buffer)
                buffer = lines.pop()

                for line in self._clean_output_lines(lines):
                    last_message = line
                    # Try to capture space freed info
                    match = SPACE_FREED_RE.search(line)
                    if match:
                        space_freed = f"{match.group(1)}{match.group(2)}"
                    if visible:
                        visible = await self._update_running_status(ctx, result_id, f"> {line}", running=True, dry_run=dry_run)

            # Handle any remaining buffered text
            for line in self._clean_output_lines([buffer]):
                last_message = line
                match = SPACE_FREED_RE.search(line)
                if match:
                    space_freed = f"{match.group(1)}{match.group(2)}"
                if visible:
                    visible = await self._update_running_status(ctx, result_id, f"> {line}", running=True, dry_run=dry_run)

        return_code = await process.wait()
        self._running.pop(result_id, None)

        if return_code == 0:
            if space_freed:
                final_sub_title = await self.api.get_translation(ctx, "i18n:subtitle_done")
                final_sub_title = final_sub_title.replace("{0}", space_freed)
            else:
                final_sub_title = "i18n:subtitle_finished"
            final_title = "i18n:title_completed"
        else:
            final_sub_title = last_message or "i18n:subtitle_failed"
            final_title = "i18n:title_failed"

        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                title=final_title,
                sub_title=final_sub_title,
                actions=self._build_actions(dry_run=dry_run, running=False),
            ),
        )

    async def _update_running_status(
        self,
        ctx: Context,
        result_id: str,
        message: str,
        running: bool = False,
        dry_run: bool = False,
    ) -> bool:
        assert self.api is not None
        return await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                title="i18n:title_running",
                sub_title=message,
                actions=self._build_actions(dry_run=dry_run, running=running),
            ),
        )

    async def _update_actions(self, ctx: Context, result_id: str, running: bool, dry_run: bool) -> None:
        assert self.api is not None
        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                actions=self._build_actions(dry_run=dry_run, running=running),
            ),
        )

    async def stop_clean(self, action_context: ActionContext) -> None:
        if self.api is None:
            return

        ctx = self.ctx or Context.new()
        result_id = action_context.result_id or "mole-clean"
        stopped = await self._stop_running_process(result_id)
        message = "i18n:subtitle_stopped" if stopped else "i18n:subtitle_nothing_to_stop"

        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                sub_title=message,
                actions=self._build_actions(dry_run=result_id.endswith("dry-run"), running=False),
            ),
        )
        await self._update_actions(ctx, result_id, running=False, dry_run=result_id.endswith("dry-run"))

    async def _stop_running_process(self, result_id: str) -> bool:
        process = self._running.pop(result_id, None)
        if process is None:
            return False
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        return True

    def _clean_output_lines(self, lines: Iterable[str]) -> list[str]:
        cleaned: list[str] = []
        for raw_line in lines:
            text = ANSI_ESCAPE_RE.sub("", raw_line)
            text = CONTROL_CHAR_RE.sub("", text)
            text = text.replace("\t", " ").strip()

            if not text:
                continue

            # Drop non-ASCII glyphs (spinners/icons) to keep the UI tidy
            text = text.encode("ascii", "ignore").decode().strip()
            if text:
                cleaned.append(text)

        return cleaned

    @staticmethod
    def _mole_available() -> bool:
        return shutil.which("mole") is not None

    def _build_actions(self, running: bool, dry_run: bool = False) -> list[ResultAction]:
        run_icon = WoxImage(image_type=WoxImageType.EMOJI, image_data="▶️")
        preview_icon = WoxImage(image_type=WoxImageType.EMOJI, image_data="👀")
        stop_icon = WoxImage(image_type=WoxImageType.EMOJI, image_data="🛑")

        if running:
            return [
                ResultAction(
                    name="i18n:action_stop",
                    action=self.stop_clean,
                    context_data="dry-run" if dry_run else "clean",
                    icon=stop_icon,
                ),
            ]

        return [
            ResultAction(
                name="i18n:action_run",
                prevent_hide_after_action=True,
                action=self.run_clean,
                context_data="clean",
                icon=run_icon,
            ),
            ResultAction(
                name="i18n:action_preview",
                prevent_hide_after_action=True,
                action=self.run_clean,
                context_data="dry-run",
                icon=preview_icon,
            ),
        ]


plugin = MolePlugin()
