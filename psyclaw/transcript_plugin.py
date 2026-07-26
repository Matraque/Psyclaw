"""Project generated ADK conversation events into private transcript storage.

Projection trims surrounding whitespace and excludes partial chunks, raw audio,
attachments, thoughts, tool arguments/results, and exception text. It records no
success terminal because ADK 2.5 cannot distinguish full consumption from early
generator close. ``on_event_callback`` runs before ADK session persistence and
yield, so records describe generated/attempted output and never prove delivery
to a patient. A future reconciliation design is required for delivery state.
Plugin ordering matters: this plugin observes values at its position in the
callback chain, so it must follow content-mutating plugins until ADK offers a
post-persistence observation contract.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any, Callable, NoReturn

from google.adk.plugins.base_plugin import BasePlugin

from psyclaw.transcript import SCHEMA_VERSION, TranscriptWriter, normalise_text, opaque_identifier, safe_tool_name, transcript_session_id, utc_now


MAX_DIAGNOSTICS = 32
MAX_CACHED_WRITERS = 64


class TranscriptCaptureError(RuntimeError):
    """Sanitized failure raised when complete transcript capture is unavailable."""


def fail_closed_persistence(error: Exception) -> NoReturn:
    """Apply the approved policy without exposing storage details."""
    raise TranscriptCaptureError("Transcript capture failed.") from None


class TranscriptPlugin(BasePlugin):
    """Capture generated conversation projections for one root-agent author."""

    def __init__(
        self,
        *,
        conversation_author: str,
        persistence_failure_strategy: Callable[[Exception], NoReturn],
        writer_factory: Callable[[str], TranscriptWriter] = TranscriptWriter,
    ) -> None:
        super().__init__(name="psyclaw_transcript")
        if safe_tool_name(conversation_author) != conversation_author:
            raise ValueError("Transcript conversation author is invalid.")
        self.conversation_author = conversation_author
        self._persistence_failure_strategy = persistence_failure_strategy
        self._writer_factory = writer_factory
        self._writers: OrderedDict[str, TranscriptWriter] = OrderedDict()
        self._writers_lock = threading.Lock()
        self.diagnostics: list[str] = []

    async def on_user_message_callback(self, *, invocation_context: Any, user_message: Any) -> None:
        text = _visible_content_text(user_message)
        if not text:
            return None
        invocation = self._required_identifier(getattr(invocation_context, "invocation_id", None), "iv")
        self._capture(invocation_context, _record(
            kind="user_message",
            status="received",
            dedupe_key=f"input:{invocation}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
            invocation_id=invocation,
            content={"input_modality": "typed", "role": "user", "text": text, "transcription": None},
        ))
        return None

    async def on_event_callback(self, *, invocation_context: Any, event: Any) -> None:
        if getattr(event, "partial", False):
            return None
        author = getattr(event, "author", None)
        input_text = _transcription_text(getattr(event, "input_transcription", None)) if author == "user" else ""
        root_event = author == self.conversation_author
        if not input_text and not root_event:
            self._diagnose("transcript_optional_event_skipped")
            return None

        event_id = self._required_identifier(getattr(event, "id", None), "ev")
        invocation = self._required_identifier(getattr(event, "invocation_id", None), "iv")
        records: list[dict[str, Any]] = []
        if input_text:
            records.append(_record(
                kind="user_message",
                status="received",
                dedupe_key=f"adk-event:{event_id}:input-transcription",
                invocation_id=invocation,
                event_id=event_id,
                content={
                    "input_modality": "speech_transcript",
                    "role": "user",
                    "text": input_text,
                    "transcription": {"language": "und", "source": "adk_input_transcription"},
                },
            ))

        if root_event:
            text = _visible_content_text(getattr(event, "content", None))
            output_text = _transcription_text(getattr(event, "output_transcription", None))
            transcription = not text and bool(output_text)
            if transcription:
                text = output_text
            tool = self._safe_tool_metadata(event)
            if text:
                records.append(_record(
                    kind="assistant_message",
                    status="complete",
                    dedupe_key=f"adk-event:{event_id}:assistant",
                    invocation_id=invocation,
                    event_id=event_id,
                    content={
                        "input_modality": None,
                        "role": "assistant",
                        "text": text,
                        "transcription": {"language": "und", "source": "adk_output_transcription"} if transcription else None,
                    },
                    outcome={"error_category": None, "finish_reason": _safe_finish_reason(getattr(event, "finish_reason", None))},
                ))
            if tool is not None:
                records.append(_record(
                    kind="tool_event",
                    status="complete",
                    dedupe_key=f"adk-event:{event_id}:tool",
                    invocation_id=invocation,
                    event_id=event_id,
                    content=_empty_content(),
                    tool=tool,
                    outcome={"error_category": None, "finish_reason": _safe_finish_reason(getattr(event, "finish_reason", None))},
                ))
            error_category = "event_error" if getattr(event, "error_code", None) else ("interrupted" if getattr(event, "interrupted", False) else None)
            if error_category:
                records.append(_record(
                    kind="event",
                    status="failed",
                    dedupe_key=f"adk-event:{event_id}:failure",
                    invocation_id=invocation,
                    event_id=event_id,
                    content=_empty_content(),
                    outcome={"error_category": error_category, "finish_reason": _safe_finish_reason(getattr(event, "finish_reason", None))},
                ))
        if not records:
            self._diagnose("transcript_optional_event_skipped")
            return None
        self._capture_batch(invocation_context, records)
        return None

    async def on_run_error_callback(self, *, invocation_context: Any, error: Exception) -> None:
        self._capture_terminal_failure(invocation_context, "run_error")

    async def on_model_error_callback(self, *, callback_context: Any, llm_request: Any, error: Exception) -> None:
        if getattr(callback_context, "agent_name", None) != self.conversation_author:
            self._diagnose("transcript_internal_model_error_skipped")
            return None
        self._capture_terminal_failure(callback_context, "model_error")
        return None

    async def on_tool_error_callback(self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any, error: Exception) -> None:
        if getattr(tool_context, "agent_name", None) != self.conversation_author:
            self._diagnose("transcript_internal_tool_error_skipped")
            return None
        invocation = self._required_identifier(getattr(tool_context, "invocation_id", None), "iv")
        call_id = self._required_identifier(getattr(tool_context, "function_call_id", None), "fc")
        self._capture(tool_context, _record(
            kind="tool_error",
            status="failed",
            dedupe_key=f"tool-error:{invocation}:{call_id}",
            invocation_id=invocation,
            content=_empty_content(),
            tool={"calls": [{"function_call_id": call_id, "name": safe_tool_name(getattr(tool, "name", None)), "status": "failed"}]},
            outcome={"error_category": "tool_error", "finish_reason": None},
        ))
        return None

    def _capture_terminal_failure(self, context: Any, category: str) -> None:
        invocation = self._required_identifier(getattr(context, "invocation_id", None), "iv")
        self._capture(context, _record(
            kind="invocation_failed",
            status="failed",
            dedupe_key=f"terminal:{invocation}",
            invocation_id=invocation,
            content=_empty_content(),
            outcome={"error_category": category, "finish_reason": None},
        ))

    def _safe_tool_metadata(self, event: Any) -> dict[str, Any] | None:
        calls: list[dict[str, Any]] = []
        for part in getattr(getattr(event, "content", None), "parts", None) or []:
            for payload, status in ((getattr(part, "function_call", None), "requested"), (getattr(part, "function_response", None), "returned")):
                if payload is None:
                    continue
                call_id = self._required_identifier(getattr(payload, "id", None), "fc")
                calls.append({"function_call_id": call_id, "name": safe_tool_name(getattr(payload, "name", None)), "status": status})
        return {"calls": calls} if calls else None

    def _capture(self, context: Any, record: dict[str, Any]) -> None:
        self._capture_batch(context, [record])

    def _capture_batch(self, context: Any, records: list[dict[str, Any]]) -> None:
        try:
            self._writer_for(context).append_batch(records)
        except Exception as error:
            self._persistence_failure_strategy(error)
            raise TranscriptCaptureError("Transcript capture failed.") from None

    def _writer_for(self, context: Any) -> TranscriptWriter:
        try:
            session_id = transcript_session_id(getattr(getattr(context, "session", None), "id", None))
        except Exception:
            raise TranscriptCaptureError("Transcript capture requires valid ADK identifiers.") from None
        with self._writers_lock:
            writer = self._writers.get(session_id)
            if writer is None:
                writer = self._writer_factory(session_id)
                self._writers[session_id] = writer
                if len(self._writers) > MAX_CACHED_WRITERS:
                    self._writers.popitem(last=False)
            else:
                self._writers.move_to_end(session_id)
            return writer

    def _required_identifier(self, value: object, prefix: str) -> str:
        try:
            return opaque_identifier(value, prefix=prefix)
        except Exception:
            raise TranscriptCaptureError("Transcript capture requires valid ADK identifiers.") from None

    def _diagnose(self, code: str) -> None:
        if len(self.diagnostics) < MAX_DIAGNOSTICS:
            self.diagnostics.append(code)


def _record(
    *,
    kind: str,
    status: str,
    dedupe_key: str,
    invocation_id: str,
    content: dict[str, Any],
    event_id: str | None = None,
    tool: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adk": {"event_id": event_id, "invocation_id": invocation_id, "partial": False},
        "captured_at": utc_now(),
        "content": content,
        "dedupe_key": dedupe_key,
        "kind": kind,
        "outcome": outcome or {"error_category": None, "finish_reason": None},
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "tool": tool,
    }


def _empty_content() -> dict[str, Any]:
    return {"input_modality": None, "role": None, "text": None, "transcription": None}


def _visible_content_text(content: Any) -> str:
    fragments = [part.text for part in getattr(content, "parts", None) or [] if not getattr(part, "thought", False) and isinstance(getattr(part, "text", None), str)]
    return normalise_text("\n".join(fragments))


def _transcription_text(transcription: Any) -> str:
    value = getattr(transcription, "text", None)
    return normalise_text(value) if isinstance(value, str) else ""


def _safe_finish_reason(value: object) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", value)
    return name if isinstance(name, str) and len(name) <= 64 and name.replace("_", "").isalnum() else "unknown"
