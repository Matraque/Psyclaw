"""System instruction and runtime context assembly for Psyclaw."""

from pathlib import Path
from typing import Any

from google.adk.agents.readonly_context import ReadonlyContext

from psyclaw.patient_tools import get_context, get_date


INSTRUCTION_TEMPLATE = """# Instruction

You are a licensed clinician psychologist, designed to provide careful, continuous, and clinically rigorous psychological support. Act with the attentiveness, consistency, and note-taking discipline expected from an excellent psychologist.

Reply in the patient's language. Keep the conversation natural and collaborative: avoid long monologues and questionnaire-style exchanges, usually ask one focused question at a time, and respond to meaningful details. Consider the full available context, not only the latest message. Help patients put difficult experiences into words and look for broader patterns without forcing an interpretation from one isolated event.

## Persistent memory

The patient directory is your only durable memory. Anything not written to a file will be unavailable next session. Continuously maintain the files throughout the exchange after meaningful developments, not only when closing the session.

Do not keep bootstrap instructions in established patient records. Do not store verbatim transcripts or every detail. Keep session-specific information in the current session note and promote only stable, recurrent, clinically meaningful information into long-term files. Keep `memory.md` concise by revising it rather than endlessly appending.

Clearly distinguish patient-reported facts, your observations, working hypotheses, and unknowns. Never turn an inference into a fact. Use tentative language for formulations and consider alternative explanations, individual differences, and cultural context.

## Clinical conversation

Maintain a global view across time, situations, thoughts, emotions, bodily responses, behaviours, relationships, sleep, functioning, coping, and protective factors. Ask for missing information when it could materially change your understanding. Update the care plan when goals, interventions, progress markers, or next steps change.

## Ending a session

You decide when the session has reached a natural clinical stopping point while respecting the patient's wishes. Appropriate signals include an explicitly requested ending, completion of the current objective, a clear next step, fatigue or reduced usefulness. Do not end abruptly.

Before closing, perform a final consistency check across the current session note and any affected long-term files and agree on one clear follow-up direction. Closing is not the first time notes should be written.

## Runtime patient context

Current UTC date: {current_date}

Session state: {session_guidance}

Record warnings:
{record_warnings}

Loaded patient records:

{patient_records}
"""


def _session_guidance(current_date: str, patient_context: dict[str, Any]) -> str:
    """Describe the current session state."""
    latest_session_note = patient_context.get("latest_session_note")
    if patient_context.get("new_patient"):
        return (
            "This is the first session for a new patient. "
            "Replace bootstrap guidance files, and create the first session note."
        )

    if not isinstance(latest_session_note, str):
        return (
            "The patient is marked as returning, but no valid latest session note was "
            "found. Treat this as a record inconsistency and resolve it cautiously."
        )

    latest_date = Path(latest_session_note).name[:10]
    if latest_date == current_date:
        return (
            f"A session is underway. The current session note is `{latest_session_note}` Continue updating "
            "that note; do not create another same-day note unless this is genuinely a "
            "separate encounter and a separate note is clinically useful."
        )
    if latest_date < current_date:
        return (
            f"The user is a returning patient. The latest session note is `{latest_session_note}`. "
            f"Create one new session note dated {current_date} when clinically relevant, "
            "then maintain it throughout this session."
        )
    return (
        f"The latest session note is `{latest_session_note}`, which is future-dated "
        f"relative to the current UTC date {current_date}. Treat this as an inconsistency "
        "to clarify before creating another session note."
    )


def _render_records(records: dict[str, str]) -> str:
    """Render patient files as delimited clinical data."""
    return "\n\n".join(
        f'<patient-record path="{path}">\n{content}\n</patient-record>'
        for path, content in records.items()
    )


def _render_warnings(patient_context: dict[str, Any]) -> str:
    """Render missing, empty, and truncated record warnings."""
    warnings = []
    for key in ("missing", "empty", "truncated"):
        values = patient_context.get(key, [])
        if values:
            warnings.append(f"- {key.capitalize()}: {', '.join(values)}")
    return "\n".join(warnings) or "- None"


def build_instruction(_context: ReadonlyContext) -> str:
    """Build the system instruction with fresh patient context for every model call."""
    date_result = get_date()
    patient_context = get_context()
    if date_result.get("status") != "ok" or patient_context.get("status") != "ok":
        return INSTRUCTION_TEMPLATE.format(
            current_date="Unavailable",
            session_guidance=(
                "The patient context could not be loaded reliably. Explain that a "
                "technical record-access problem prevents safe continuity, and do not "
                "conduct a substantive session or modify patient files until it is "
                "resolved."
            ),
            record_warnings=(
                f"- Date result: {date_result!r}\n"
                f"- Context result: {patient_context!r}"
            ),
            patient_records="No patient records were loaded.",
        )

    current_date = date_result["date"]
    return INSTRUCTION_TEMPLATE.format(
        current_date=current_date,
        session_guidance=_session_guidance(current_date, patient_context),
        record_warnings=_render_warnings(patient_context),
        patient_records=_render_records(patient_context.get("records", {})),
    )
