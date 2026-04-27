import unittest
from unittest.mock import patch

from wox_plugin import Context, Query, QueryEnv, QueryType, Selection

from src.main import ACTION_MODE_KEY, ACTION_PATH_KEY, RESULT_ID_ANALYZE, RESULT_ID_CLEAN, RESULT_SCORE, MolePlugin


def build_query(raw_query: str, trigger_keyword: str, search: str) -> Query:
    return Query(
        id="test-query",
        type=QueryType.INPUT,
        raw_query=raw_query,
        selection=Selection(),
        env=QueryEnv(),
        trigger_keyword=trigger_keyword,
        search=search,
    )


class TestMolePlugin(unittest.IsolatedAsyncioTestCase):
    async def test_query_provides_clean_actions(self) -> None:
        plugin = MolePlugin()
        query = build_query(raw_query="mo", trigger_keyword="mo", search="clean")

        with patch.object(MolePlugin, "_find_mo_executable", return_value="/opt/homebrew/bin/mo"):
            results = await plugin.query(Context.new(), query)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, RESULT_ID_CLEAN)
        self.assertEqual(results[0].score, RESULT_SCORE)
        self.assertEqual([action.context_data[ACTION_MODE_KEY] for action in results[0].actions], ["clean", "dry-run"])

    async def test_query_provides_clean_and_analyze_actions_for_empty_query(self) -> None:
        plugin = MolePlugin()
        query = build_query(raw_query="mo", trigger_keyword="mo", search="")

        with patch.object(MolePlugin, "_find_mo_executable", return_value="/opt/homebrew/bin/mo"):
            results = await plugin.query(Context.new(), query)

        self.assertEqual([result.id for result in results], [RESULT_ID_CLEAN, RESULT_ID_ANALYZE])

    async def test_query_provides_analyze_action_with_path(self) -> None:
        plugin = MolePlugin()
        query = build_query(raw_query="mo analyze ~/Downloads", trigger_keyword="mo", search="analyze ~/Downloads")

        with patch.object(MolePlugin, "_find_mo_executable", return_value="/opt/homebrew/bin/mo"):
            results = await plugin.query(Context.new(), query)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, RESULT_ID_ANALYZE)
        self.assertEqual(results[0].actions[0].context_data[ACTION_MODE_KEY], "analyze")
        self.assertEqual(results[0].actions[0].context_data[ACTION_PATH_KEY], "~/Downloads")

    async def test_query_provides_result_for_exact_global_mo_query(self) -> None:
        plugin = MolePlugin()
        query = build_query(raw_query="mo", trigger_keyword="", search="mo")

        with patch.object(MolePlugin, "_find_mo_executable", return_value="/opt/homebrew/bin/mo"):
            results = await plugin.query(Context.new(), query)

        self.assertEqual(len(results), 2)
        self.assertEqual([result.id for result in results], [RESULT_ID_CLEAN, RESULT_ID_ANALYZE])

    async def test_query_ignores_unrelated_global_query(self) -> None:
        plugin = MolePlugin()
        query = build_query(raw_query="music", trigger_keyword="", search="music")

        results = await plugin.query(Context.new(), query)

        self.assertEqual(results, [])

    async def test_query_returns_install_hint_when_mo_is_missing(self) -> None:
        plugin = MolePlugin()
        query = build_query(raw_query="mo", trigger_keyword="mo", search="clean")

        with patch.object(MolePlugin, "_find_mo_executable", return_value=None):
            results = await plugin.query(Context.new(), query)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, RESULT_ID_CLEAN)
        self.assertEqual(results[0].title, "i18n:title_not_installed")

    def test_build_clean_command_uses_resolved_mo_executable(self) -> None:
        plugin = MolePlugin()

        with patch.object(MolePlugin, "_find_mo_executable", return_value="/opt/homebrew/bin/mo"):
            self.assertEqual(plugin._build_clean_command(), ["/opt/homebrew/bin/mo", "clean"])
            self.assertEqual(plugin._build_clean_command(dry_run=True), ["/opt/homebrew/bin/mo", "clean", "--dry-run"])

    def test_build_analyze_command_uses_json_and_optional_path(self) -> None:
        plugin = MolePlugin()

        with patch.object(MolePlugin, "_find_mo_executable", return_value="/opt/homebrew/bin/mo"):
            self.assertEqual(plugin._build_analyze_command(), ["/opt/homebrew/bin/mo", "analyze", "--json"])
            self.assertEqual(
                plugin._build_analyze_command(path="/Volumes"),
                ["/opt/homebrew/bin/mo", "analyze", "--json", "/Volumes"],
            )

    def test_format_analyze_summary_shows_top_entries(self) -> None:
        plugin = MolePlugin()
        payload = (
            b'{"path":"/Users/me/Downloads","entries":['
            b'{"name":"Library","size":1073741824},'
            b'{"name":"Movies","size":536870912}],'
            b'"total_size":2147483648,"total_files":42}'
        )

        summary = plugin._format_analyze_summary(payload)

        self.assertIn("/Users/me/Downloads: Total 2.0GB, 42 files", summary)
        self.assertIn("Library 1.0GB", summary)
        self.assertIn("Movies 512.0MB", summary)


if __name__ == "__main__":
    unittest.main()
