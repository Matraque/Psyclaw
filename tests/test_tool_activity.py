import unittest
from types import SimpleNamespace

from psyclaw.tool_activity import ToolActivityPlugin


class ToolActivityPluginTest(unittest.IsolatedAsyncioTestCase):
    async def test_logs_safe_start_and_returns_none(self) -> None:
        plugin = ToolActivityPlugin()

        with self.assertLogs("psyclaw.tool_activity", "INFO") as logs:
            result = await plugin.before_tool_callback(
                tool=SimpleNamespace(name="list_files"),
                tool_args={"private": "must not appear"},
                tool_context=object(),
            )

        self.assertIsNone(result)
        self.assertEqual(logs.output, ["INFO:psyclaw.tool_activity:Tool started: list_files"])

    async def test_logs_safe_success_and_returns_none(self) -> None:
        plugin = ToolActivityPlugin()

        with self.assertLogs("psyclaw.tool_activity", "INFO") as logs:
            result = await plugin.after_tool_callback(
                tool=SimpleNamespace(name="read_file"),
                tool_args={"private": "must not appear"},
                tool_context=object(),
                result={"secret": "must not appear"},
            )

        self.assertIsNone(result)
        self.assertEqual(logs.output, ["INFO:psyclaw.tool_activity:Tool succeeded: read_file"])

    async def test_logs_safe_error_and_returns_none(self) -> None:
        plugin = ToolActivityPlugin()

        with self.assertLogs("psyclaw.tool_activity", "ERROR") as logs:
            result = await plugin.on_tool_error_callback(
                tool=SimpleNamespace(name="list_files"),
                tool_args={"private": "must not appear"},
                tool_context=object(),
                error=ValueError("must not appear"),
            )

        self.assertIsNone(result)
        self.assertEqual(logs.output, ["ERROR:psyclaw.tool_activity:Tool failed: list_files"])

    async def test_unsafe_tool_name_uses_unknown_fallback(self) -> None:
        plugin = ToolActivityPlugin()

        with self.assertLogs("psyclaw.tool_activity", "INFO") as logs:
            await plugin.before_tool_callback(
                tool=SimpleNamespace(name="list_files\nsecret"),
                tool_args={},
                tool_context=object(),
            )

        self.assertEqual(logs.output, ["INFO:psyclaw.tool_activity:Tool started: unknown"])


if __name__ == "__main__":
    unittest.main()
