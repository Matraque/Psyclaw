import unittest

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


if __name__ == "__main__":
    unittest.main()
