import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from wox_plugin import ActionContext, Context, Query, QueryEnv, QueryType, Selection
from src.main import MolePlugin
from src.dashboard import LABELS, Session, summary
from src.scene import POINTS, Facing


def query():
    return Query(id="q", type=QueryType.INPUT, raw_query="mo ", selection=Selection(), env=QueryEnv(), trigger_keyword="mo")


class TestCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_query_opens_full_native_scene_without_execution(self):
        plugin = MolePlugin()
        with patch("src.runner.asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            response = await plugin.query(Context.new(), query())
        spawn.assert_not_called()
        self.assertEqual(response.layout.result_preview_width_ratio, 0)
        self.assertEqual(len(response.results), 1)
        row = response.results[0]
        self.assertEqual(row.id, "mole-clean")
        self.assertEqual(row.preview.preview_type, "woxview")
        self.assertEqual(json.loads(row.preview.preview_data)["version"], 1)
        self.assertEqual(plugin.scene.location, "porch")
        self.assertEqual(plugin.scene.mode, "idle")
        self.assertEqual(len(row.actions), 1)
        self.assertTrue(row.actions[0].is_default)
        self.assertTrue(row.actions[0].prevent_hide_after_action)
        self.assertEqual(row.actions[0].action, plugin.start_clean)
        self.assertFalse(json.loads(Path("plugin.json").read_text(encoding="utf-8")).get("Commands"))

    async def test_enter_starts_once_and_changes_default_to_stop(self):
        plugin = MolePlugin()
        row = (await plugin.query(Context.new(), query())).results[0]
        gate = asyncio.Event()

        async def worker(ctx):
            await gate.wait()

        with patch.object(plugin, "_find_mo_executable", return_value="mo"), patch.object(plugin, "run_clean", side_effect=worker) as run:
            await row.actions[0].action(Context.new(), ActionContext())
            task = plugin.task
            await plugin.start_clean(Context.new(), ActionContext())
            self.assertIs(task, plugin.task)
            self.assertEqual(plugin.session.state, "running")
            self.assertEqual(plugin.actions()[0].action, plugin.stop_clean)
            gate.set()
            await task
        self.assertEqual(run.call_count, 1)

    async def test_leave_reenter_reuses_session_and_latest_context(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        old, current = Context.new(), Context.new()
        await plugin.query(old, query())
        plugin.session = Session(state="running")
        plugin.session.feed("➤ Browser caches")
        plugin.session.feed("→ Chrome cache (1 items, 2MB)")
        original = plugin.session
        await plugin.leave_query(old)
        await plugin.publish(old)
        plugin.api.update_result.assert_not_called()
        response = await plugin.query(current, query())
        self.assertIs(plugin.session, original)
        self.assertIn("Chrome cache", response.results[0].preview.preview_data)
        await plugin.publish(old)
        self.assertIs(plugin.api.update_result.call_args.args[0], current)

    async def test_leave_during_visibility_check_cannot_publish_stale_result(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        ctx = Context.new()
        await plugin.query(ctx, query())

        async def leave(_):
            await plugin.leave_query(ctx)
            return True

        plugin.api.is_visible.side_effect = leave
        await plugin.publish(ctx)
        plugin.api.update_result.assert_not_called()

    async def test_hidden_window_skips_animation_pushes(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        plugin.api.is_visible.return_value = False
        ctx = Context.new()
        await plugin.query(ctx, query())
        await plugin.publish(ctx)
        plugin.api.update_result.assert_not_called()

    async def test_native_playback_does_not_publish_duplicate_animation_snapshots(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        plugin.api.is_visible.return_value = True
        ctx = Context.new()
        await plugin.query(ctx, query())
        plugin.session = Session(state="running")
        plugin.scene.mode = "sweeping"
        plugin.scene.frozen = False
        plugin.scene.started = plugin.session.started_at
        plugin.scene.deadline = plugin.session.started_at + 10
        await plugin.publish(ctx)
        for _ in range(10):
            await plugin.publish(ctx)
        self.assertEqual(plugin.api.update_result.await_count, 1)
        plugin.session.current_item = "Browser cache"
        await plugin.publish(ctx)
        self.assertEqual(plugin.api.update_result.await_count, 2)
        self.assertIsNone(plugin.api.update_result.call_args.args[1].actions)
        plugin.scene.motion_id += 1
        await plugin.publish(ctx)
        self.assertEqual(plugin.api.update_result.await_count, 3)
        self.assertIsNone(plugin.api.update_result.call_args.args[1].actions)
        await plugin.leave_query(ctx)

    async def test_missing_cli_reports_failure_and_recovery_action(self):
        plugin = MolePlugin()
        with (
            patch.object(plugin, "_find_mo_executable", return_value=None),
            patch.object(plugin, "_install_cli", side_effect=OSError("Could not download Mole.")),
            patch.object(plugin, "clean_command") as command,
        ):
            await plugin.run_clean(Context.new())
        command.assert_not_called()
        self.assertEqual(plugin.session.state, "failed")
        self.assertIsNone(plugin.task)
        self.assertIn("Could not download Mole.", plugin.session.message)
        self.assertEqual(plugin.actions()[0].action, plugin.start_clean)
        self.assertEqual(plugin.actions()[1].action, plugin.copy_output)

    async def test_missing_cli_installs_before_cleanup(self):
        plugin = MolePlugin()
        process = AsyncMock()
        process.stdout.read.return_value = b""
        process.wait.return_value = 0
        with (
            patch.object(plugin, "_find_mo_executable", return_value=None),
            patch.object(plugin, "_install_cli") as install,
            patch.object(plugin, "clean_command", return_value=["fixture"]),
            patch("src.runner.asyncio.create_subprocess_exec", return_value=process),
        ):
            await plugin.run_clean(Context.new())
        install.assert_called_once()
        self.assertEqual(plugin.session.state, "completed")
        self.assertIn("Mole CLI installed.", "\n".join(plugin.session.logs))

    async def test_missing_cli_shows_download_instead_of_cleaning(self):
        plugin = MolePlugin()
        gate = asyncio.Event()

        async def worker(ctx):
            self.assertEqual(plugin.session.state, "downloading")
            self.assertTrue(plugin.scene.frozen)
            await gate.wait()

        with (
            patch.object(plugin, "_find_mo_executable", return_value=None),
            patch.object(plugin, "run_clean", side_effect=worker),
        ):
            await plugin.start_clean(Context.new(), ActionContext())
            view = summary(plugin.session, LABELS, plugin.session.started_at)
            self.assertEqual(view["title"], "Downloading Mo")
            self.assertEqual(view["message"], "Finding the latest release")
            gate.set()
            await plugin.task

    async def test_success_keeps_exit_animation_alive_after_process_finishes(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        ctx = Context.new()
        await plugin.query(ctx, query())
        plugin.session = Session(state="running")
        plugin.scene.location = "bedroom"
        plugin.scene.position = POINTS["bedroom"]
        plugin.scene.mode = "sweeping"
        plugin.scene.frozen = False
        walk = plugin.scene._walk

        def quick_walk(now):
            walk(now)
            plugin.scene.duration = 0.01
            plugin.scene.deadline = now + 0.01
            plugin.scene.motion_ms = 10

        process = AsyncMock()
        process.stdout.read.return_value = b""
        process.wait.return_value = 0
        with (
            patch.object(plugin, "clean_command", return_value=["fixture"]),
            patch("src.runner.asyncio.create_subprocess_exec", return_value=process),
            patch.object(plugin.scene, "_walk", side_effect=quick_walk),
        ):
            task = asyncio.create_task(plugin.run_clean(ctx))
            plugin.task = task
            await task
            ended_at = plugin.session.ended_at
            self.assertEqual(plugin.session.state, "completed")
            self.assertIsNone(plugin.task)
            self.assertEqual(plugin.actions()[0].action, plugin.start_clean)
            self.assertIsNotNone(plugin.scene_task)
            await asyncio.wait_for(plugin.scene_task, 3)
        self.assertEqual(plugin.session.ended_at, ended_at)
        self.assertEqual(plugin.scene.position, POINTS["porch"])
        self.assertEqual(plugin.scene.facing, Facing.FRONT)
        self.assertTrue(plugin.scene.frozen)
        self.assertIsNone(plugin.scene_task)
        self.assertGreater(plugin.api.update_result.await_count, 1)

    async def test_exit_resumes_on_reentry_and_is_cancelled_by_restart_or_unload(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        ctx = Context.new()
        plugin.session = Session(state="completed", ended_at=100)
        plugin.scene.location = "bedroom"
        plugin.scene.position = POINTS["bedroom"]
        plugin.scene.mode = "sweeping"
        await plugin.query(ctx, query())
        exiting = plugin.scene_task
        await plugin.leave_query(ctx)
        await asyncio.wait_for(exiting, 1)
        self.assertIsNone(plugin.scene_task)
        plugin.api.update_result.assert_not_called()
        await plugin.query(Context.new(), query())
        exiting = plugin.scene_task
        self.assertIsNotNone(exiting)
        gate = asyncio.Event()

        async def worker(_):
            await gate.wait()

        with patch.object(plugin, "run_clean", side_effect=worker):
            await plugin.start_clean(ctx, ActionContext())
            task = plugin.task
            with self.assertRaises(asyncio.CancelledError):
                await exiting
            self.assertEqual(plugin.scene.position, POINTS["porch"])
            self.assertIsNot(plugin.scene_task, exiting)
            gate.set()
            await task
        plugin.session.state = "completed"
        plugin.scene.location = "bedroom"
        plugin.scene.position = POINTS["bedroom"]
        plugin.scene.mode = "sweeping"
        await plugin.query(ctx, query())
        exiting = plugin.scene_task
        await plugin.on_unload(ctx)
        self.assertTrue(exiting.cancelled())

    async def test_cancellation_reaps_process(self):
        plugin = MolePlugin()
        process = AsyncMock()
        entered = asyncio.Event()

        async def read(size):
            entered.set()
            await asyncio.Event().wait()

        process.stdout.read.side_effect = read
        with (
            patch.object(plugin, "clean_command", return_value=["fixture"]),
            patch("src.runner.asyncio.create_subprocess_exec", return_value=process),
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

    async def test_completed_view_keeps_quantity_for_reported_zero_and_unknown_amounts(self):
        for detail, amount in (
            ("Tracked cleanup: \x1b[32m59.8MB\x1b[0m | Items cleaned: 2", "59.8MB"),
            ("Space freed: 2GB", "2GB"),
            ("System was already clean; no additional space freed.", "0MB"),
            ("Tool cache cleanup completed.", "— MB"),
        ):
            with self.subTest(detail=detail):
                plugin = MolePlugin()
                process = AsyncMock()
                # Actual Windows summary forms; free disk space is not the cleanup total.
                output = f"Cleanup complete\n{detail}\nFree space: 123.45GB\n".encode()
                process.stdout.read.side_effect = [output[:30], output[30:], b""]
                process.wait.return_value = 0
                with (
                    patch.object(plugin, "clean_command", return_value=["fixture"]),
                    patch("src.runner.asyncio.create_subprocess_exec", return_value=process),
                ):
                    await plugin.run_clean(Context.new())
                tree = json.loads(plugin.preview().preview_data)
                caption = tree["root"]["children"][1]["children"]
                self.assertEqual(caption[0]["props"]["text"], amount)
                self.assertEqual(caption[1]["props"]["text"], "Cleanup complete")
                self.assertEqual(caption[2]["children"][0]["props"]["text"], "Elapsed")

    async def test_stop_before_spawn_is_honored(self):
        plugin = MolePlugin()
        plugin.stop_requested = True
        process = AsyncMock()
        process.stdout.read.return_value = b""
        process.wait.return_value = 1
        with (
            patch.object(plugin, "clean_command", return_value=["fixture"]),
            patch("src.runner.asyncio.create_subprocess_exec", return_value=process),
            patch.object(plugin, "terminate", new=AsyncMock()) as terminate,
        ):
            await plugin.run_clean(Context.new())
            terminate.assert_awaited_once()
        self.assertEqual(plugin.session.state, "stopped")

    async def test_unload_stops_worker_and_suppresses_updates(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        ctx = Context.new()
        await plugin.query(ctx, query())
        with patch.object(plugin, "terminate", new=AsyncMock()) as terminate:
            await plugin.on_unload(ctx)
            terminate.assert_awaited_once()
        await plugin.publish(ctx)
        plugin.api.update_result.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell required")
    async def test_hidden_powershell_utf8_quoting_exit_code_and_ui_failure(self):
        # This script only prints simulated output. It never runs the installed CLI.
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory(prefix="mole ' space ") as folder:
                script = Path(folder) / "mole.ps1"
                script.write_text(
                    "param([string]$Command,[Parameter(ValueFromRemainingArguments)][string[]]$Rest)\n"
                    'if ($Command -ne "clean" -or $Rest.Count -gt 0 -or $env:MOLE_DRY_RUN -ne "0") { exit 9 }\n'
                    'Write-Output "➤ 中文类别"\nWrite-Output "Tracked cleanup: 12MB"\n'
                    f"exit {code}\n",
                    encoding="utf-8-sig",
                )
                plugin = MolePlugin()
                plugin.api = AsyncMock()
                plugin.api.update_result.side_effect = RuntimeError("UI disconnected")
                ctx = Context.new()
                await plugin.query(ctx, query())
                with patch.object(plugin, "_find_mo_executable", return_value=str(script)):
                    await asyncio.wait_for(plugin.run_clean(ctx), 10)
                self.assertEqual(plugin.session.state, "completed" if code == 0 else "failed")
                self.assertEqual(plugin.session.stage, "中文类别")
                self.assertEqual(plugin.session.tracked, "12MB")
                self.assertEqual(plugin.session.freed, "")


class TestOutput(unittest.TestCase):
    def test_download_progress_replaces_the_cleaning_caption(self):
        state = Session(state="downloading", download_phase="lookup", started_at=10)
        value = summary(state, LABELS, 25)
        self.assertEqual(value["title"], "Downloading Mo")
        self.assertEqual(value["message"], "Finding the latest release")
        self.assertEqual(value["elapsed"], "Elapsed 00:15")
        state.download_phase = "archive"
        state.download_label = "1.42.0"
        state.download_received = 5 * 1024 * 1024
        state.download_total = 20 * 1024 * 1024
        self.assertEqual(summary(state, LABELS, 25)["message"], "1.42.0 · 5.0 MB / 20.0 MB · 25%")
        state.download_total = 0
        state.download_received = 1536 * 1024
        self.assertEqual(summary(state, LABELS, 25)["message"], "1.42.0 · 1.5 MB")
        state.download_phase = "install"
        self.assertEqual(summary(state, LABELS, 25)["message"], "Installing Mo")

    def test_amount_parser_accepts_localized_numbers_and_keeps_unknown_distinct_from_zero(self):
        for amount in ("1,023KB", "59,8MB", "1.24GB", "0B"):
            with self.subTest(amount=amount):
                state = Session()
                state.feed(f"Tracked cleanup: {amount} | Items cleaned: 2")
                state.finish(0)
                self.assertEqual(summary(state, LABELS, 100)["amount"], amount)
        unknown = Session()
        unknown.finish(0)
        self.assertEqual(summary(unknown, LABELS, 100)["amount"], "— MB")
        self.assertEqual(summary(unknown, LABELS, 100)["message"], LABELS["unknown"])

    def test_actual_and_tracked_cleanup_remain_distinct(self):
        state = Session(state="running")
        state.feed("➤ Browser caches")
        state.feed("✓ Edge Cache (2 items, 104.2MB)")
        state.feed("Cleanup complete")
        state.feed("Tracked cleanup: 104.2MB | Items cleaned: 2")
        state.finish(0)
        self.assertEqual(state.current_item, "Edge Cache")
        self.assertEqual(state.freed, "")
        self.assertEqual(summary(state, LABELS, 100)["message"], LABELS["tracked"])
        state.feed("Space freed: 2GB")
        self.assertEqual(summary(state, LABELS, 100)["message"], LABELS["freed"])

    def test_caught_errors_nonzero_exit_and_stopped(self):
        state = Session()
        state.feed("☻ Cleanup error: Access denied")
        state.finish(0)
        self.assertEqual(state.state, "failed")
        state = Session()
        state.finish(7)
        self.assertEqual(state.state, "failed")
        self.assertIn("code 7", state.message)
        state.finish(1, stopped=True)
        self.assertEqual(state.state, "stopped")

    def test_quiet_process_keeps_current_item_and_elapsed_freezes_on_stop(self):
        state = Session(state="running", started_at=10, last_output_at=20)
        state.current_item = "Chrome cache"
        value = summary(state, LABELS, 80)
        self.assertEqual(value["message"], "Chrome cache")
        self.assertNotIn("Waiting", value["message"])
        self.assertEqual(value["elapsed"], "Elapsed 01:10")
        state.ended_at = 90
        self.assertEqual(summary(state, LABELS, 200)["elapsed"], "Elapsed 01:20")

    def test_log_memory_is_bounded_and_controls_removed(self):
        state = Session()
        state.feed("\x1b[31m➤ Cache\x1b[0m\x00")
        self.assertEqual(state.stage, "Cache")
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
