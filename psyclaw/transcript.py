"""Private, versioned storage for Psyclaw conversation transcripts.

This implementation requires a POSIX local filesystem with advisory ``flock``
and uses structural, not cryptographic, integrity checks. Same-user filesystem
replacement cannot be made fully adversary-proof without a privileged service.
Logs are append-only and still require a future explicit rotation/retention
policy; no automatic deletion or compaction occurs here. Assistant ``complete``
means a non-partial generated projection, not persistence by ADK or delivery to
the patient; delivery reconciliation is intentionally deferred.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import threading
import unicodedata
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from psyclaw.patient_paths import configured_patient_root


SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 32 * 1024
MAX_SESSION_BYTES = 8 * 1024 * 1024
MAX_RECOVERY_BYTES = 64 * 1024
MAX_BATCH_RECORDS = 16
MAX_TEXT_CHARS = 24_000
MAX_DEDUPE_KEY_CHARS = 180
_SESSION_ID_PATTERN = re.compile(r"^tr_[a-f0-9]{32}$")
_RECORD_ID_PATTERN = re.compile(r"^rec_[a-f0-9]{32}$")
_BATCH_ID_PATTERN = re.compile(r"^bat_[a-f0-9]{32}$")
_RECOVERY_ID_PATTERN = re.compile(r"^rcv_[a-f0-9]{32}$")
_OPAQUE_ID_PATTERN = re.compile(r"^(?:ev|iv|fc)_[a-f0-9]{32}$")
_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
_DEDUPE_KEY_PATTERN = re.compile(r"^[a-z][A-Za-z0-9:_-]{0,179}$")
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class TranscriptError(ValueError):
    """Raised when transcript data cannot be safely persisted."""


class TranscriptCorruptionError(TranscriptError):
    """Raised when an existing transcript cannot be safely recovered."""


@dataclass(frozen=True)
class AppendResult:
    """The observable result of one append attempt."""

    status: str
    sequence: int | None = None


def normalise_text(value: str) -> str:
    """Produce a stable text representation without retaining binary data."""
    if not isinstance(value, str):
        raise TranscriptError("Transcript text must be a string.")
    return unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n").strip()


def opaque_identifier(value: object, *, prefix: str) -> str:
    """Return a bounded opaque correlation identifier without retaining input."""
    if not isinstance(value, str) or not value or len(value) > 256:
        raise TranscriptError("Transcript identity is invalid.")
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


def transcript_session_id(adk_session_id: object) -> str:
    """Map an external ADK session identity to a safe internal directory name."""
    return opaque_identifier(adk_session_id, prefix="tr")


def safe_tool_name(value: object) -> str:
    """Keep only a bounded tool name, never tool arguments or result content."""
    return value if isinstance(value, str) and _SAFE_TOOL_NAME.fullmatch(value) else "unknown"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _check_no_symlink(path: Path) -> None:
    """Reject a link in the configured root or Psyclaw-owned path."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        # macOS's system aliases are not patient-controlled redirections.
        if current.exists() and current.is_symlink() and current not in {Path("/var"), Path("/tmp")}:
            raise TranscriptError("Transcript storage cannot use symbolic links.")


def _create_directory(path: Path, *, private: bool) -> None:
    _check_no_symlink(path.parent)
    path.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    if path.is_symlink() or not path.is_dir():
        raise TranscriptError("Transcript storage directory is unsafe.")
    if private:
        os.chmod(path, 0o700)


def _validate_timestamp(value: object) -> None:
    if not isinstance(value, str) or not _TIMESTAMP_PATTERN.fullmatch(value):
        raise TranscriptError("Transcript timestamp is invalid.")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise TranscriptError("Transcript timestamp is invalid.") from exc


