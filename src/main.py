import asyncio
import json
import os
import re
import shlex
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
)
from wox_plugin.models.toolbar_msg import ToolbarMsg, ToolbarMsgAction, ToolbarMsgActionContext

# Regex to extract "Space freed: X.XXGB" or similar from mole output
SPACE_FREED_RE = re.compile(r"Space freed:\s*([\d.]+)\s*(GB|MB|KB|B)", re.IGNORECASE)

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
MO_EXECUTABLE_NAME = "mo"
MO_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
RESULT_ID_CLEAN = "mole-clean"
RESULT_ID_ANALYZE = "mole-analyze"
TRIGGER_KEYWORD = "mo"
ACTION_MODE_KEY = "mode"
ACTION_PATH_KEY = "path"
ACTION_MODE_CLEAN = "clean"
ACTION_MODE_DRY_RUN = "dry-run"
ACTION_MODE_ANALYZE = "analyze"
TOOLBAR_CONTEXT_RESULT_ID = "result_id"
TOOLBAR_MSG_ID_CLEAN = "mole-clean-status"
TOOLBAR_MSG_ID_ANALYZE = "mole-analyze-status"
RESULT_SCORE = 100


class MolePlugin(Plugin):
    def __init__(self) -> None:
        self.api: PublicAPI | None = None
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._stopped: set[str] = set()

    async def init(self, ctx: Context, init_params: PluginInitParams) -> None:
        self.api = init_params.api

    async def query(self, ctx: Context, query: Query) -> list[Result]:
        if not self._should_handle_query(query):
            return []

        icon = WoxImage.new_relative("image/app.png")
        results: list[Result] = []

        if self._find_mo_executable() is None:
            results.append(
                Result(
                    id=RESULT_ID_CLEAN,
                    title="i18n:title_not_installed",
                    sub_title="i18n:subtitle_not_installed",
                    icon=icon,
                    score=RESULT_SCORE,
                )
            )
            return results

        command_query = self._parse_command_query(query)
        if command_query.command in ("", ACTION_MODE_CLEAN):
            results.append(
                Result(
                    id=RESULT_ID_CLEAN,
                    title="i18n:title_clean",
                    sub_title="i18n:subtitle_clean",
                    icon=icon,
                    score=RESULT_SCORE,
                    actions=self._build_clean_actions(running=False),
                )
            )

        if command_query.command in ("", ACTION_MODE_ANALYZE):
            results.append(
                Result(
                    id=RESULT_ID_ANALYZE,
                    title="i18n:title_analyze",
                    sub_title=self._build_analyze_subtitle(command_query.path),
                    icon=icon,
                    score=RESULT_SCORE - 1,
                    actions=self._build_analyze_actions(running=False, path=command_query.path),
                )
            )

        return results

    def _should_handle_query(self, query: Query) -> bool:
        if query.trigger_keyword == TRIGGER_KEYWORD:
            return True

        return query.trigger_keyword == "" and query.raw_query.strip() == TRIGGER_KEYWORD

    @staticmethod
    def _parse_command_query(query: Query) -> "CommandQuery":
        search = query.search.strip() if query.trigger_keyword == TRIGGER_KEYWORD else ""
        if not search:
            return CommandQuery(command="", path="")

        try:
            parts = shlex.split(search)
        except ValueError:
            parts = search.split()

        if not parts:
            return CommandQuery(command="", path="")

        command = parts[0].lower()
        if command in ("clean", "cleanup"):
            return CommandQuery(command=ACTION_MODE_CLEAN, path="")
        if command in ("analyze", "analyse"):
            return CommandQuery(command=ACTION_MODE_ANALYZE, path=" ".join(parts[1:]))
        return CommandQuery(command="unknown", path="")

    async def run_clean(self, ctx: Context, action_context: ActionContext) -> None:
        if self.api is None:
            return

        dry_run = self._get_action_mode(action_context) == ACTION_MODE_DRY_RUN
        result_id = action_context.result_id or RESULT_ID_CLEAN
        command = self._build_clean_command(dry_run=dry_run)
        if command is None:
            await self.api.update_result(
                ctx,
                UpdatableResult(
                    id=result_id,
                    title="i18n:title_not_found",
                    sub_title="i18n:subtitle_not_found",
                ),
            )
            return

        await self._stop_running_process(result_id)

        await self._update_actions(ctx, result_id, running=True, dry_run=dry_run)
        await self._show_clean_toolbar_status(ctx, result_id, "i18n:subtitle_starting", dry_run=dry_run)
        await self._stream_mole_output(ctx, result_id, command)

    async def run_analyze(self, ctx: Context, action_context: ActionContext) -> None:
        if self.api is None:
            return

        result_id = action_context.result_id or RESULT_ID_ANALYZE
        path = action_context.context_data.get(ACTION_PATH_KEY, "")
        command = self._build_analyze_command(path=path)
        if command is None:
            await self.api.update_result(
                ctx,
                UpdatableResult(
                    id=result_id,
                    title="i18n:title_not_found",
                    sub_title="i18n:subtitle_not_found",
                ),
            )
            return

        await self._stop_running_process(result_id)

        await self._update_analyze_actions(ctx, result_id, running=True, path=path)
        await self._show_analyze_toolbar_status(ctx, result_id, "i18n:subtitle_analyze_starting", path=path)
        await self._run_analyze_json(ctx, result_id, command, path)

    @staticmethod
    def _get_action_mode(action_context: ActionContext) -> str:
        return action_context.context_data.get(ACTION_MODE_KEY, ACTION_MODE_CLEAN)

    def _build_clean_command(self, dry_run: bool = False) -> list[str] | None:
        executable = self._find_mo_executable()
        if executable is None:
            return None

        command = [executable, "clean"]
        if dry_run:
            command.append("--dry-run")
        return command

    def _build_analyze_command(self, path: str = "") -> list[str] | None:
        executable = self._find_mo_executable()
        if executable is None:
            return None

        command = [executable, "analyze", "--json"]
        normalized_path = self._normalize_analyze_path(path)
        if normalized_path:
            command.append(normalized_path)
        return command

    @staticmethod
    def _normalize_analyze_path(path: str) -> str:
        return os.path.expandvars(os.path.expanduser(path.strip()))

    @staticmethod
    def _build_analyze_subtitle(path: str) -> str:
        if path:
            return f"Analyze disk usage for {path}."
        return "i18n:subtitle_analyze"

    @staticmethod
    def _build_mo_env() -> dict[str, str]:
        env = os.environ.copy()
        path_entries = [*MO_BIN_DIRS, *env.get("PATH", "").split(os.pathsep)]
        env.update(
            {
                "TERM": env.get("TERM", "xterm-256color"),
                "MOLE_NO_COLOR": "1",
                "NO_COLOR": "1",
                "CLICOLOR": "0",
                "LC_ALL": "C",
                "LANG": "C",
                "PATH": os.pathsep.join(dict.fromkeys(entry for entry in path_entries if entry)),
            }
        )
        return env

    async def _stream_mole_output(self, ctx: Context, result_id: str, command: list[str]) -> None:
        assert self.api is not None
        dry_run = "--dry-run" in command
        env = self._build_mo_env()

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except FileNotFoundError:
            await self._clear_clean_toolbar_status(ctx)
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
                    await self._show_clean_toolbar_status(ctx, result_id, line, dry_run=dry_run)

            # Handle any remaining buffered text
            for line in self._clean_output_lines([buffer]):
                last_message = line
                match = SPACE_FREED_RE.search(line)
                if match:
                    space_freed = f"{match.group(1)}{match.group(2)}"
                await self._show_clean_toolbar_status(ctx, result_id, line, dry_run=dry_run)

        return_code = await process.wait()
        self._running.pop(result_id, None)
        await self._clear_clean_toolbar_status(ctx)

        if result_id in self._stopped:
            self._stopped.discard(result_id)
            final_sub_title = "i18n:subtitle_stopped"
            final_title = "i18n:title_stopped"
        elif return_code == 0:
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
                actions=self._build_clean_actions(dry_run=dry_run, running=False),
            ),
        )

    async def _run_analyze_json(self, ctx: Context, result_id: str, command: list[str], path: str) -> None:
        assert self.api is not None

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_mo_env(),
            )
        except FileNotFoundError:
            await self._clear_analyze_toolbar_status(ctx)
            await self.api.update_result(
                ctx,
                UpdatableResult(
                    id=result_id,
                    title="i18n:title_analyze_failed",
                    sub_title="i18n:subtitle_cmd_not_found",
                    actions=self._build_analyze_actions(running=False, path=path),
                ),
            )
            return

        self._running[result_id] = process
        stdout, stderr = await process.communicate()
        self._running.pop(result_id, None)
        await self._clear_analyze_toolbar_status(ctx)

        if result_id in self._stopped:
            self._stopped.discard(result_id)
            final_title = "i18n:title_analyze_stopped"
            final_sub_title = "i18n:subtitle_analyze_stopped"
        elif process.returncode == 0:
            final_title = "i18n:title_analyze_completed"
            final_sub_title = self._format_analyze_summary(stdout)
        else:
            final_title = "i18n:title_analyze_failed"
            final_sub_title = self._clean_output_text(stderr.decode(errors="ignore")) or "i18n:subtitle_analyze_failed"

        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                title=final_title,
                sub_title=final_sub_title,
                actions=self._build_analyze_actions(running=False, path=path),
            ),
        )

    def _format_analyze_summary(self, stdout: bytes) -> str:
        try:
            payload = json.loads(stdout.decode(errors="ignore"))
        except json.JSONDecodeError:
            return "i18n:subtitle_analyze_finished"

        path = str(payload.get("path") or "")
        total_size = self._format_bytes(payload.get("total_size"))
        total_files = payload.get("total_files")
        entries = payload.get("entries")
        top = ""
        if isinstance(entries, list):
            top_entries = []
            for entry in entries[:3]:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "")
                if not name:
                    continue
                top_entries.append(f"{name} {self._format_bytes(entry.get('size'))}")
            top = ", ".join(top_entries)

        summary = f"Total {total_size}"
        if isinstance(total_files, int):
            summary = f"{summary}, {total_files} files"
        if top:
            summary = f"{summary}. Top: {top}"
        if path:
            summary = f"{path}: {summary}"
        return summary

    @staticmethod
    def _format_bytes(value: object) -> str:
        if not isinstance(value, int | float):
            return "0B"

        size = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                if unit == "B":
                    return f"{int(size)}B"
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    async def _show_clean_toolbar_status(self, ctx: Context, result_id: str, message: str, dry_run: bool = False) -> None:
        assert self.api is not None
        await self.api.show_toolbar_msg(
            ctx,
            self._build_toolbar_msg(
                id=TOOLBAR_MSG_ID_CLEAN,
                title=message,
                icon=WoxImage.new_relative("image/app.png"),
                indeterminate=True,
                actions=[
                    ToolbarMsgAction(
                        name="i18n:action_stop",
                        action=self.stop_clean_from_toolbar,
                        icon=WoxImage.new_emoji("🛑"),
                        prevent_hide_after_action=True,
                        context_data={
                            TOOLBAR_CONTEXT_RESULT_ID: result_id,
                            ACTION_MODE_KEY: ACTION_MODE_DRY_RUN if dry_run else ACTION_MODE_CLEAN,
                        },
                    ),
                ],
            ),
        )

    async def _clear_clean_toolbar_status(self, ctx: Context) -> None:
        assert self.api is not None
        await self.api.clear_toolbar_msg(ctx, TOOLBAR_MSG_ID_CLEAN)

    async def _show_analyze_toolbar_status(self, ctx: Context, result_id: str, message: str, path: str = "") -> None:
        assert self.api is not None
        await self.api.show_toolbar_msg(
            ctx,
            self._build_toolbar_msg(
                id=TOOLBAR_MSG_ID_ANALYZE,
                title=message,
                icon=WoxImage.new_relative("image/app.png"),
                indeterminate=True,
                actions=[
                    ToolbarMsgAction(
                        name="i18n:action_stop",
                        action=self.stop_analyze_from_toolbar,
                        icon=WoxImage.new_emoji("🛑"),
                        prevent_hide_after_action=True,
                        context_data={
                            TOOLBAR_CONTEXT_RESULT_ID: result_id,
                            ACTION_MODE_KEY: ACTION_MODE_ANALYZE,
                            ACTION_PATH_KEY: path,
                        },
                    ),
                ],
            ),
        )

    @staticmethod
    def _build_toolbar_msg(
        id: str,
        title: str,
        icon: WoxImage,
        indeterminate: bool,
        actions: list[ToolbarMsgAction],
    ) -> ToolbarMsg:
        msg = ToolbarMsg(
            id=id,
            title=title,
            icon=icon,
            indeterminate=indeterminate,
            actions=actions,
        )
        # Newer SDKs serialize scope when present. Older Mo hosts ignore this
        # dynamic attribute and still accept the toolbar msg payload.
        msg.scope = "global"  # type: ignore[assignment]
        return msg

    async def _clear_analyze_toolbar_status(self, ctx: Context) -> None:
        assert self.api is not None
        await self.api.clear_toolbar_msg(ctx, TOOLBAR_MSG_ID_ANALYZE)

    async def _update_actions(self, ctx: Context, result_id: str, running: bool, dry_run: bool) -> None:
        assert self.api is not None
        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                actions=self._build_clean_actions(dry_run=dry_run, running=running),
            ),
        )

    async def _update_analyze_actions(self, ctx: Context, result_id: str, running: bool, path: str) -> None:
        assert self.api is not None
        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                actions=self._build_analyze_actions(running=running, path=path),
            ),
        )

    async def stop_clean(self, ctx: Context, action_context: ActionContext) -> None:
        if self.api is None:
            return

        result_id = action_context.result_id or RESULT_ID_CLEAN
        dry_run = self._get_action_mode(action_context) == ACTION_MODE_DRY_RUN
        stopped = await self._stop_running_process(result_id)
        if stopped:
            self._stopped.add(result_id)
        await self._clear_clean_toolbar_status(ctx)
        message = "i18n:subtitle_stopped" if stopped else "i18n:subtitle_nothing_to_stop"

        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                sub_title=message,
                actions=self._build_clean_actions(dry_run=dry_run, running=False),
            ),
        )
        await self._update_actions(ctx, result_id, running=False, dry_run=dry_run)

    async def stop_clean_from_toolbar(self, ctx: Context, action_context: ToolbarMsgActionContext) -> None:
        if self.api is None:
            return

        result_id = action_context.context_data.get(TOOLBAR_CONTEXT_RESULT_ID, RESULT_ID_CLEAN)
        dry_run = action_context.context_data.get(ACTION_MODE_KEY) == ACTION_MODE_DRY_RUN
        stopped = await self._stop_running_process(result_id)
        if stopped:
            self._stopped.add(result_id)
        await self._clear_clean_toolbar_status(ctx)

        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                sub_title="i18n:subtitle_stopped" if stopped else "i18n:subtitle_nothing_to_stop",
                actions=self._build_clean_actions(dry_run=dry_run, running=False),
            ),
        )

    async def stop_analyze(self, ctx: Context, action_context: ActionContext) -> None:
        if self.api is None:
            return

        result_id = action_context.result_id or RESULT_ID_ANALYZE
        path = action_context.context_data.get(ACTION_PATH_KEY, "")
        stopped = await self._stop_running_process(result_id)
        if stopped:
            self._stopped.add(result_id)
        await self._clear_analyze_toolbar_status(ctx)

        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                sub_title="i18n:subtitle_analyze_stopped" if stopped else "i18n:subtitle_nothing_to_stop",
                actions=self._build_analyze_actions(running=False, path=path),
            ),
        )

    async def stop_analyze_from_toolbar(self, ctx: Context, action_context: ToolbarMsgActionContext) -> None:
        if self.api is None:
            return

        result_id = action_context.context_data.get(TOOLBAR_CONTEXT_RESULT_ID, RESULT_ID_ANALYZE)
        path = action_context.context_data.get(ACTION_PATH_KEY, "")
        stopped = await self._stop_running_process(result_id)
        if stopped:
            self._stopped.add(result_id)
        await self._clear_analyze_toolbar_status(ctx)

        await self.api.update_result(
            ctx,
            UpdatableResult(
                id=result_id,
                sub_title="i18n:subtitle_analyze_stopped" if stopped else "i18n:subtitle_nothing_to_stop",
                actions=self._build_analyze_actions(running=False, path=path),
            ),
        )

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

    def _clean_output_text(self, text: str) -> str:
        lines = self._clean_output_lines(text.splitlines())
        return lines[-1] if lines else ""

    @staticmethod
    def _find_mo_executable() -> str | None:
        path = shutil.which(MO_EXECUTABLE_NAME)
        if path is not None:
            return path

        for directory in MO_BIN_DIRS:
            path = os.path.join(directory, MO_EXECUTABLE_NAME)
            if os.path.exists(path):
                return path
        return None

    def _build_clean_actions(self, running: bool, dry_run: bool = False) -> list[ResultAction]:
        run_icon = WoxImage.new_emoji("▶️")
        preview_icon = WoxImage.new_emoji("👀")
        stop_icon = WoxImage.new_emoji("🛑")

        if running:
            return [
                ResultAction(
                    name="i18n:action_stop",
                    action=self.stop_clean,
                    context_data={ACTION_MODE_KEY: ACTION_MODE_DRY_RUN if dry_run else ACTION_MODE_CLEAN},
                    icon=stop_icon,
                ),
            ]

        return [
            ResultAction(
                name="i18n:action_run",
                prevent_hide_after_action=True,
                action=self.run_clean,
                context_data={ACTION_MODE_KEY: ACTION_MODE_CLEAN},
                icon=run_icon,
            ),
            ResultAction(
                name="i18n:action_preview",
                prevent_hide_after_action=True,
                action=self.run_clean,
                context_data={ACTION_MODE_KEY: ACTION_MODE_DRY_RUN},
                icon=preview_icon,
            ),
        ]

    def _build_analyze_actions(self, running: bool, path: str = "") -> list[ResultAction]:
        analyze_icon = WoxImage.new_emoji("🔎")
        stop_icon = WoxImage.new_emoji("🛑")

        if running:
            return [
                ResultAction(
                    name="i18n:action_stop",
                    action=self.stop_analyze,
                    context_data={ACTION_MODE_KEY: ACTION_MODE_ANALYZE, ACTION_PATH_KEY: path},
                    icon=stop_icon,
                ),
            ]

        return [
            ResultAction(
                name="i18n:action_analyze",
                prevent_hide_after_action=True,
                action=self.run_analyze,
                context_data={ACTION_MODE_KEY: ACTION_MODE_ANALYZE, ACTION_PATH_KEY: path},
                icon=analyze_icon,
            ),
        ]


class CommandQuery:
    def __init__(self, command: str, path: str) -> None:
        self.command = command
        self.path = path


plugin = MolePlugin()
