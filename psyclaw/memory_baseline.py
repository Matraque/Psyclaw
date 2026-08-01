"""Small, synthetic real-model baseline for durable-memory consolidation.

Run this module deliberately, outside the deterministic unittest suite.  It
uses the configured Psyclaw chat and memory models, but always creates a new
temporary user workspace; it never reads from or writes to a real user root.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from unittest.mock import patch

from google.adk.apps import App
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from psyclaw import user_tools


NOTE_TAKER_NAME = "note_taker"
PSYCHOLOGIST_NAME = "psyclaw_agent"
MUTATING_MEMORY_TOOLS = frozenset({"write_file", "edit_file"})


@dataclass(frozen=True)
class BaselineScenario:
    """A synthetic input and the compact deterministic state checks it needs."""

    identifier: str
    message: str
    seed_path: str | None = None
    seed_text: str | None = None
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    max_occurrences: tuple[tuple[str, int], ...] = ()
    expects_note_taker: bool = True


SCENARIOS = (
    BaselineScenario(
        identifier="durable_fact",
        message="Please remember that I work as a botanist.",
        required_terms=("botanist",),
    ),
    BaselineScenario(
        identifier="greeting",
        message="Hello, thank you.",
        expects_note_taker=False,
    ),
    BaselineScenario(
        identifier="correction",
        message="Correction: I no longer work at Northwind; I left that job.",
        seed_path="user_profile.md",
        seed_text="\n- Reported fact: User works at Northwind.\n",
        required_terms=("northwind", "left"),
        forbidden_terms=("user works at northwind",),
        max_occurrences=(("northwind", 1),),
    ),
)


@dataclass(frozen=True)
class PsychologistDecision:
    note_taker_calls: int
    meets_expectation: bool


@dataclass(frozen=True)
class NoteTakerBehavior:
    filesystem_calls: tuple[str, ...]
    mutating_calls: tuple[str, ...]
    no_op: bool
    meets_expectation: bool


@dataclass(frozen=True)
class MemoryState:
    changed_files: int
    required_terms_present: bool
    forbidden_terms_absent: bool
    duplication_free: bool
    meets_expectation: bool


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    candidate_tokens: int
    tool_use_prompt_tokens: int
    thoughts_tokens: int
    total_tokens: int
    events_with_usage: int


@dataclass(frozen=True)
class BaselineCaseResult:
    identifier: str
    latency_ms: int
    psychologist_decision: PsychologistDecision
    note_taker_behavior: NoteTakerBehavior
    memory_state: MemoryState
    token_usage_by_author: dict[str, TokenUsage]
    passed: bool


@dataclass(frozen=True)
class BaselineReport:
    cases: tuple[BaselineCaseResult, ...]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    def as_dict(self) -> dict[str, object]:
        """Return redacted measurements only; never emit messages or note content."""
        return {"passed": self.passed, "cases": [asdict(case) for case in self.cases]}


async def run_baseline(*, memory_root: Path | None = None) -> BaselineReport:
    """Run each synthetic scenario once against normal Psyclaw model settings.

    ``memory_root`` is intentionally rejected.  This public harness is not a
    migration or diagnostic tool for real records: every invocation gets a
    fresh temporary workspace.
    """
    if memory_root is not None:
        raise ValueError("The memory baseline only accepts its temporary workspace.")

    with tempfile.TemporaryDirectory(prefix="psyclaw-memory-baseline-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        cases = []
        for scenario in SCENARIOS:
            cases.append(await _run_scenario(scenario, temporary_root / scenario.identifier))
    return BaselineReport(cases=tuple(cases))


async def _run_scenario(scenario: BaselineScenario, user_directory: Path) -> BaselineCaseResult:
    """Run one isolated synthetic session and retain only redacted measurements."""
    with patch.object(user_tools, "USER_DIRECTORY", user_directory):
        user_tools._initialise_user_workspace()
        memory_directory = user_tools._memory_root()
        if scenario.seed_path and scenario.seed_text:
            seeded_file = memory_directory / scenario.seed_path
            seeded_file.write_text(
                seeded_file.read_text(encoding="utf-8") + scenario.seed_text,
                encoding="utf-8",
            )
        before = _memory_snapshot(memory_directory)

        # Import only inside the temporary-root patch: psyclaw.agent builds its
        # module-level ADK app, whose MCP toolset must also be temporary.
        root_agent = _create_configured_root_agent()
        app = App(name=f"memory_baseline_{scenario.identifier}", root_agent=root_agent)
        sessions = InMemorySessionService()
        session_id = f"baseline-{scenario.identifier}"
        await sessions.create_session(
            app_name=app.name,
            user_id="synthetic-baseline-user",
            session_id=session_id,
        )
        runner = Runner(app=app, session_service=sessions)

        started = time.perf_counter()
        events = [
            event
            async for event in runner.run_async(
                user_id="synthetic-baseline-user",
                session_id=session_id,
                new_message=types.Content(
                    role="user", parts=[types.Part.from_text(text=scenario.message)]
                ),
            )
        ]
        latency_ms = round((time.perf_counter() - started) * 1000)
        after = _memory_snapshot(memory_directory)

    decision, behavior = _extract_agent_metrics(events, scenario)
    state = _measure_memory_state(scenario, before, after)
    return BaselineCaseResult(
        identifier=scenario.identifier,
        latency_ms=latency_ms,
        psychologist_decision=decision,
        note_taker_behavior=behavior,
        memory_state=state,
        token_usage_by_author=_extract_token_usage(events),
        passed=(
            decision.meets_expectation
            and behavior.meets_expectation
            and state.meets_expectation
        ),
    )


def _memory_snapshot(memory_directory: Path) -> dict[str, str]:
    return {
        path.relative_to(memory_directory).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(memory_directory.rglob("*.md"))
    }


def _create_configured_root_agent():
    """Build the normal provider-neutral agent only with complete configuration."""
    agent_module = importlib.import_module("psyclaw.agent")
    agent_module.load_chat_configuration(os.environ)
    agent_module.load_memory_configuration(os.environ)
    return agent_module.create_root_agent()


def _extract_agent_metrics(
    events: Iterable[Event], scenario: BaselineScenario
) -> tuple[PsychologistDecision, NoteTakerBehavior]:
    event_list = tuple(events)
    psychologist_calls = sum(
        call.name == NOTE_TAKER_NAME
        for event in event_list
        if event.author == PSYCHOLOGIST_NAME
        for call in event.get_function_calls()
    )
    filesystem_calls = tuple(
        call.name
        for event in event_list
        if event.author == NOTE_TAKER_NAME
        for call in event.get_function_calls()
    )
    mutating_calls = tuple(
        tool_name for tool_name in filesystem_calls if tool_name in MUTATING_MEMORY_TOOLS
    )
    no_op = not mutating_calls
    if scenario.expects_note_taker:
        decision_ok = psychologist_calls > 0
        behavior_ok = bool(mutating_calls) and "read_text_file" in filesystem_calls
    else:
        decision_ok = psychologist_calls == 0 or no_op
        behavior_ok = no_op

    return (
        PsychologistDecision(
            note_taker_calls=psychologist_calls, meets_expectation=decision_ok
        ),
        NoteTakerBehavior(
            filesystem_calls=filesystem_calls,
            mutating_calls=mutating_calls,
            no_op=no_op,
            meets_expectation=behavior_ok,
        ),
    )


def _measure_memory_state(
    scenario: BaselineScenario, before: dict[str, str], after: dict[str, str]
) -> MemoryState:
    changed_files = sum(
        before.get(path) != after.get(path) for path in before.keys() | after.keys()
    )
    contents = "\n".join(after.values()).lower()
    required_terms_present = all(term.lower() in contents for term in scenario.required_terms)
    forbidden_terms_absent = all(term.lower() not in contents for term in scenario.forbidden_terms)
    duplication_free = all(
        contents.count(term.lower()) <= maximum
        for term, maximum in scenario.max_occurrences
    )
    if scenario.expects_note_taker:
        meets_expectation = (
            changed_files > 0
            and required_terms_present
            and forbidden_terms_absent
            and duplication_free
        )
    else:
        meets_expectation = changed_files == 0
    return MemoryState(
        changed_files=changed_files,
        required_terms_present=required_terms_present,
        forbidden_terms_absent=forbidden_terms_absent,
        duplication_free=duplication_free,
        meets_expectation=meets_expectation,
    )


def _extract_token_usage(events: Iterable[Event]) -> dict[str, TokenUsage]:
    totals: dict[str, dict[str, int]] = {}
    for event in events:
        usage = event.usage_metadata
        if usage is None:
            continue
        author_totals = totals.setdefault(
            event.author,
            {
                "prompt_tokens": 0,
                "candidate_tokens": 0,
                "tool_use_prompt_tokens": 0,
                "thoughts_tokens": 0,
                "total_tokens": 0,
                "events_with_usage": 0,
            },
        )
        author_totals["prompt_tokens"] += _usage_value(usage, "prompt_token_count")
        author_totals["candidate_tokens"] += _usage_value(usage, "candidates_token_count")
        author_totals["tool_use_prompt_tokens"] += _usage_value(
            usage, "tool_use_prompt_token_count"
        )
        author_totals["thoughts_tokens"] += _usage_value(usage, "thoughts_token_count")
        author_totals["total_tokens"] += _usage_value(usage, "total_token_count")
        author_totals["events_with_usage"] += 1
    return {author: TokenUsage(**values) for author, values in totals.items()}


def _usage_value(usage: object, attribute: str) -> int:
    value = getattr(usage, attribute, None)
    return value if isinstance(value, int) else 0


def main() -> int:
    report = asyncio.run(run_baseline())
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
