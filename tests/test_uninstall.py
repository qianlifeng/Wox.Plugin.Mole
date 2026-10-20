import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from wox_plugin import ActionContext, Context
from src.main import MolePlugin
from src.uninstall import UninstallBackend, parse_apps
from tests.test_query import query

APPS = [
    {"key": "registry:a", "name": "Editor", "size": "2GB", "size_kb": 2097152},
    {"key": "package:b", "name": "Editor", "size": "1GB", "size_kb": 1048576},
    {"key": "registry:c", "name": "中文应用", "size": "10MB", "size_kb": 10240},
]


class TestUninstallUI(unittest.IsolatedAsyncioTestCase):
    async def test_enter_loads_mole_apps_and_returns_list_not_full_preview(self):
        plugin = MolePlugin()
        plugin.api = AsyncMock()
        plugin.uninstall_backend.run = AsyncMock(return_value=APPS)
        response = await plugin.query(Context.new(), query("uninstall"))
        self.assertIsNone(response.layout.result_preview_width_ratio)
        self.assertEqual(response.results[0].title, "i18n:apps_loading")
        await plugin.apps_task
        response = await plugin.query(Context.new(), query("uninstall", "EDITOR"))
        self.assertEqual([r.title for r in response.results], ["Editor", "Editor"])
        self.assertEqual([r.tails[0].text for r in response.results], ["2GB", "1GB"])
        self.assertNotEqual(response.results[0].id, response.results[1].id)
        plugin.uninstall_backend.run.assert_awaited_once_with("list", "", False)

    async def test_selection_requires_confirmation_and_preserves_exact_identity(self):
        plugin = MolePlugin()
        plugin.apps = APPS.copy()
        plugin.api = AsyncMock()
        plugin.active_command = "uninstall"
        plugin.uninstall_backend.run = AsyncMock(return_value=[APPS[0], APPS[2]])
        ctx = Context.new()
        await plugin.select_uninstall(ctx, ActionContext(context_data={"key": "package:b"}))
        plugin.uninstall_backend.run.assert_not_called()
        response = plugin.query_uninstall(ctx, "")
        self.assertEqual(response.results[0].id, "mole-uninstall-confirm")
        await plugin.confirm_uninstall(ctx, ActionContext(context_data={"key": "registry:a"}))
        self.assertIsNone(plugin.apps_task)
        await plugin.confirm_uninstall(ctx, ActionContext(context_data={"key": "package:b"}))
        task = plugin.apps_task
        await plugin.confirm_uninstall(ctx, ActionContext(context_data={"key": "package:b"}))
        await task
        plugin.uninstall_backend.run.assert_awaited_once_with("uninstall", "package:b", False)
        self.assertEqual([app["key"] for app in plugin.apps], ["registry:a", "registry:c"])

    async def test_failure_does_not_claim_success_or_keep_stale_actions(self):
        plugin = MolePlugin()
        plugin.apps = APPS.copy()
        plugin.uninstall_backend.run = AsyncMock(side_effect=OSError("still installed"))
        await plugin.load_apps(Context.new(), key="registry:a")
        self.assertIsNone(plugin.apps)
        self.assertIn("still installed", plugin.apps_error)
        self.assertEqual(plugin.query_uninstall(Context.new(), "").results[0].title, "i18n:apps_error")

    def test_parser_sorts_and_rejects_ambiguous_keys(self):
        self.assertEqual(parse_apps(json.dumps(list(reversed(APPS)))), APPS)
        with self.assertRaises(ValueError):
            parse_apps(json.dumps([APPS[0], APPS[0]]))

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell required")
    async def test_real_bridge_calls_only_selected_mole_function(self):
        # No registry access or real uninstaller: the installed Mole is replaced
        # by a fixture implementing the same function contract.
        fixture = r"""
param([switch]$ShowHelp)
if (-not $ShowHelp) { throw 'Interactive main must not run' }
$script:removed = ''
function Get-InstalledApplications {
    param([switch]$ForceRescan)
    foreach ($key in @('registry:a','registry:b')) {
        if ($key -ne $script:removed) {
            [PSCustomObject]@{Key=$key;Name='中文 App';SizeHuman='10MB';SizeKB=10240}
        }
    }
}
function Get-AppSelectionKey { param($App) return $App.Key }
function Test-ProtectedApp { param($Name) return $false }
function Set-DryRunMode { param([bool]$Enabled) }
function Uninstall-SelectedApps {
    param([array]$Apps)
    if ($Apps.Count -ne 1) { throw 'Expected exactly one app' }
    $script:removed = $Apps[0].Key
    Write-Host 'Fixture removed only selected record'
}
"""
        with tempfile.TemporaryDirectory(prefix="mole ' ") as folder:
            root = Path(folder)
            (root / "bin").mkdir()
            (root / "bin" / "uninstall.ps1").write_text(fixture, encoding="utf-8-sig")
            backend = UninstallBackend()
            with patch("src.uninstall.find_root", return_value=root):
                apps = await backend.run("list")
                self.assertEqual(len(apps), 2)
                self.assertEqual(apps[0]["name"], "中文 App")
                apps = await backend.run("uninstall", "registry:b")
                self.assertEqual([app["key"] for app in apps], ["registry:a"])
                with self.assertRaises(OSError):
                    await backend.run("uninstall", "registry:missing")
