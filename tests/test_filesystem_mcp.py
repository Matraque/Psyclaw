import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams

from psyclaw import filesystem_mcp
from psyclaw import user_tools


class FilesystemMcpTest(unittest.TestCase):
    def test_factory_uses_adk_toolset_with_only_the_canonical_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_directory = Path(temporary_directory) / "workspace" / ".." / "user"
            transcript_path = user_directory / ".adk" / "session.db"
            transcript_path.parent.mkdir(parents=True)
            transcript_path.touch()

            with patch.object(user_tools, "USER_DIRECTORY", user_directory):
                toolset = filesystem_mcp.create_user_filesystem_toolset()
                memory_directory = user_directory / "memory"
                self.assertTrue((user_directory / user_tools.WORKSPACE_MARKER).is_file())
                self.assertFalse((memory_directory / user_tools.WORKSPACE_MARKER).exists())
                self.assertTrue((memory_directory / "memory.md").is_file())

        self.assertIsInstance(toolset, McpToolset)
        connection_params = toolset.connection_params
        self.assertIsInstance(connection_params, StdioConnectionParams)
        self.assertEqual(connection_params.timeout, 5.0)
        parameters = connection_params.server_params
        memory_directory = user_directory / "memory"
        self.assertEqual(parameters.command, "npx")
        self.assertEqual(
            parameters.args,
            [
                "-y",
                filesystem_mcp.FILESYSTEM_SERVER_PACKAGE,
                str(memory_directory.resolve()),
            ],
        )
        self.assertEqual(parameters.args[-1:], [str(memory_directory.resolve())])
        self.assertNotIn(str(transcript_path.resolve()), parameters.args)
        self.assertNotIn(str(user_directory.resolve()), parameters.args)
        self.assertNotIn(str(Path.cwd().resolve()), parameters.args)
        self.assertEqual(toolset.tool_filter, list(filesystem_mcp.NOTE_TAKER_TOOL_ALLOWLIST))

    def test_allowlist_is_exact_and_excludes_unneeded_filesystem_tools(self) -> None:
        self.assertEqual(
            filesystem_mcp.NOTE_TAKER_TOOL_ALLOWLIST,
            (
                "read_text_file",
                "list_directory",
                "create_directory",
                "write_file",
                "edit_file",
            ),
        )
        self.assertNotIn("read_multiple_files", filesystem_mcp.NOTE_TAKER_TOOL_ALLOWLIST)
        for excluded_tool in (
            "read_media_file",
            "move_file",
            "search_files",
            "directory_tree",
            "get_file_info",
        ):
            with self.subTest(excluded_tool=excluded_tool):
                self.assertNotIn(excluded_tool, filesystem_mcp.NOTE_TAKER_TOOL_ALLOWLIST)

    def test_windows_uses_official_cmd_launcher_without_unix_shell(self) -> None:
        memory_root = Path("C:/Users/example/.psyclaw-data/user/memory")
        with patch.object(filesystem_mcp.sys, "platform", "win32"):
            connection_params = filesystem_mcp._filesystem_connection_params(memory_root)

        self.assertIsInstance(connection_params, StdioConnectionParams)
        parameters = connection_params.server_params
        self.assertEqual(parameters.command, "cmd")
        self.assertEqual(
            parameters.args,
            [
                "/c",
                "npx",
                "-y",
                filesystem_mcp.FILESYSTEM_SERVER_PACKAGE,
                str(memory_root.resolve()),
            ],
        )

if __name__ == "__main__":
    unittest.main()
