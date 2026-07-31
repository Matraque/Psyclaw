import string
import unittest
from unittest.mock import patch

from psyclaw.instruction import INSTRUCTION_TEMPLATE, build_instruction


class InstructionTest(unittest.TestCase):
    def test_template_exposes_only_supported_runtime_fields(self) -> None:
        fields = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(INSTRUCTION_TEMPLATE)
            if field_name is not None
        }

        self.assertEqual(
            fields,
            {
                "current_date",
                "session_guidance",
                "record_warnings",
                "user_records",
            },
        )

    @patch("psyclaw.instruction.get_context")
    @patch("psyclaw.instruction.get_date")
    def test_build_instruction_fills_runtime_context(
        self, mock_get_date, mock_get_context
    ) -> None:
        mock_get_date.return_value = {"status": "ok", "date": "2026-07-23"}
        mock_get_context.return_value = {
            "status": "ok",
            "new_user": False,
            "latest_session_note": "session_notes/2026-07-23.md",
            "records": {"memory.md": "# Clinical memory\n\nCurrent context."},
            "missing": [],
            "empty": [],
            "truncated": [],
        }

        instruction = build_instruction(None)

        self.assertIn("Current UTC date: 2026-07-23", instruction)
        self.assertIn("session_notes/2026-07-23.md", instruction)
        self.assertIn("Ask the note-taker to consolidate", instruction)
        self.assertNotIn("Continue updating that note", instruction)
        self.assertIn('<user-record path="memory.md">', instruction)
        self.assertNotIn("{current_date}", instruction)
        self.assertNotIn("{user_records}", instruction)

    def test_psychologist_delegates_durable_memory_without_claiming_to_write_notes(self) -> None:
        self.assertIn("call the `note_taker` tool", INSTRUCTION_TEMPLATE)
        self.assertIn("current conversation history natively", INSTRUCTION_TEMPLATE)
        self.assertIn("do not manufacture a parallel transcript", INSTRUCTION_TEMPLATE)
        self.assertIn("`Memory consolidated`", INSTRUCTION_TEMPLATE)
        self.assertIn("`No durable memory update`", INSTRUCTION_TEMPLATE)
        self.assertNotIn("Continuously maintain the files", INSTRUCTION_TEMPLATE)
        self.assertNotIn("Keep `memory.md` concise by revising", INSTRUCTION_TEMPLATE)
        self.assertNotIn("Continue updating that note", INSTRUCTION_TEMPLATE)
        self.assertNotIn("Update the care plan", INSTRUCTION_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
