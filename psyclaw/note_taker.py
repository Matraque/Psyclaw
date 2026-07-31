"""Specialist agent that consolidates durable user memory through FILES-I03."""

from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm

from psyclaw.filesystem_mcp import create_user_filesystem_toolset


NOTE_TAKER_INSTRUCTION = """You maintain the user's durable Markdown records.

The current conversation history is available to you. The tool request signals
the new learning to assess; use the history only to understand its context, not
to recreate or exhaustively summarize the conversation. Read the relevant
Markdown files before deciding whether to act.

Do nothing when there is no new durable information. Otherwise use only the
filesystem tools to add, correct, or update the useful Markdown records. Keep
notes concise. Clearly label reported facts, your observations, tentative
hypotheses, and important unknowns. Never invent a diagnosis or turn an
inference into a fact.

After changing a record, return exactly `Memory consolidated`. When no record
needs changing, return exactly `No durable memory update`.
"""


def create_note_taker(model: str | BaseLlm) -> Agent:
    """Create a single-turn specialist with the bounded FILES-I03 toolset."""
    return Agent(
        name="note_taker",
        model=model,
        description=(
            "Consolidates newly learned durable user memory from the current "
            "conversation."
        ),
        instruction=NOTE_TAKER_INSTRUCTION,
        mode="single_turn",
        include_contents="default",
        tools=[create_user_filesystem_toolset()],
    )
