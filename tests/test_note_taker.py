from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpToolset
from google.genai import types
from pydantic import Field

from psyclaw.agent import create_root_agent
from psyclaw.note_taker import NOTE_TAKER_INSTRUCTION, create_note_taker
from psyclaw import filesystem_mcp, user_tools


class ScriptedModel(BaseLlm):
    """Deterministic ADK model fixture that yields one response per call."""

    responses: list[LlmResponse]
    requests: list[object] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        del stream
        yield self.responses.pop(0)


class NoteTakerTest(unittest.IsolatedAsyncioTestCase):
    def test_note_taker_has_exactly_the_bounded_filesystem_toolset(self) -> None:
        with patch.object(user_tools, "USER_DIRECTORY", self._temporary_user_directory()):
            note_taker = create_note_taker("test/memory")

        self.assertEqual(len(note_taker.tools), 1)
        self.assertIsInstance(note_taker.tools[0], McpToolset)
        self.assertEqual(note_taker.mode, "single_turn")
        self.assertEqual(note_taker.include_contents, "default")
        self.assertEqual(
            note_taker.tools[0].tool_filter,
            list(filesystem_mcp.NOTE_TAKER_TOOL_ALLOWLIST),
        )

    def test_note_taker_instruction_covers_consolidation_and_provenance(self) -> None:
        for expected in ("Do nothing", "add, correct, or update", "reported facts", "observations", "hypotheses", "unknowns"):
            with self.subTest(expected=expected):
                self.assertIn(expected, NOTE_TAKER_INSTRUCTION)
        self.assertIn("conversation history is available", NOTE_TAKER_INSTRUCTION)
        self.assertIn("return exactly `Memory consolidated`", NOTE_TAKER_INSTRUCTION)
        self.assertIn("return exactly `No durable memory update`", NOTE_TAKER_INSTRUCTION)

    def test_root_keeps_only_readers_and_the_agent_tool(self) -> None:
        specialist = Agent(
            name="note_taker",
            description="Specialist memory consolidator.",
            model="test/memory",
            instruction="test",
            mode="single_turn",
        )
        root = create_root_agent(chat_model="test/chat", note_taker=specialist)

        self.assertEqual([tool.__name__ for tool in root.tools[:2]], ["list_files", "read_file"])
        self.assertEqual(len(root.tools), 3)
        self.assertEqual(type(root.tools[2]).__name__, "_SingleTurnAgentTool")
        self.assertIs(root.tools[2].agent, specialist)
        self.assertEqual(root.sub_agents, [specialist])
        self.assertIs(specialist.parent_agent, root)
        self.assertNotIn("write_file", [getattr(tool, "name", None) for tool in root.tools])
        self.assertNotIn("append_file", [getattr(tool, "name", None) for tool in root.tools])

    async def test_single_turn_subagent_receives_history_and_emits_standard_events(self) -> None:
        memory_model = ScriptedModel(
            model="test/memory",
            responses=[self._text_response("Memory consolidated")],
        )
        specialist = Agent(
            name="note_taker",
            description="Specialist memory consolidator.",
            model=memory_model,
            instruction="test",
            mode="single_turn",
            include_contents="default",
        )
        root = create_root_agent(
            chat_model=ScriptedModel(
                model="test/chat",
                responses=[
                    self._text_response("How did that feel?"),
                    LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[
                                types.Part(
                                    function_call=types.FunctionCall(
                                        name="note_taker",
                                        args={"request": "New durable information."},
                                        id="call-1",
                                    )
                                )
                            ],
                        )
                    ),
                    self._text_response("Thank you for sharing that."),
                ],
            ),
            note_taker=specialist,
        )
        service = InMemorySessionService()
        app = App(name="agent_tool_test", root_agent=root)
        await service.create_session(
            app_name=app.name,
            user_id="test-user",
            session_id="test-session",
        )
        runner = Runner(app=app, session_service=service)

        first_turn_events = [
            event
            async for event in runner.run_async(
                user_id="test-user",
                session_id="test-session",
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Yesterday I lost my job.")],
                ),
            )
        ]
        self.assertTrue(first_turn_events)

        events = [
            event
            async for event in runner.run_async(
                user_id="test-user",
                session_id="test-session",
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Today I started a new role.")],
                ),
            )
        ]

        self.assertTrue(
            any(
                call.name == "note_taker"
                for event in events
                for call in event.get_function_calls()
            )
        )
        responses = [
            response
            for event in events
            for response in event.get_function_responses()
            if response.name == "note_taker"
        ]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].response, {"result": "Memory consolidated"})
        child_request = memory_model.requests[0]
        child_contents = child_request.contents
        self.assertEqual(
            child_contents[0].parts[0].text,
            "Yesterday I lost my job.",
        )
        self.assertEqual(child_contents[1].parts[0].text, "For context:")
        self.assertIn("How did that feel?", child_contents[1].parts[1].text)
        self.assertEqual(
            child_contents[2].parts[0].text,
            "Today I started a new role.",
        )
        self.assertEqual(
            child_contents[-1].parts[0].text,
            "New durable information.",
        )
        final_text = "\n".join(
            part.text or ""
            for event in events
            if event.author == root.name and event.content and event.content.parts
            for part in event.content.parts
        )
        self.assertIn("Thank you for sharing that.", final_text)
        self.assertNotIn("Memory consolidated", final_text)

    @staticmethod
    def _text_response(text: str) -> LlmResponse:
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text=text)])
        )

    def _temporary_user_directory(self):
        temporary_directory = self.enterContext(tempfile.TemporaryDirectory())
        return Path(temporary_directory) / "user"


if __name__ == "__main__":
    unittest.main()
