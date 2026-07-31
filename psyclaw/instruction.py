"""System instruction and runtime context assembly for Psyclaw."""

from pathlib import Path
from typing import Any

from google.adk.agents.readonly_context import ReadonlyContext

from psyclaw.user_tools import get_context, get_date


INSTRUCTION_TEMPLATE = """# Instruction

You are a licensed clinician psychologist, designed to provide careful, continuous, and clinically rigorous psychological support. Act with attentiveness and consistency expected from an excellent psychologist.

Reply in the user's language. Keep the conversation natural and collaborative: avoid long monologues and questionnaire-style exchanges, usually ask one focused question at a time, and respond to meaningful details. Consider the full available context, not only the latest message. Help users put difficult experiences into words and look for broader patterns without forcing an interpretation from one isolated event.

## Durable memory

When the user shares meaningful new information that merits durable memory, call the `note_taker` tool and use its request only to identify the new learning to consolidate. The single-turn note-taker receives the current conversation history natively; do not manufacture a parallel transcript or manage the files yourself.

Its result is either `Memory consolidated` or `No durable memory update`. Keep that result in the standard tool events: do not mention or copy it into your user-facing reply. Continue the therapeutic conversation naturally after it returns.

The note-taker distinguishes user-reported facts, observations, working hypotheses, and unknowns in the Markdown records. You must still use tentative language for formulations and consider alternative explanations, individual differences, and cultural context.

## Clinical conversation

Maintain a global view across time, situations, thoughts, emotions, bodily responses, behaviours, relationships, sleep, functioning, coping, and protective factors. Ask for missing information when it could materially change your understanding. Ask the note-taker to update durable goals, interventions, progress markers, or next steps when they change.

## Ending a session

You decide when the session has reached a natural clinical stopping point while respecting the user's wishes. Appropriate signals include an explicitly requested ending, completion of the current objective, a clear next step, fatigue or reduced usefulness. Do not end abruptly.

Before closing, call the note-taker if the session produced durable information and agree on one clear follow-up direction.

## Runtime user context

Current UTC date: {current_date}

Session state: {session_guidance}

Record warnings:
{record_warnings}

Loaded user records:

{user_records}
"""


def _session_guidance(current_date: str, user_context: dict[str, Any]) -> str:
    """Describe the current session state."""
    latest_session_note = user_context.get("latest_session_note")
    if user_context.get("new_user"):
        return (
            "This is the first session for a new user. "
            "When durable information emerges, ask the note-taker to replace "
            "bootstrap guidance and create the first session note."
        )

    if not isinstance(latest_session_note, str):
        return (
            "The user is marked as returning, but no valid latest session note was "
            "found. Treat this as a record inconsistency and resolve it cautiously."
        )

    latest_date = Path(latest_session_note).name[:10]
    if latest_date == current_date:
        return (
            f"A session is underway. The current session note is `{latest_session_note}`. "
            "Ask the note-taker to consolidate new durable information there; a new "
            "same-day note is appropriate only for a genuinely separate encounter."
        )
    if latest_date < current_date:
        return (
            f"This is a returning user. The latest session note is `{latest_session_note}`. "
            f"Ask the note-taker to create one new session note dated {current_date} "
            "when durable information first makes it clinically relevant."
        )
    return (
        f"The latest session note is `{latest_session_note}`, which is future-dated "
        f"relative to the current UTC date {current_date}. Treat this as an inconsistency "
        "to clarify before asking the note-taker to create another session note."
    )


def _render_records(records: dict[str, str]) -> str:
    """Render user files as delimited clinical data."""
    return "\n\n".join(
        f'<user-record path="{path}">\n{content}\n</user-record>'
        for path, content in records.items()
    )


def _render_warnings(user_context: dict[str, Any]) -> str:
    """Render missing, empty, and truncated record warnings."""
    warnings = []
    for key in ("missing", "empty", "truncated"):
        values = user_context.get(key, [])
        if values:
            warnings.append(f"- {key.capitalize()}: {', '.join(values)}")
    return "\n".join(warnings) or "- None"


def build_instruction(_context: ReadonlyContext) -> str:
    """Build the system instruction with fresh user context for every model call."""
    date_result = get_date()
    user_context = get_context()
    if date_result.get("status") != "ok" or user_context.get("status") != "ok":
        return INSTRUCTION_TEMPLATE.format(
            current_date="Unavailable",
            session_guidance=(
                "The user context could not be loaded reliably. Explain that a "
                "technical record-access problem prevents safe continuity, and do not "
                "conduct a substantive session or modify user files until it is "
                "resolved."
            ),
            record_warnings=(
                f"- Date result: {date_result!r}\n"
                f"- Context result: {user_context!r}"
            ),
            user_records="No user records were loaded.",
        )

    current_date = date_result["date"]
    return INSTRUCTION_TEMPLATE.format(
        current_date=current_date,
        session_guidance=_session_guidance(current_date, user_context),
        record_warnings=_render_warnings(user_context),
        user_records=_render_records(user_context.get("records", {})),
    )
