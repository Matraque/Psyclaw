from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from psyclaw import memory_baseline
from psyclaw.memory_baseline import (
    BaselineScenario,
    _extract_agent_metrics,
    _extract_token_usage,
    _measure_memory_state,
    run_baseline,
)


class FakeEvent:
    def __init__(self, author, *, calls=(), usage_metadata=None):
        self.author = author
        self._calls = calls
        self.usage_metadata = usage_metadata

    def get_function_calls(self):
        return list(self._calls)


class MemoryBaselineTest(unittest.TestCase):
    def test_baseline_awaits_each_synthetic_scenario_without_running_models(self) -> None:
        scenarios = (
            BaselineScenario(identifier="first", message="synthetic"),
            BaselineScenario(identifier="second", message="synthetic"),
        )
        run_scenario = AsyncMock(return_value=SimpleNamespace(passed=True))

        with (
            patch.object(memory_baseline, "SCENARIOS", scenarios),
            patch.object(memory_baseline, "_run_scenario", run_scenario),
        ):
            report = asyncio.run(memory_baseline.run_baseline())

        self.assertEqual(report.cases, (run_scenario.return_value,) * 2)
        self.assertEqual(
            [call.args[0] for call in run_scenario.await_args_list], list(scenarios)
        )

    def test_durable_fact_separates_agent_and_memory_metrics(self) -> None:
        scenario = BaselineScenario(
            identifier="durable_fact", message="synthetic", required_terms=("botanist",)
        )
        events = (
            FakeEvent("psyclaw_agent", calls=(SimpleNamespace(name="note_taker"),)),
            FakeEvent(
                "note_taker",
                calls=(
                    SimpleNamespace(name="read_text_file"),
                    SimpleNamespace(name="write_file"),
                ),
            ),
        )

        decision, behavior = _extract_agent_metrics(events, scenario)

        self.assertEqual(decision.note_taker_calls, 1)
        self.assertTrue(decision.meets_expectation)
        self.assertEqual(behavior.filesystem_calls, ("read_text_file", "write_file"))
        self.assertEqual(behavior.mutating_calls, ("write_file",))
        self.assertTrue(behavior.meets_expectation)

    def test_greeting_accepts_a_counted_filesystem_no_op(self) -> None:
        scenario = BaselineScenario(
            identifier="greeting", message="synthetic", expects_note_taker=False
        )
        events = (
            FakeEvent("psyclaw_agent", calls=(SimpleNamespace(name="note_taker"),)),
            FakeEvent(
                "note_taker", calls=(SimpleNamespace(name="read_text_file"),)
            ),
        )

        decision, behavior = _extract_agent_metrics(events, scenario)

        self.assertEqual(decision.note_taker_calls, 1)
        self.assertTrue(decision.meets_expectation)
        self.assertTrue(behavior.no_op)
        self.assertTrue(behavior.meets_expectation)

    def test_correction_state_requires_removal_replacement_and_no_duplication(self) -> None:
        scenario = BaselineScenario(
            identifier="correction",
            message="synthetic",
            required_terms=("northwind", "left"),
            forbidden_terms=("user works at northwind",),
            max_occurrences=(("northwind", 1),),
        )
        before = {"user_profile.md": "- Reported fact: User works at Northwind.\n"}
        after = {"user_profile.md": "- Reported fact: User left Northwind.\n"}

        state = _measure_memory_state(scenario, before, after)

        self.assertEqual(state.changed_files, 1)
        self.assertTrue(state.required_terms_present)
        self.assertTrue(state.forbidden_terms_absent)
        self.assertTrue(state.duplication_free)
        self.assertTrue(state.meets_expectation)

    def test_token_usage_is_aggregated_by_event_author_when_available(self) -> None:
        usage = SimpleNamespace(
            prompt_token_count=7,
            candidates_token_count=3,
            tool_use_prompt_token_count=None,
            thoughts_token_count=2,
            total_token_count=12,
        )
        totals = _extract_token_usage((
            FakeEvent("psyclaw_agent", usage_metadata=usage),
            FakeEvent("psyclaw_agent", usage_metadata=usage),
            FakeEvent("note_taker"),
        ))

        self.assertEqual(totals["psyclaw_agent"].prompt_tokens, 14)
        self.assertEqual(totals["psyclaw_agent"].candidate_tokens, 6)
        self.assertEqual(totals["psyclaw_agent"].tool_use_prompt_tokens, 0)
        self.assertEqual(totals["psyclaw_agent"].thoughts_tokens, 4)
        self.assertEqual(totals["psyclaw_agent"].total_tokens, 24)
        self.assertEqual(totals["psyclaw_agent"].events_with_usage, 2)

    def test_real_memory_root_is_rejected_before_any_model_or_mcp_work(self) -> None:
        with self.assertRaisesRegex(ValueError, "temporary workspace"):
            asyncio.run(run_baseline(memory_root=Path(__file__)))


if __name__ == "__main__":
    unittest.main()