def _require_exact_keys(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise TranscriptError(f"Transcript {label} is invalid.")
    return value


class TranscriptWriter:
    """Append validated canonical records under the hidden private namespace."""

    def __init__(
        self,
        session_id: str,
        *,
        patient_root: Path | None = None,
        max_record_bytes: int = MAX_RECORD_BYTES,
        max_session_bytes: int = MAX_SESSION_BYTES,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
            raise TranscriptError("Transcript session identity is invalid.")
        if os.name != "posix":
            raise TranscriptError("Transcript storage requires a POSIX local filesystem.")
        if not isinstance(max_record_bytes, int) or not isinstance(max_session_bytes, int) or max_record_bytes < 1 or max_session_bytes < max_record_bytes:
            raise TranscriptError("Transcript size policy is invalid.")
        self.session_id = session_id
        configured_root = (patient_root or configured_patient_root()).expanduser()
        self.patient_root = Path(os.path.abspath(os.fspath(configured_root)))
        self.max_record_bytes = max_record_bytes
        self.max_session_bytes = max_session_bytes
        self.failure_injector = failure_injector
        self._dedupe_keys: set[str] = set()
        self._dedupe_groups: set[frozenset[str]] = set()
        self._last_sequence = 0
        self._cache_signature: tuple[object, object] | None = None
        self._full_recovery_count = 0

    @property
    def directory(self) -> Path:
        return self.patient_root / ".transcripts" / "v1" / "sessions" / self.session_id

    @property
    def events_path(self) -> Path:
        return self.directory / "events.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.directory / "manifest.json"

    @property
    def recovery_path(self) -> Path:
        return self.directory / "recovery.jsonl"

    def prepare(self) -> None:
        """Create the private namespace and recover while holding both locks."""
        self._ensure_layout()
        with self._in_process_lock():
            with self._file_lock():
                self._prepare_locked()

    def append(self, record: Mapping[str, Any]) -> AppendResult:
        """Durably append one valid record, or report an existing dedupe key."""
        return self.append_batch([record])

    def append_batch(self, records: list[Mapping[str, Any]]) -> AppendResult:
        """Atomically append every projection from one source event.

        Multi-record batches are encoded as one JSONL envelope, so recovery
        observes either the whole source event or no source-event records.
        """
        if not isinstance(records, list) or not records or len(records) > MAX_BATCH_RECORDS:
            raise TranscriptError("Transcript batch is invalid.")
        validated_records = [self._validate_record(record, stored=False) for record in records]
        input_keys = [record["dedupe_key"] for record in validated_records]
        if len(set(input_keys)) != len(input_keys):
            raise TranscriptError("Transcript batch contains duplicate dedupe keys.")
        if len(validated_records) > 1:
            event_ids = {record["adk"]["event_id"] for record in validated_records}
            if len(event_ids) != 1 or None in event_ids:
                raise TranscriptError("Transcript batch must project one ADK event.")
        self._ensure_layout()
        with self._in_process_lock():
            with self._file_lock():
                self._prepare_locked(create_manifest=False)
                dedupe_group = frozenset(input_keys)
                existing = [key in self._dedupe_keys for key in input_keys]
                if dedupe_group in self._dedupe_groups:
                    return AppendResult("duplicate")
                if any(existing):
                    raise TranscriptCorruptionError("Transcript batch overlaps existing records.")
                stored_records: list[dict[str, Any]] = []
                for offset, validated in enumerate(validated_records, start=1):
                    stored = dict(validated)
                    stored["sequence"] = self._last_sequence + offset
                    stored["record_id"] = f"rec_{uuid.uuid4().hex}"
                    stored_records.append(self._validate_record(stored, stored=True))
                encoded = self._encode_storage_batch(stored_records)
                current_size = self._safe_file_size(self.events_path)
                if current_size + len(encoded) > self.max_session_bytes:
                    raise TranscriptError("Transcript session quota exceeded.")
                self._inject("before_append")
                self._append_bytes(self.events_path, encoded)
                self._inject("after_append")
                self._dedupe_keys.update(input_keys)
                self._dedupe_groups.add(dedupe_group)
                self._last_sequence = stored_records[-1]["sequence"]
                self._write_manifest(last_record_id=stored_records[-1]["record_id"])
                self._refresh_cache_signature()
                self._inject("after_manifest")
                return AppendResult("appended", stored_records[-1]["sequence"])

    def records(self) -> list[dict[str, Any]]:
        """Read validated records in append order, rejecting future schemas."""
        self._ensure_layout()
        with self._in_process_lock():
            with self._file_lock():
                self._prepare_locked()
                return self._read_events()

    def _ensure_layout(self) -> None:
        _check_no_symlink(self.patient_root)
        # The configured patient root is not Psyclaw-owned: never chmod it.
        _create_directory(self.patient_root, private=False)
        _create_directory(self.patient_root / ".transcripts", private=True)
        _create_directory(self.patient_root / ".transcripts" / "v1", private=True)
        _create_directory(self.patient_root / ".transcripts" / "v1" / "sessions", private=True)
        _create_directory(self.directory, private=True)

    def _prepare_locked(self, *, create_manifest: bool = True) -> None:
        if self._cache_is_current():
            return
        self._recover_events()
        if create_manifest and not self._path_exists(self.manifest_path):
            self._write_manifest()
        self._refresh_cache_signature()

    def _in_process_lock(self) -> threading.RLock:
        with _LOCKS_GUARD:
            return _LOCKS.setdefault(str(self.directory), threading.RLock())

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        import fcntl

        descriptor = self._open_append(self.directory / "writer.lock")
        replacement_detected = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._assert_descriptor_matches_path(descriptor, self.directory / "writer.lock")
            yield
        finally:
            try:
                self._assert_descriptor_matches_path(descriptor, self.directory / "writer.lock")
            except TranscriptError:
                replacement_detected = True
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            if replacement_detected:
                raise TranscriptError("Transcript lock file was replaced.")

    def _recover_events(self) -> None:
        self._full_recovery_count += 1
        pending_recovery = self._validate_recovery_log()
        if not self._path_exists(self.events_path):
            if pending_recovery is not None:
                if pending_recovery["valid_length"] != 0:
                    raise TranscriptCorruptionError("Transcript recovery intent contradicts the event log.")
                self._append_recovery_marker(
                    recovery_id=pending_recovery["recovery_id"],
                    state="resolved",
                    valid_length=0,
                )
            self._dedupe_keys = set()
            self._dedupe_groups = set()
            self._last_sequence = 0
            manifest = self._read_manifest_if_present()
            if manifest is not None and (manifest["last_committed_sequence"] != 0 or manifest["last_record_id"] is not None):
                raise TranscriptCorruptionError("Transcript manifest has no matching event log.")
            return
        raw = self._read_bounded(self.events_path, self.max_session_bytes)
        lines = raw.splitlines(keepends=True)
        truncated = bool(lines and not lines[-1].endswith(b"\n"))
        complete_lines = lines[:-1] if truncated else lines
        records: list[dict[str, Any]] = []
        dedupe_groups: set[frozenset[str]] = set()
        commit_boundaries: dict[int, str | None] = {0: None}
        expected_sequence = 1
        for index, line in enumerate(complete_lines, start=1):
            if len(line) > self.max_record_bytes * MAX_BATCH_RECORDS:
                raise TranscriptCorruptionError("Transcript storage batch exceeds the size limit.")
            try:
                line_records = self._decode_storage_line(line)
            except (UnicodeDecodeError, json.JSONDecodeError, TranscriptError) as exc:
                raise TranscriptCorruptionError(f"Transcript corruption at record {index}.") from exc
            for record in line_records:
                if record["sequence"] != expected_sequence:
                    raise TranscriptCorruptionError("Transcript sequence is not monotonic.")
                expected_sequence += 1
                records.append(record)
            dedupe_groups.add(frozenset(record["dedupe_key"] for record in line_records))
            commit_boundaries[records[-1]["sequence"]] = records[-1]["record_id"]
        self._dedupe_keys = {record["dedupe_key"] for record in records}
        self._dedupe_groups = dedupe_groups
        if len(self._dedupe_keys) != len(records):
            raise TranscriptCorruptionError("Transcript contains duplicate dedupe keys.")
        if truncated:
            valid_length = sum(len(line) for line in complete_lines)
            if pending_recovery is None:
                recovery_id = f"rcv_{uuid.uuid4().hex}"
                self._append_recovery_marker(
                    recovery_id=recovery_id,
                    state="intent",
                    valid_length=valid_length,
                )
            else:
                recovery_id = pending_recovery["recovery_id"]
                if pending_recovery["valid_length"] != valid_length:
                    raise TranscriptCorruptionError("Transcript recovery intent contradicts the event log.")
            self._inject("after_recovery_intent")
            self._truncate_events(valid_length)
            self._inject("after_recovery_truncate")
            self._append_recovery_marker(
                recovery_id=recovery_id,
                state="resolved",
                valid_length=valid_length,
            )
        elif pending_recovery is not None:
            if len(raw) != pending_recovery["valid_length"]:
                raise TranscriptCorruptionError("Transcript recovery intent contradicts the event log.")
            self._append_recovery_marker(
                recovery_id=pending_recovery["recovery_id"],
                state="resolved",
                valid_length=pending_recovery["valid_length"],
            )
        self._last_sequence = len(records)
        manifest = self._read_manifest_if_present()
        actual_last_record_id = records[-1]["record_id"] if records else None
        if manifest is None:
            self._write_manifest(last_record_id=actual_last_record_id)
        elif manifest["last_committed_sequence"] > self._last_sequence:
            raise TranscriptCorruptionError("Transcript manifest is ahead of the event log.")
        elif manifest["last_committed_sequence"] == self._last_sequence:
            if manifest["last_record_id"] != actual_last_record_id:
                raise TranscriptCorruptionError("Transcript manifest contradicts the event log.")
        else:
            committed = manifest["last_committed_sequence"]
            expected_committed_id = commit_boundaries.get(committed, "not-a-boundary")
            if manifest["last_record_id"] != expected_committed_id:
                raise TranscriptCorruptionError("Transcript manifest lag is contradictory.")
            self._write_manifest(last_record_id=records[-1]["record_id"] if records else None)

    def _read_events(self) -> list[dict[str, Any]]:
        if not self._path_exists(self.events_path):
            return []
        records: list[dict[str, Any]] = []
        for line in self._read_bounded(self.events_path, self.max_session_bytes).splitlines():
            records.extend(self._decode_storage_line(line))
        return records

    def _encode_storage_batch(self, records: list[dict[str, Any]]) -> bytes:
        if len(records) == 1:
            return self._encode(records[0])
        envelope = {
            "batch_id": f"bat_{uuid.uuid4().hex}",
            "records": records,
            "schema_version": SCHEMA_VERSION,
        }
        encoded = self._encode(envelope)
        if len(encoded) > self.max_record_bytes * MAX_BATCH_RECORDS:
            raise TranscriptError("Transcript storage batch exceeds the size limit.")
        return encoded

    def _decode_storage_line(self, line: bytes) -> list[dict[str, Any]]:
        value = json.loads(line.decode("utf-8"))
        if isinstance(value, dict) and "batch_id" in value:
            envelope = _require_exact_keys(value, {"batch_id", "records", "schema_version"}, "storage batch")
            if envelope["schema_version"] != SCHEMA_VERSION:
                raise TranscriptError("Transcript storage batch schema is invalid.")
            if not isinstance(envelope["batch_id"], str) or not _BATCH_ID_PATTERN.fullmatch(envelope["batch_id"]):
                raise TranscriptError("Transcript storage batch identity is invalid.")
            if not isinstance(envelope["records"], list) or not 2 <= len(envelope["records"]) <= MAX_BATCH_RECORDS:
                raise TranscriptError("Transcript storage batch records are invalid.")
            records = [self._validate_record(record, stored=True) for record in envelope["records"]]
            event_ids = {record["adk"]["event_id"] for record in records}
            if len(event_ids) != 1 or None in event_ids:
                raise TranscriptError("Transcript storage batch must project one ADK event.")
            dedupe_keys = [record["dedupe_key"] for record in records]
            if len(set(dedupe_keys)) != len(dedupe_keys):
                raise TranscriptError("Transcript storage batch has duplicate dedupe keys.")
            return records
        return [self._validate_record(value, stored=True)]

    def _read_manifest_if_present(self) -> dict[str, Any] | None:
        if not self._path_exists(self.manifest_path):
            return None
        try:
            manifest = json.loads(self._read_bounded(self.manifest_path, self.max_record_bytes).decode("utf-8"))
            return self._validate_manifest(manifest)
        except (UnicodeDecodeError, json.JSONDecodeError, TranscriptError) as exc:
            raise TranscriptCorruptionError("Transcript manifest is invalid.") from exc

    def _validate_manifest(self, value: object) -> dict[str, Any]:
        manifest = _require_exact_keys(value, {"created_at", "integrity", "last_committed_sequence", "last_record_id", "schema_version", "transcript_id"}, "manifest")
        if manifest["schema_version"] != SCHEMA_VERSION or manifest["transcript_id"] != self.session_id:
            raise TranscriptError("Transcript manifest schema or identity is invalid.")
        _validate_timestamp(manifest["created_at"])
        if type(manifest["last_committed_sequence"]) is not int or manifest["last_committed_sequence"] < 0:
            raise TranscriptError("Transcript manifest sequence is invalid.")
        if manifest["last_record_id"] is not None and (not isinstance(manifest["last_record_id"], str) or not _RECORD_ID_PATTERN.fullmatch(manifest["last_record_id"])):
            raise TranscriptError("Transcript manifest record identity is invalid.")
        integrity = _require_exact_keys(manifest["integrity"], {"last_verified_sequence", "status"}, "manifest integrity")
        if integrity["status"] != "ok" or integrity["last_verified_sequence"] != manifest["last_committed_sequence"]:
            raise TranscriptError("Transcript manifest integrity is invalid.")
        return manifest

    def _write_manifest(self, *, last_record_id: str | None = None) -> None:
        existing = self._read_manifest_if_present()
        manifest = {
            "created_at": existing["created_at"] if existing else utc_now(),
            "integrity": {"last_verified_sequence": self._last_sequence, "status": "ok"},
            "last_committed_sequence": self._last_sequence,
            "last_record_id": last_record_id,
            "schema_version": SCHEMA_VERSION,
            "transcript_id": self.session_id,
        }
        self._atomic_json_write(self.manifest_path, self._validate_manifest(manifest))

    def _append_recovery_marker(self, *, recovery_id: str, state: str, valid_length: int) -> None:
        marker = {
            "captured_at": utc_now(),
            "reason": "truncated_final_record",
            "recovery_id": recovery_id,
            "schema_version": SCHEMA_VERSION,
            "state": state,
            "valid_length": valid_length,
        }
        self._validate_recovery_marker(marker)
        encoded = self._encode(marker)
        if self._safe_file_size(self.recovery_path) + len(encoded) > MAX_RECOVERY_BYTES:
            raise TranscriptCorruptionError("Transcript recovery log exceeds the size limit.")
        self._append_bytes(self.recovery_path, encoded)

    def _validate_recovery_marker(self, value: object) -> dict[str, Any]:
        marker = _require_exact_keys(
            value,
            {"captured_at", "reason", "recovery_id", "schema_version", "state", "valid_length"},
            "recovery marker",
        )
        if marker["schema_version"] != SCHEMA_VERSION or marker["reason"] != "truncated_final_record":
            raise TranscriptError("Transcript recovery marker is invalid.")
        if not isinstance(marker["recovery_id"], str) or not _RECOVERY_ID_PATTERN.fullmatch(marker["recovery_id"]):
            raise TranscriptError("Transcript recovery marker identity is invalid.")
        if marker["state"] not in {"intent", "resolved"} or type(marker["valid_length"]) is not int or marker["valid_length"] < 0:
            raise TranscriptError("Transcript recovery marker state is invalid.")
        _validate_timestamp(marker["captured_at"])
        return marker

    def _validate_recovery_log(self) -> dict[str, Any] | None:
        if not self._path_exists(self.recovery_path):
            return None
        raw = self._read_bounded(self.recovery_path, MAX_RECOVERY_BYTES)
        lines = raw.splitlines(keepends=True)
        truncated = bool(lines and not lines[-1].endswith(b"\n"))
        complete_lines = lines[:-1] if truncated else lines
        pending: dict[str, Any] | None = None
        for line in complete_lines:
            try:
                marker = self._validate_recovery_marker(json.loads(line.decode("utf-8")))
                if marker["state"] == "intent":
                    if pending is not None:
                        raise TranscriptError("Transcript recovery intents overlap.")
                    pending = marker
                elif pending is None or marker["recovery_id"] != pending["recovery_id"] or marker["valid_length"] != pending["valid_length"]:
                    raise TranscriptError("Transcript recovery resolution has no matching intent.")
                else:
                    pending = None
            except (UnicodeDecodeError, json.JSONDecodeError, TranscriptError) as exc:
                raise TranscriptCorruptionError("Transcript recovery log is invalid.") from exc
        if truncated:
            # A marker is one physical JSONL line. A missing newline proves
            # that the final intent/resolution never completed; discard only
            # that tail before any event-log mutation.
            self._truncate_recovery(sum(len(line) for line in complete_lines))
        return pending

    def _atomic_json_write(self, path: Path, value: Mapping[str, Any]) -> None:
        encoded = self._encode(value)
        if len(encoded) > self.max_record_bytes:
            raise TranscriptError("Transcript manifest exceeds the size limit.")
        if self._path_exists(path):
            self._assert_regular(path)
        directory_descriptor = self._open_directory(path.parent)
        temporary_name = f".manifest-{uuid.uuid4().hex}"
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                0o600,
                dir_fd=directory_descriptor,
            )
            with os.fdopen(descriptor, "wb") as temporary_file:
                os.fchmod(temporary_file.fileno(), 0o600)
                temporary_file.write(encoded)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            # os.replace replaces a raced symlink rather than following it.
            os.replace(temporary_name, path.name, src_dir_fd=directory_descriptor, dst_dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
        finally:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            finally:
                os.close(directory_descriptor)

    def _validate_record(self, record: Mapping[str, Any], *, stored: bool) -> dict[str, Any]:
        keys = {"adk", "captured_at", "content", "dedupe_key", "kind", "outcome", "schema_version", "status", "tool"}
        if stored:
            keys |= {"record_id", "sequence"}
        value = _require_exact_keys(record, keys, "record")
        if value["schema_version"] != SCHEMA_VERSION or value["kind"] not in {"user_message", "assistant_message", "tool_event", "tool_error", "event", "invocation_failed"}:
            raise TranscriptError("Transcript record kind is invalid.")
        expected_status = {
            "user_message": "received",
            "assistant_message": "complete",
            "tool_event": "complete",
            "tool_error": "failed",
            "event": "failed",
            "invocation_failed": "failed",
        }[value["kind"]]
        if value["status"] != expected_status:
            raise TranscriptError("Transcript record status is invalid.")
        if not isinstance(value["dedupe_key"], str) or not _DEDUPE_KEY_PATTERN.fullmatch(value["dedupe_key"]):
            raise TranscriptError("Transcript dedupe key is invalid.")
        _validate_timestamp(value["captured_at"])
        self._validate_content(value["content"], kind=value["kind"])
        self._validate_adk(value["adk"], kind=value["kind"], input_modality=value["content"]["input_modality"])
        self._validate_tool(value["tool"], kind=value["kind"])
        self._validate_outcome(value["outcome"], kind=value["kind"], status=value["status"])
        if stored:
            if type(value["sequence"]) is not int or value["sequence"] < 1:
                raise TranscriptError("Transcript sequence is invalid.")
            if not isinstance(value["record_id"], str) or not _RECORD_ID_PATTERN.fullmatch(value["record_id"]):
                raise TranscriptError("Transcript record identity is invalid.")
        if len(self._encode(value)) > self.max_record_bytes:
            raise TranscriptError("Transcript record exceeds the size limit.")
        return value

    def _validate_adk(self, value: object, *, kind: str, input_modality: object) -> None:
        adk = _require_exact_keys(value, {"event_id", "invocation_id", "partial"}, "ADK metadata")
        if adk["event_id"] is not None and (not isinstance(adk["event_id"], str) or not _OPAQUE_ID_PATTERN.fullmatch(adk["event_id"]) or not adk["event_id"].startswith("ev_")):
            raise TranscriptError("Transcript event identity is invalid.")
        if not isinstance(adk["invocation_id"], str) or not _OPAQUE_ID_PATTERN.fullmatch(adk["invocation_id"]) or not adk["invocation_id"].startswith("iv_") or adk["partial"] is not False:
            raise TranscriptError("Transcript invocation metadata is invalid.")
        event_id_required = kind in {"assistant_message", "tool_event", "event"} or (kind == "user_message" and input_modality == "speech_transcript")
        if event_id_required != (adk["event_id"] is not None):
            raise TranscriptError("Transcript event identity does not match the record kind.")

    def _validate_content(self, value: object, *, kind: str) -> None:
        content = _require_exact_keys(value, {"input_modality", "role", "text", "transcription"}, "content")
        role = content["role"]
        text = content["text"]
        if text is not None and (not isinstance(text, str) or len(text) > MAX_TEXT_CHARS):
            raise TranscriptError("Transcript content text is invalid.")
        if kind == "user_message":
            if role != "user" or not text or content["input_modality"] not in {"typed", "speech_transcript"}:
                raise TranscriptError("Transcript user content is invalid.")
            if content["input_modality"] == "typed" and content["transcription"] is not None:
                raise TranscriptError("Transcript typed content is invalid.")
            if content["input_modality"] == "speech_transcript":
                transcription = _require_exact_keys(content["transcription"], {"language", "source"}, "transcription")
                if transcription != {"language": "und", "source": "adk_input_transcription"}:
                    raise TranscriptError("Transcript transcription is invalid.")
        elif kind == "assistant_message":
            if role != "assistant" or not text or content["input_modality"] is not None:
                raise TranscriptError("Transcript assistant content is invalid.")
            if content["transcription"] is not None:
                transcription = _require_exact_keys(content["transcription"], {"language", "source"}, "transcription")
                if transcription != {"language": "und", "source": "adk_output_transcription"}:
                    raise TranscriptError("Transcript transcription is invalid.")
        elif role is not None or text is not None or content["input_modality"] is not None or content["transcription"] is not None:
            raise TranscriptError("Transcript metadata content is invalid.")

    def _validate_tool(self, value: object, *, kind: str) -> None:
        if kind not in {"tool_event", "tool_error"}:
            if value is not None:
                raise TranscriptError("Transcript tool metadata is invalid.")
            return
        tool = _require_exact_keys(value, {"calls"}, "tool metadata")
        if not isinstance(tool["calls"], list) or not tool["calls"] or len(tool["calls"]) > 16:
            raise TranscriptError("Transcript tool calls are invalid.")
        allowed_statuses = {"requested", "returned"} if kind == "tool_event" else {"failed"}
        for call in tool["calls"]:
            call = _require_exact_keys(call, {"function_call_id", "name", "status"}, "tool call")
            if not isinstance(call["function_call_id"], str) or not _OPAQUE_ID_PATTERN.fullmatch(call["function_call_id"]) or not call["function_call_id"].startswith("fc_"):
                raise TranscriptError("Transcript tool call identity is invalid.")
            if not isinstance(call["name"], str) or not _SAFE_TOOL_NAME.fullmatch(call["name"]) or call["status"] not in allowed_statuses:
                raise TranscriptError("Transcript tool call metadata is invalid.")

    def _validate_outcome(self, value: object, *, kind: str, status: str) -> None:
        outcome = _require_exact_keys(value, {"error_category", "finish_reason"}, "outcome")
        finish_reason = outcome["finish_reason"]
        if finish_reason is not None and (not isinstance(finish_reason, str) or len(finish_reason) > 64 or not finish_reason.replace("_", "").isalnum()):
            raise TranscriptError("Transcript finish reason is invalid.")
        expected_error = {
            "invocation_failed": {"model_error", "run_error"},
            "tool_error": {"tool_error"},
            "event": {"event_error", "interrupted"},
        }.get(kind, {None})
        if outcome["error_category"] not in expected_error:
            raise TranscriptError("Transcript error category is invalid.")
        if status == "complete" and outcome["error_category"] is not None:
            raise TranscriptError("Transcript complete outcome is invalid.")
        if status == "failed" and outcome["error_category"] is None:
            raise TranscriptError("Transcript failed outcome is invalid.")
        if kind in {"user_message", "tool_error", "invocation_failed"} and finish_reason is not None:
            raise TranscriptError("Transcript finish reason is invalid for this record kind.")

    def _path_exists(self, path: Path) -> bool:
        try:
            os.lstat(path)
            return True
        except FileNotFoundError:
            return False

    def _file_signature(self, path: Path) -> object:
        if not self._path_exists(path):
            return None
        info = self._assert_regular(path)
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _refresh_cache_signature(self) -> None:
        self._cache_signature = (self._file_signature(self.events_path), self._file_signature(self.manifest_path))

    def _cache_is_current(self) -> bool:
        if self._cache_signature is None:
            return False
        return self._cache_signature == (self._file_signature(self.events_path), self._file_signature(self.manifest_path))

    def _assert_regular(self, path: Path) -> os.stat_result:
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            raise TranscriptError("Transcript file is missing.") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise TranscriptError("Transcript file is unsafe.")
        return info

    def _safe_file_size(self, path: Path) -> int:
        if not self._path_exists(path):
            return 0
        size = self._assert_regular(path).st_size
        if size > self.max_session_bytes:
            raise TranscriptCorruptionError("Transcript session exceeds the size limit.")
        return size

    def _read_bounded(self, path: Path, limit: int) -> bytes:
        size = self._safe_file_size(path)
        if size > limit:
            raise TranscriptCorruptionError("Transcript file exceeds the size limit.")
        parent_descriptor = self._open_directory(path.parent)
        try:
            descriptor = os.open(path.name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC, dir_fd=parent_descriptor)
        except OSError as exc:
            os.close(parent_descriptor)
            raise TranscriptCorruptionError("Transcript file cannot be opened safely.") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise TranscriptCorruptionError("Transcript file is unsafe or oversized.")
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > limit:
                raise TranscriptCorruptionError("Transcript file exceeds the size limit.")
            return data
        finally:
            os.close(descriptor)
            os.close(parent_descriptor)

    def _open_append(self, path: Path) -> int:
        parent_descriptor = self._open_directory(path.parent)
        try:
            for attempt in range(3):
                try:
                    descriptor = os.open(path.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW | _CLOEXEC, 0o600, dir_fd=parent_descriptor)
                    break
                except OSError as exc:
                    # Concurrent first-use creators can transiently observe
                    # ENOENT on macOS even though the anchored directory is
                    # still valid. Retry only that narrow, bounded case.
                    if exc.errno != errno.ENOENT or attempt == 2:
                        raise
        except OSError as exc:
            os.close(parent_descriptor)
            raise TranscriptError("Transcript file cannot be opened safely.") from exc
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            os.close(parent_descriptor)
            raise TranscriptError("Transcript file is unsafe.")
        os.fchmod(descriptor, 0o600)
        # The descriptor stays open; its parent can now be safely released.
        os.close(parent_descriptor)
        return descriptor

    def _assert_descriptor_matches_path(self, descriptor: int, path: Path) -> None:
        """Detect replacement of an opened file within the local threat model."""
        opened = os.fstat(descriptor)
        current = self._assert_regular(path)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise TranscriptError("Transcript file changed during use.")

    def _append_bytes(self, path: Path, data: bytes) -> None:
        descriptor = self._open_append(path)
        try:
            remaining = memoryview(data)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise TranscriptError("Transcript write did not complete.")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _truncate_events(self, length: int) -> None:
        parent_descriptor = self._open_directory(self.events_path.parent)
        try:
            descriptor = os.open(self.events_path.name, os.O_RDWR | _NOFOLLOW | _CLOEXEC, dir_fd=parent_descriptor)
        except OSError as exc:
            os.close(parent_descriptor)
            raise TranscriptCorruptionError("Transcript event log cannot be opened safely.") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise TranscriptCorruptionError("Transcript event log is unsafe.")
            os.ftruncate(descriptor, length)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            os.close(parent_descriptor)

    def _truncate_recovery(self, length: int) -> None:
        parent_descriptor = self._open_directory(self.recovery_path.parent)
        try:
            descriptor = os.open(self.recovery_path.name, os.O_RDWR | _NOFOLLOW | _CLOEXEC, dir_fd=parent_descriptor)
        except OSError as exc:
            os.close(parent_descriptor)
            raise TranscriptCorruptionError("Transcript recovery log cannot be opened safely.") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise TranscriptCorruptionError("Transcript recovery log is unsafe.")
            os.ftruncate(descriptor, length)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            os.close(parent_descriptor)

    def _open_directory(self, directory: Path) -> int:
        _check_no_symlink(directory)
        flags = os.O_RDONLY | _CLOEXEC | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
        try:
            descriptor = os.open(directory, flags)
        except OSError as exc:
            raise TranscriptError("Transcript directory cannot be opened safely.") from exc
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise TranscriptError("Transcript directory is unsafe.")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _encode(self, value: Mapping[str, Any]) -> bytes:
        try:
            return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise TranscriptError("Transcript data is not serializable.") from exc

    def _inject(self, point: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(point)
