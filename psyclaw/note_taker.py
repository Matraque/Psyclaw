"""Specialist agent that consolidates durable user memory through FILES-I03."""

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from psyclaw.filesystem_mcp import NOTE_TAKER_TOOL_ALLOWLIST, create_user_filesystem_toolset


NOTE_TAKER_INSTRUCTION = """You maintain the user's durable Markdown records.

The current conversation history is available to you. The tool request signals
the new learning to assess; use the history only to understand its context, not
to recreate or exhaustively summarize the conversation. Read the relevant
Markdown records before deciding whether to act.

Only after checking may you make a read-only no-op when there is no new durable
information. Otherwise use only the filesystem tools to write or edit the
useful Markdown records before reporting success. Keep notes concise. Clearly
label reported facts, your observations, tentative hypotheses, and important
unknowns. Never invent a diagnosis or turn an inference into a fact.
"""

_MUTATING_TOOLS = frozenset({"write_file", "edit_file"})
_READ_TOOLS = frozenset({"read_text_file"})
_MEMORY_TOOLS = frozenset(NOTE_TAKER_TOOL_ALLOWLIST)


def discard_unverified_prose(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse:
    """Leave tool calls intact; prevent a terminal model message becoming output."""
    del callback_context
    content = llm_response.content
    if content is None or any(part.function_call is not None for part in content.parts):
        return llm_response
    return llm_response.model_copy(update={"content": None})


def verify_consolidation(callback_context: CallbackContext) -> types.Content:
    """Replace the note-taker's prose with a verified, non-sensitive result."""
    events = tuple(
        event
        for event in callback_context.session.events
        if (
            event.author == "note_taker"
            and event.invocation_id == callback_context.invocation_id
            and event.branch == callback_context.branch
        )
    )
    responses = tuple(
        response
        for event in events
        for response in event.get_function_responses()
    )
    failed = any(
        response.name in _MEMORY_TOOLS
        and isinstance(response.response, dict)
        and bool(response.response.get("isError"))
        for response in responses
    )
    successful_tools = frozenset(
        response.name
        for response in responses
        if response.response is not None
        and not (
            isinstance(response.response, dict) and bool(response.response.get("isError"))
        )
    )
    if failed:
        result = "Memory consolidation failed: filesystem operation failed."
    elif successful_tools & _MUTATING_TOOLS and successful_tools & _READ_TOOLS:
        result = "Memory consolidated."
    elif successful_tools & _READ_TOOLS:
        result = "No durable memory update."
    else:
        result = "Memory consolidation failed: no filesystem operation."
    return types.Content(role="model", parts=[types.Part.from_text(text=result)])


def create_note_taker(model: str | BaseLlm) -> Agent:
    """Create the note-taker with filesystem toolset."""
    return Agent(
        name="note_taker",
        model=model,
        description=(
            "Consolidates newly learned durable user memory from the current conversation."
        ),
        instruction=NOTE_TAKER_INSTRUCTION,
        mode="single_turn",
        include_contents="default",
        tools=[create_user_filesystem_toolset()],
        after_model_callback=discard_unverified_prose,
        after_agent_callback=verify_consolidation,
    )
