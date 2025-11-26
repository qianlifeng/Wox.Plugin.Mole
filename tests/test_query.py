import unittest

from wox_plugin import Context, Query, QueryEnv, QueryType, Selection

from src.main import MolePlugin


class TestMolePlugin(unittest.IsolatedAsyncioTestCase):
    async def test_query_provides_clean_actions(self) -> None:
        plugin = MolePlugin()
        query = Query(
            type=QueryType.INPUT,
            raw_query="mo",
            selection=Selection(),
            env=QueryEnv(),
            trigger_keyword="mo",
            search="clean",
        )

        results = await plugin.query(Context.new(), query)

        self.assertGreaterEqual(len(results), 2)
        self.assertEqual(results[0].id, "mole-clean")
        self.assertEqual(results[1].id, "mole-clean-dry-run")


if __name__ == "__main__":
    unittest.main()
