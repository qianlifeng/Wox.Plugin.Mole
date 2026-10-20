import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from wox_plugin import ActionContext, Context, Query, QueryEnv, QueryType, Selection, WoxPreviewType
from src.main import COMMANDS, MolePlugin
from src.dashboard import LABELS, Session, render, snapshot, script_update


def html_data(value):
    return json.loads(value)["html"]


def query(command="", search=""):
    return Query(
        id="q",
        type=QueryType.INPUT,
        raw_query=f"mo {command} {search}".strip(),
        selection=Selection(),
        env=QueryEnv(),
        trigger_keyword="mo",
        command=command,
        search=search,
    )


class TestCommands(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_declares_native_commands_in_order(self):
        metadata = json.loads(Path("plugin.json").read_text(encoding="utf-8"))
        self.assertEqual([c["Command"] for c in metadata["Commands"]], list(COMMANDS))
        response = await MolePlugin().query(Context.new(), query())
        self.assertEqual([row.id for row in response.results], ["mole-command-" + name for name in COMMANDS])
        self.assertIsNone(response.layout.result_preview_width_ratio)

    async def test_all_commands_enter_full_preview_without_execution(self):
        plugin = MolePlugin()
        with patch("src.main.asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            for name in (name for name in COMMANDS if name not in ("uninstall", "status")):
                response = await plugin.query(Context.new(), query(name))
                self.assertEqual(response.layout.result_preview_width_ratio, 0)
                self.assertEqual(len(response.results), 1)
                self.assertEqual(response.results[0].preview.preview_type, WoxPreviewType.WEBVIEW)
                self.assertEqual(response.results[0].id, "mole-" + name)
        spawn.assert_not_called()

    async def test_clean_default_enter_cleans_and_ctrl_enter_previews(self):
        row = (await MolePlugin().query(Context.new(), query("clean"))).results[0]
        self.assertTrue(row.actions[0].is_default)
        self.assertTrue(row.actions[0].prevent_hide_after_action)
        self.assertEqual(row.actions[0].context_data, {"mode": "clean"})
        self.assertEqual(row.actions[1].context_data, {"mode": "preview"})
        self.assertEqual(row.actions[1].hotkey, "cmd+enter" if sys.platform == "darwin" else "ctrl+enter")
        self.assertFalse(row.actions[1].is_default)
        self.assertNotIn("i18n:action_logs", [a.name for a in row.actions])

    async def test_command_search_filters_without_entering_or_executing(self):
        plugin = MolePlugin()
        for search, expected in [("  ", list(COMMANDS)), ("CL", ["clean"]), ("all", ["uninstall"]), ("clean", ["clean"]), ("unknown", [])]:
            with self.subTest(search=search):
                response = await plugin.query(Context.new(), query(search=search))
                self.assertEqual([row.actions[0].context_data["command"] for row in response.results], expected)
                self.assertIsNone(response.layout.result_preview_width_ratio)
                self.assertEqual(plugin.active_command, "")
                self.assertIsNone(plugin.task)

    async def test_menu_enter_only_navigates_to_native_command(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        row = (await plugin.query(Context.new(), query(search="op"))).results[0]
        self.assertTrue(row.actions[0].is_default)
        self.assertTrue(row.actions[0].prevent_hide_after_action)
        await row.actions[0].action(Context.new(), ActionContext(context_data=row.actions[0].context_data))
        self.assertEqual(plugin.api.change_query.call_args.args[1].query_text, "mo optimize ")
        self.assertIsNone(plugin.task)

    async def test_leaving_clean_prevents_stale_updates(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        plugin.active_command = "status"
        await plugin.publish(Context.new())
        plugin.api.update_result.assert_not_called()
        plugin.active_command = "clean"
        await plugin.go_home(Context.new(), ActionContext())
        await plugin.publish(Context.new())
        self.assertEqual(plugin.api.change_query.call_args.args[1].query_text, "mo ")
        plugin.api.update_result.assert_not_called()

    async def test_background_publish_uses_latest_query_context(self):
        plugin = MolePlugin()
        old, current = Context.new(), Context.new()
        await plugin.query(old, query("clean"))
        await plugin.query(current, query("clean"))
        plugin.api = AsyncMock()
        await plugin.publish(old)
        self.assertIs(plugin.api.update_result.call_args.args[0], current)
        data = json.loads(plugin.api.update_result.call_args.args[1].preview.preview_data)
        self.assertEqual(data["updateMode"], "script")
        self.assertNotIn("html", data)

    async def test_leave_and_reenter_restores_snapshot_without_restarting(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        plugin.api.get_theme_colors.side_effect = RuntimeError("theme unavailable in fixture")
        plugin.session = Session(state="running")
        plugin.session.feed("➤ Developer tools")
        plugin.session.feed("→ Current cache (1 items, 2MB dry)")
        original = plugin.session
        plugin.active_command = "clean"
        await plugin.leave_query(Context.new())
        await plugin.publish(Context.new())
        plugin.api.update_result.assert_not_called()
        with patch.object(plugin, "start_clean", new=AsyncMock()) as start:
            response = await plugin.query(Context.new(), query("clean"))
        start.assert_not_called()
        self.assertIs(plugin.session, original)
        data = json.loads(response.results[0].preview.preview_data)
        self.assertEqual(set(data), {"html"})
        self.assertIn("Current cache", data["html"])
        self.assertIn("Scanning", data["html"])

    async def test_repeated_start_is_ignored(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        gate = asyncio.Event()

        async def worker(ctx):
            await gate.wait()

        with patch.object(plugin, "run_clean", side_effect=worker) as run:
            await plugin.start_clean(Context.new(), ActionContext(context_data={"mode": "preview"}))
            task = plugin.task
            await plugin.start_clean(Context.new(), ActionContext(context_data={"mode": "clean"}))
            self.assertIs(task, plugin.task)
            self.assertTrue(plugin.session.dry_run)
            gate.set()
            await task
        self.assertEqual(run.call_count, 1)

    async def test_launch_failure_sets_failed_and_restores_actions(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        plugin.active_command = "clean"
        with patch.object(plugin, "_find_mo_executable", return_value=None):
            await plugin.run_clean(Context.new())
        self.assertEqual(plugin.session.state, "failed")
        self.assertIsNone(plugin.task)
        self.assertEqual(plugin.actions("clean")[0].context_data["mode"], "clean")

    async def test_cancelled_worker_stops_process_before_clearing_handle(self):
        plugin = MolePlugin()
        process = AsyncMock()
        entered = asyncio.Event()

        async def read(size):
            entered.set()
            await asyncio.Event().wait()

        process.stdout.read.side_effect = read
        with (
            patch.object(plugin, "clean_command", return_value=["mole"]),
            patch("src.main.asyncio.create_subprocess_exec", return_value=process),
            patch.object(plugin, "terminate", new=AsyncMock()) as terminate,
        ):
            task = asyncio.create_task(plugin.run_clean(Context.new()))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            terminate.assert_awaited_once()
        self.assertIsNone(plugin.process)
        self.assertEqual(plugin.session.state, "stopped")

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell required")
    async def test_real_hidden_powershell_stream_and_exit_status(self):
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory(prefix="mole ' space ") as folder:
                script = Path(folder) / "mole.ps1"
                script.write_text(
                    "param([string]$Command,[Parameter(ValueFromRemainingArguments)][string[]]$Rest)\n"
                    'if ($Command -ne "clean" -or $Rest[0] -ne "--dry-run") { exit 9 }\n'
                    'Write-Output "➤ 中文类别"\nWrite-Output "Potential space: 12MB"\n'
                    f"exit {code}\n",
                    encoding="utf-8-sig",
                )
                plugin = MolePlugin()
                plugin.session.dry_run = True
                with patch.object(plugin, "_find_mo_executable", return_value=str(script)):
                    await asyncio.wait_for(plugin.run_clean(Context.new()), 10)
                self.assertEqual(plugin.session.state, "completed" if code == 0 else "failed")
                self.assertEqual(plugin.session.stage, "中文类别")
                self.assertEqual(plugin.session.estimated, "12MB")
                self.assertEqual(plugin.session.freed, "")


class TestPresentation(unittest.TestCase):
    def test_terminal_summary_is_not_part_of_last_category(self):
        for dry_run, heading, metric in [
            (False, "Cleanup complete", "Tracked cleanup: 93KB | Items cleaned: 3"),
            (True, "Dry run complete - no changes made", "Potential space: 93KB | Items: 3"),
        ]:
            state = Session(dry_run=dry_run)
            for line in ["➤ Applications", "✓ Nothing to tidy", "=" * 70, heading, metric, "Free space: 115.16GB", "=" * 70]:
                state.feed(line)
            self.assertEqual(list(state.sections[-1][1]), ["✓ Nothing to tidy"])
            self.assertEqual(state.estimated if dry_run else state.tracked, "93KB")
            self.assertIn(metric, state.logs)
            state.finish(0)
            page = html_data(render("clean", state, LABELS, {}).preview_data)
            self.assertNotIn("Free space: 115.16GB", page)
            self.assertNotIn("=" * 70, page)
            self.assertIn("93KB", page)
            state.feed("☻ Cleanup error: access denied")
            state.finish(0)
            self.assertEqual(state.state, "failed")
            self.assertIn("access denied", state.sections[-1][1][-1])

    def test_compact_preview_and_silent_process_status(self):
        state = Session(state="running", started_at=10, last_output_at=20)
        for index in range(20):
            state.feed(f"➤ Section {index}")
            state.feed("→ cache")
        state.last_output_at = 20
        with patch("src.dashboard.time.monotonic", return_value=80):
            page = html_data(render("clean", state, LABELS, {}).preview_data)
        self.assertEqual(page.count('id="category-'), 20)
        self.assertIn("60s", page)
        self.assertIn("1:10", page)
        self.assertIn("overflow:hidden", page)
        self.assertNotIn("View full output", page)
        self.assertIn("overflow-y:auto", page)

    def test_windows_cleanup_summary_and_caught_error(self):
        state = Session(dry_run=False)
        state.feed("➤ Browser caches")
        state.feed("✓ Edge Cache (2 items, 104.2MB)")
        state.feed("Tracked cleanup: 104.2MB | Items cleaned: 2")
        state.finish(0)
        self.assertEqual(state.state, "completed")
        self.assertEqual(state.tracked, "104.2MB")
        self.assertEqual(state.freed, "")
        page = html_data(render("clean", state, LABELS, {}).preview_data)
        self.assertIn("Edge Cache", page)
        self.assertIn("Tracked cleanup", page)
        state.feed("☻ Cleanup error: Access denied")
        state.finish(0)
        self.assertEqual(state.state, "failed")

    def test_preview_completion_and_nonzero_exit_are_unambiguous(self):
        state = Session()
        state.feed("➤ Applications")
        state.feed("→ Cache (1 items, 1MB dry)")
        state.feed("Tracked cleanup: 2GB")
        state.finish(0)
        self.assertEqual(state.tracked, "")
        page = html_data(render("clean", state, LABELS, {}).preview_data)
        self.assertIn("No files were removed", page)
        state.finish(7)
        self.assertEqual(state.state, "failed")
        self.assertIn("code 7", state.message)
        state.finish(1, stopped=True)
        self.assertEqual(state.state, "stopped")

    def test_preview_never_claims_actual_freed_space(self):
        state = Session(dry_run=True)
        state.feed("Space freed: 99GB")
        state.feed("Potential space: 12MB")
        self.assertEqual(state.freed, "")
        self.assertEqual(state.estimated, "12MB")
        state.dry_run = False
        state.feed("Space freed: 2GB")
        self.assertEqual(state.freed, "2GB")

    def test_svg_page_escapes_output_and_uses_theme(self):
        state = Session(state="running")
        state.feed("<script>alert(1)</script>")
        data = html_data(render("clean", state, LABELS, {"background": "#eeeeee"}).preview_data)
        self.assertNotIn("<script>alert(1)</script>", data)
        self.assertIn("&lt;script&gt;", data)
        self.assertIn("#eeeeee", data)
        self.assertIn("<svg", data)
        self.assertNotIn('aria-valuenow="100"', data)
        self.assertNotIn('id="progress"', data)

    def test_completed_design_and_header_metadata(self):
        state = Session(state="completed", estimated="198.5MB")
        page = html_data(render("clean", state, LABELS, {"accent": "#111111"}).preview_data)
        self.assertNotIn("<header>", page)
        self.assertIn("Scan complete", page)
        self.assertIn("198.5MB", page)
        self.assertIn("#62d9ac", page)
        self.assertNotIn('id="progress"', page)
        self.assertIn('id="mode">Preview only</span>', page)
        self.assertIn('id="elapsed"', page)
        self.assertNotIn("<footer", page)

    def test_animation_is_css_not_image_frames(self):
        for status in ("ready", "running", "completed", "failed", "stopped"):
            state = Session(state=status)
            state.feed("➤ Cache & <tools>")
            result = render("clean", state, LABELS, {})
            self.assertEqual(result.preview_type, WoxPreviewType.WEBVIEW)
            page = html_data(result.preview_data)
            self.assertIn("@keyframes spin", page)
            self.assertNotIn("<img", page)
            self.assertIn("Cache &amp; &lt;tools&gt;", page)

    def test_all_items_and_scroll_tracking(self):
        state = Session(state="running")
        state.feed("➤ Browser caches")
        state.feed("✓ Chrome (1 items, 1MB)")
        state.feed("✓ Edge (2 items, 2MB)")
        page = html_data(render("clean", state, LABELS, {}).preview_data)
        self.assertIn("Chrome", page)
        self.assertIn("Edge", page)
        self.assertIn('data-revision="3"', page)
        self.assertIn("if (following) area.scrollTop = area.scrollHeight", page)
        self.assertNotIn("sessionStorage", page)
        self.assertNotIn("window.name", page)
        self.assertNotIn("@keyframes sweep", page)

    @unittest.skipUnless(shutil.which("node"), "Node is required for the scroll script regression test")
    def test_scroll_pauses_on_user_scroll_and_resumes_only_at_bottom(self):
        subprocess.run([shutil.which("node"), "tests/cleanup_dom.cjs"], check=True, capture_output=True, text=True, timeout=10)

    def test_script_snapshot_is_json_safe_and_never_replaces_html(self):
        state = Session(state="running")
        raw = '</script><img onerror="boom()"> 中文 \u2028 " \\ '
        state.feed("➤ Apps")
        state.feed(raw)
        update = json.loads(script_update(state, LABELS).preview_data)
        self.assertEqual(set(update), {"updateMode", "script"})
        self.assertEqual(update["updateMode"], "script")
        data = json.loads(update["script"][len("window.updateCleanup(") : -2])
        self.assertEqual(data["sections"][0]["items"][0]["text"], raw.strip())
        page = html_data(render("clean", state, LABELS, {}).preview_data)
        self.assertNotIn(raw, page)
        self.assertIn("window.updateCleanup(", page)

    def test_bounded_rows_keep_stable_ids(self):
        state = Session()
        state.feed("➤ Cache")
        for index in range(100):
            state.feed(f"row {index}")
        before = snapshot("clean", state, LABELS)["sections"][0]["items"]
        state.feed("row 100")
        after = snapshot("clean", state, LABELS)["sections"][0]["items"]
        self.assertEqual(before[1:], after[:-1])

    def test_log_memory_is_bounded(self):
        state = Session()
        for _ in range(1600):
            state.feed("x" * 2100)
        self.assertEqual(len(state.logs), 1500)
        self.assertEqual(len(state.logs[-1]), 2000)
        self.assertTrue(state.truncated)

    def test_standard_script_install_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "Programs" / "mole-cli" / "mole.ps1"
            script.parent.mkdir(parents=True)
            script.touch()
            with (
                patch("src.runtime.sys.platform", "win32"),
                patch.dict(os.environ, {"LOCALAPPDATA": folder}),
                patch("src.runtime.shutil.which", return_value=None),
            ):
                self.assertEqual(MolePlugin._find_mo_executable(), str(script))
