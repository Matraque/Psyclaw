import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from psyclaw import user_tools


class UserToolsTest(unittest.TestCase):
    def test_references_are_loaded_first(self) -> None:
        self.assertEqual(user_tools.CORE_RECORD_PATHS[0], "references.md")

    def test_public_defaults_include_every_core_record(self) -> None:
        for record_path in user_tools.CORE_RECORD_PATHS:
            with self.subTest(record_path=record_path):
                self.assertTrue(
                    (user_tools.DEFAULT_USER_FILES_DIRECTORY / record_path).is_file()
                )

    def test_workspace_bootstrap_keeps_markdown_memory_separate_from_adk_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_directory = Path(temporary_directory) / "user"
            transcript_path = user_directory / ".adk" / "session.db"
            transcript_path.parent.mkdir(parents=True)
            transcript_path.touch()

            with patch.object(user_tools, "USER_DIRECTORY", user_directory):
                context = user_tools.get_context()
                entries = user_tools.list_files()
                memory_directory = user_directory / user_tools.MEMORY_DIRECTORY_NAME
                self.assertEqual(context["status"], "ok")
                self.assertTrue(context["workspace_bootstrapped"])
                self.assertEqual(user_tools._memory_root(), memory_directory.resolve())
                self.assertTrue((user_directory / user_tools.WORKSPACE_MARKER).is_file())
                self.assertFalse((memory_directory / user_tools.WORKSPACE_MARKER).exists())
                self.assertTrue((memory_directory / "memory.md").is_file())
                self.assertTrue(transcript_path.is_file())
                self.assertNotIn(".adk", {entry["path"] for entry in entries["entries"]})


if __name__ == "__main__":
    unittest.main()
