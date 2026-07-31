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


class TemporaryMemoryFilesystem:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def read_text_file(self, path: str) -> dict[str, str]:
        return {"content": self._path(path).read_text(encoding="utf-8")}

    def write_file(self, path: str, content: str) -> dict[str, str]:
        self._path(path).write_text(content, encoding="utf-8")
        return {"status": "ok"}

    def edit_file(self, path: str, old_text: str, new_text: str) -> dict[str, str]:
        file_path = self._path(path)
        content = file_path.read_text(encoding="utf-8")
        if content.count(old_text) != 1:
            raise ValueError("Expected one matching passage.")
        file_path.write_text(content.replace(old_text, new_text), encoding="utf-8")
        return {"status": "ok"}

    def snapshot(self) -> dict[str, str]:
        return {
            path.relative_to(self.root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(self.root.rglob("*.md"))
        }

    def _path(self, path: str) -> Path:
        candidate = (self.root / path).resolve()
        if candidate.suffix != ".md" or self.root not in candidate.parents:
            raise ValueError("The test tool only permits Markdown files under its root.")
        return candidate


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
        self.assertEqual(len(root.tools), 4)
        self.assertEqual(type(root.tools[3]).__name__, "_SingleTurnAgentTool")
        self.assertIs(root.tools[3].agent, specialist)
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

    async def test_memory_state_gates_through_runner(self) -> None:
        old_fact = "- Reported fact: User works at Northwind.\n"
        corrected_fact = "- Reported fact: User left Northwind.\n"
        added_fact = "# Memory\n- Reported fact: User started a new role.\n"
        cases = (
            (
                "add",
                "# Memory\n",
                "write_file",
                {"path": "memory.md", "content": added_fact},
                added_fact,
                "Memory consolidated",
            ),
            (
                "no-op",
                added_fact,
                None,
                None,
                added_fact,
                "No durable memory update",
            ),
            (
                "correction",
                f"# Memory\n{old_fact}",
                "edit_file",
                {"path": "memory.md", "old_text": old_fact, "new_text": corrected_fact},
                f"# Memory\n{corrected_fact}",
                "Memory consolidated",
            ),
        )
        for name, initial, tool_name, tool_args, expected, result in cases:
            with self.subTest(name=name):
                before, after = await self._run_memory_case(
                    initial, tool_name, tool_args, result
                )
                self.assertEqual(after["memory.md"], expected)
                self.assertEqual(before == after, tool_name is None)

    async def _run_memory_case(
        self,
        memory_before: str,
        tool_name: str | None,
        tool_args: dict[str, str] | None,
        result: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_directory = Path(temporary_directory) / "synthetic-user"
            memory_directory = user_directory / "memory"
            memory_directory.mkdir(parents=True)
            (memory_directory / "memory.md").write_text(memory_before, encoding="utf-8")
            filesystem = TemporaryMemoryFilesystem(memory_directory)
            memory_responses = [self._text_response(result)]
            if tool_name and tool_args:
                memory_responses = [
                    self._function_call("read_text_file", {"path": "memory.md"}, "read-1"),
                    self._function_call(tool_name, tool_args, "write-1"),
                    self._text_response(result),
                ]
            specialist = Agent(
                name="note_taker",
                model=ScriptedModel(model="test/memory", responses=memory_responses),
                instruction=NOTE_TAKER_INSTRUCTION,
                mode="single_turn",
                include_contents="default",
                tools=[filesystem.read_text_file, filesystem.write_file, filesystem.edit_file],
            )
            root = create_root_agent(
                chat_model=ScriptedModel(
                    model="test/chat",
                    responses=[
                        self._function_call(
                            "note_taker",
                            {"request": "Assess the user's new information."},
                            "memory-1",
                        ),
                        self._text_response("Thank you for sharing that."),
                    ],
                ),
                note_taker=specialist,
            )
            service = InMemorySessionService()
            app = App(name="deterministic_memory_gate", root_agent=root)
            await service.create_session(app_name=app.name, user_id="synthetic-user", session_id="memory-gate")
            runner = Runner(app=app, session_service=service)
            with patch.object(user_tools, "USER_DIRECTORY", user_directory):
                user_tools._initialise_user_workspace()
                before = filesystem.snapshot()
                async for _ in runner.run_async(
                        user_id="synthetic-user",
                        session_id="memory-gate",
                        new_message=types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(
                                    text="I started a new role this week."
                                )
                            ],
                        ),
                    ):
                    pass
            after = filesystem.snapshot()

        return before, after

    @staticmethod
    def _function_call(name: str, args: dict[str, str], call_id: str) -> LlmResponse:
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name=name,
                            args=args,
                            id=call_id,
                        )
                    )
                ],
            )
        )

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
