import asyncio
import json
import sys
import unittest
from unittest.mock import AsyncMock, patch

from src.commands import CommandSession, load_report
from src.dashboard import Session, snapshot, LABELS
from src.main import MolePlugin
from tests.test_query import query
from wox_plugin import Context


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_enter_ready_and_default_action_starts(self):
        plugin = MolePlugin()
        for name in ("optimize", "analyze"):
            response = await plugin.query(Context.new(), query(name))
            self.assertTrue(response.results[0].actions[0].is_default)
            self.assertEqual(response.results[0].actions[0].context_data["command"], name)
            self.assertEqual(plugin.jobs[name].session.state, "ready")
        self.assertIsNot(plugin.jobs["optimize"].session, plugin.jobs["analyze"].session)

    async def test_optimize_stream_and_completion_with_simulated_process(self):
        job = CommandSession("optimize", AsyncMock())
        code = "print('➤ Network');print('DNS cache refreshed')"
        with patch.object(job, "argv", return_value=[sys.executable, "-X", "utf8", "-c", code]):
            await job.start()
            task = job.task
            await asyncio.wait_for(task, 10)
        self.assertEqual(job.session.state, "completed")
        self.assertEqual(job.session.sections[-1][0], "Network")
        self.assertIn("DNS cache refreshed", job.session.sections[-1][1])
        self.assertGreater(job.publish.await_count, 1)

    async def test_status_snapshots_replace_previous_metrics(self):
        job = CommandSession("status", AsyncMock())
        payloads = [dict(cpu=dict(usage=i), memory={}, collected_at=str(i)) for i in (10, 20)]
        code = ";".join("print(" + repr(json.dumps(p)) + ")" for p in payloads)
        with patch.object(job, "argv", return_value=[sys.executable, "-X", "utf8", "-c", code]):
            await job.start()
            await asyncio.wait_for(job.task, 10)
        self.assertEqual(job.session.state, "completed")
        self.assertEqual(list(job.session.sections[1][1]), ["Usage · 20.0%"])
        self.assertEqual(len(job.session.sections), 6)

    async def test_leaving_command_stops_pushes_not_task_state(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        plugin.api.get_theme_colors.side_effect = RuntimeError("no theme")
        await plugin.query(Context.new(), query("optimize"))
        job = plugin.jobs["optimize"]
        await plugin.leave_query(Context.new())
        await job.publish()
        plugin.api.update_result.assert_not_awaited()
        load_report(job.session, "status", {"cpu": {"usage": 12}, "memory": {}})
        await plugin.query(Context.new(), query("optimize"))
        self.assertIn("12.0%", list(job.session.sections[1][1])[0])

    async def test_status_auto_start_reuse_exit_and_reenter(self):
        plugin = MolePlugin()
        job = plugin.command_job("status")
        code = 'import time; print(\'{"cpu":{"usage":12}}\', flush=True); time.sleep(60)'
        with patch.object(job, "argv", return_value=[sys.executable, "-u", "-c", code]):
            response = await plugin.query(Context.new(), query("status"))
            self.assertEqual(len(job.session.cards), 6)
            self.assertEqual(response.results[0].actions[0].name, "i18n:action_back")
            first_task = job.task
            await plugin.query(Context.new(), query("status"))
            self.assertIs(job.task, first_task)
            for _ in range(100):
                if job.session.output_revision:
                    break
                await asyncio.sleep(0.05)
            self.assertGreater(job.session.output_revision, 0)
            process = job.process
            await plugin.leave_query(Context.new())
            self.assertTrue(first_task.done())
            self.assertIsNotNone(process.returncode)
            self.assertIsNone(job.process)
            await plugin.query(Context.new(), query("status"))
            self.assertIsNot(job.task, first_task)
            second_task = job.task
            await plugin.query(Context.new(), query("clean"))
            self.assertTrue(second_task.done())
            self.assertIsNone(job.process)

    async def test_analyze_rejects_tui_only_before_scan(self):
        job = CommandSession("analyze", AsyncMock())
        process = AsyncMock()
        process.communicate.return_value = (b"Usage: analyze [PATH]", b"")
        process.returncode = 0
        with (
            patch.object(job, "argv", return_value=["analyze.exe", "--json", "C:/"]),
            patch.object(job, "spawn", return_value=process) as spawn,
        ):
            await job.start()
            await job.task
        self.assertEqual(spawn.await_count, 1)
        self.assertEqual(job.session.state, "failed")
        self.assertIn("does not provide", job.session.message)

    def test_partial_disk_sizes_and_safe_labels(self):
        session = Session()
        load_report(
            session,
            "analyze",
            {
                "scan_status": "partial",
                "entries": [
                    {"name": "<script>", "size": 0, "scan_status": "unavailable"},
                    {"name": "large", "size": 2048, "scan_status": "partial"},
                ],
            },
        )
        rows = list(session.sections[0][1])
        self.assertIn("2.0KB · Partial", rows[0])
        self.assertIn("Unknown", rows[1])
        self.assertEqual(snapshot("analyze", session, LABELS)["state"], "ready")
