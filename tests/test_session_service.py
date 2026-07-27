from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.cli.utils.service_factory import create_session_service_from_options
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions.session import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from pydantic import PrivateAttr

from psyclaw import adk_web
from psyclaw.session_service import (
    create_session_service,
    get_session_database_path,
    get_session_service_uri,
)


class FakeLlm(BaseLlm):
    model: str = "fake-session-test"
    _calls: int = PrivateAttr(default=0)

    @property
    def calls(self) -> int:
        return self._calls

    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        self._calls += 1
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text=f"assistant reply {self._calls}")],
            )
        )


class FailOnAppendSessionService(SqliteSessionService):
    """A deterministic ADK service double that fails on a chosen event write."""

    def __init__(self, db_path: str, fail_on_append: int) -> None:
        super().__init__(db_path)
        self.fail_on_append = fail_on_append
        self.append_calls = 0

    async def append_event(self, session: Session, event: Event) -> Event:
        self.append_calls += 1
        if self.append_calls == self.fail_on_append:
            raise OSError("simulated SQLite write failure")
        return await super().append_event(session=session, event=event)


class SessionServiceTest(unittest.IsolatedAsyncioTestCase):
    async def _run_turn(
        self,
        runner: Runner,
        *,
        session_id: str,
        text: str,
    ) -> None:
        async for _ in runner.run_async(
            user_id="test-user",
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=text)]),
        ):
            pass

    def _runner(self, service: SqliteSessionService) -> tuple[Runner, FakeLlm]:
        model = FakeLlm()
        agent = Agent(name="session_test_agent", model=model, instruction="Reply once.")
        return (
            Runner(app=App(name="session_test_app", root_agent=agent), session_service=service),
            model,
        )

    async def test_persists_three_turns_in_order_after_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient espace é"
            service = create_session_service(patient_directory)
            runner, _ = self._runner(service)
            await service.create_session(
                app_name="session_test_app", user_id="test-user", session_id="session-one"
            )

            for text in ("STT text one", "STT text two", "STT text three"):
                await self._run_turn(runner, session_id="session-one", text=text)

            reopened_service = create_session_service(patient_directory)
            session = await reopened_service.get_session(
                app_name="session_test_app", user_id="test-user", session_id="session-one"
            )

            self.assertIsNotNone(session)
            messages = [
                (event.author, event.content.parts[0].text)
                for event in session.events
                if event.content and event.content.parts and event.content.parts[0].text
            ]
            self.assertEqual(
                messages,
                [
                    ("user", "STT text one"),
                    ("session_test_agent", "assistant reply 1"),
                    ("user", "STT text two"),
                    ("session_test_agent", "assistant reply 2"),
                    ("user", "STT text three"),
                    ("session_test_agent", "assistant reply 3"),
                ],
            )
            self.assertTrue((patient_directory / ".adk" / "session.db").is_file())

    async def test_sessions_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = create_session_service(Path(temporary_directory) / "patient")
            runner, _ = self._runner(service)
            for session_id, text in (("one", "first session"), ("two", "second session")):
                await service.create_session(
                    app_name="session_test_app", user_id="test-user", session_id=session_id
                )
                await self._run_turn(runner, session_id=session_id, text=text)

            first_session = await service.get_session(
                app_name="session_test_app", user_id="test-user", session_id="one"
            )
            second_session = await service.get_session(
                app_name="session_test_app", user_id="test-user", session_id="two"
            )
            self.assertEqual(first_session.events[0].content.parts[0].text, "first session")
            self.assertEqual(second_session.events[0].content.parts[0].text, "second session")

    async def test_initial_event_write_failure_stops_before_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = get_session_database_path(Path(temporary_directory) / "patient")
            service = FailOnAppendSessionService(str(database_path), fail_on_append=1)
            runner, model = self._runner(service)
            await service.create_session(
                app_name="session_test_app", user_id="test-user", session_id="write-failure"
            )

            with self.assertRaisesRegex(OSError, "simulated SQLite write failure"):
                await self._run_turn(runner, session_id="write-failure", text="do not call model")
            self.assertEqual(model.calls, 0)

    async def test_response_write_failure_is_visible_and_keeps_user_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = get_session_database_path(Path(temporary_directory) / "patient")
            service = FailOnAppendSessionService(str(database_path), fail_on_append=2)
            runner, model = self._runner(service)
            await service.create_session(
                app_name="session_test_app", user_id="test-user", session_id="response-failure"
            )

            yielded_events = []
            with self.assertRaisesRegex(OSError, "simulated SQLite write failure"):
                async for event in runner.run_async(
                    user_id="test-user",
                    session_id="response-failure",
                    new_message=types.Content(
                        role="user", parts=[types.Part(text="keep this user text")]
                    ),
                ):
                    yielded_events.append(event)

            self.assertEqual(yielded_events, [])

            session = await service.get_session(
                app_name="session_test_app", user_id="test-user", session_id="response-failure"
            )
            self.assertEqual(model.calls, 1)
            self.assertEqual(len(session.events), 1)
            self.assertEqual(session.events[0].author, "user")
            self.assertEqual(session.events[0].content.parts[0].text, "keep this user text")

    def test_private_directory_rejects_adk_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient"
            patient_directory.mkdir()
            adk_directory = patient_directory / ".adk"
            try:
                adk_directory.symlink_to(Path(temporary_directory) / "outside", target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symbolic links are unavailable: {error}")

            with self.assertRaisesRegex(RuntimeError, "cannot be a symbolic link"):
                get_session_database_path(patient_directory)

    def test_private_directory_rejects_session_database_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient"
            database_path = get_session_database_path(patient_directory)
            try:
                database_path.symlink_to(Path(temporary_directory) / "outside.db")
            except OSError as error:
                self.skipTest(f"symbolic links are unavailable: {error}")

            with self.assertRaisesRegex(RuntimeError, "database cannot be a symbolic link"):
                get_session_database_path(patient_directory)

    @unittest.skipIf(os.name == "nt", "POSIX permissions are unavailable on Windows")
    def test_existing_patient_directory_permissions_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "patient"
            patient_directory.mkdir(mode=0o750)
            patient_directory.chmod(0o750)

            adk_directory = get_session_database_path(patient_directory).parent

            self.assertEqual(patient_directory.stat().st_mode & 0o777, 0o750)
            self.assertEqual(adk_directory.stat().st_mode & 0o777, 0o700)

    async def test_uri_and_web_launcher_use_explicit_adk_sqlite_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            patient_directory = Path(temporary_directory) / "private espace é"
            with patch.dict("os.environ", {"PSYCLAW_PATIENT_DIR": str(patient_directory)}):
                uri = get_session_service_uri()
                arguments = adk_web.build_adk_web_arguments(
                    host="127.0.0.1", port=8123, allow_origins=("http://localhost:5173",)
                )

            self.assertEqual(uri, f"sqlite:///{(patient_directory / '.adk' / 'session.db').resolve().as_posix()}")
            self.assertEqual(
                arguments,
                [
                    "web",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8123",
                    "--no-reload",
                    "--session_service_uri",
                    uri,
                    "--allow_origins",
                    "http://localhost:5173",
                    str(adk_web.PROJECT_DIRECTORY),
                ],
            )
            service = create_session_service_from_options(
                base_dir=Path(temporary_directory), session_service_uri=uri
            )
            self.assertIsInstance(service, SqliteSessionService)
            await service.create_session(
                app_name="session_test_app", user_id="test-user", session_id="cli-uri"
            )
            self.assertTrue((patient_directory / ".adk" / "session.db").is_file())


if __name__ == "__main__":
    unittest.main()
