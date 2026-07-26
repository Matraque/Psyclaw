import asyncio
import gc
import json
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google.adk.events import Event
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import Session
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from psyclaw.transcript import (
    MAX_RECOVERY_BYTES,
    SCHEMA_VERSION,
    TranscriptCorruptionError,
    TranscriptError,
    TranscriptWriter,
    normalise_text,
    transcript_session_id,
)
from psyclaw import transcript as transcript_module
from psyclaw.transcript_plugin import MAX_CACHED_WRITERS, TranscriptCaptureError, TranscriptPlugin, fail_closed_persistence


def record(key: str, *, kind: str = "user_message", status: str | None = None) -> dict:
    statuses = {"user_message": "received", "assistant_message": "complete", "tool_event": "complete", "tool_error": "failed", "event": "failed", "invocation_failed": "failed"}
    content = {"input_modality": "typed", "role": "user", "text": "synthetic", "transcription": None} if kind == "user_message" else {"input_modality": None, "role": None, "text": None, "transcription": None}
    if kind == "assistant_message":
        content = {"input_modality": None, "role": "assistant", "text": "synthetic", "transcription": None}
    tool = {"calls": [{"function_call_id": "fc_" + "b" * 32, "name": "read_file", "status": "returned"}]} if kind == "tool_event" else None
    error_category = {"tool_error": "tool_error", "event": "event_error", "invocation_failed": "run_error"}.get(kind)
    event_id = "ev_" + "c" * 32 if kind in {"assistant_message", "tool_event", "event"} else None
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "status": status or statuses[kind],
        "dedupe_key": key,
        "captured_at": "2026-07-25T10:00:00.000Z",
        "adk": {"event_id": event_id, "invocation_id": "iv_" + "a" * 32, "partial": False},
        "content": content,
        "tool": tool,
        "outcome": {"finish_reason": None, "error_category": error_category},
    }


def _process_append(patient_root: str, session_id: str, index: int) -> None:
    TranscriptWriter(session_id, patient_root=Path(patient_root)).append(record(f"process:{index}"))


def event_batch(first_key: str, second_key: str) -> list[dict]:
    return [record(first_key, kind="assistant_message"), record(second_key, kind="tool_event")]


class TranscriptWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "patient"
        self.session_id = transcript_session_id("synthetic-adk-session")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def writer(self, **kwargs) -> TranscriptWriter:
        return TranscriptWriter(self.session_id, patient_root=self.root, **kwargs)

    def test_hidden_namespace_append_order_and_deduplication(self) -> None:
        writer = self.writer()
        self.assertEqual(writer.append(record("event:one")).sequence, 1)
        self.assertEqual(writer.append(record("event:two")).sequence, 2)
        self.assertEqual(writer.append(record("event:one")).status, "duplicate")

        self.assertEqual([item["sequence"] for item in writer.records()], [1, 2])
        self.assertTrue(writer.events_path.is_file())
        self.assertTrue(writer.manifest_path.is_file())
        self.assertEqual(writer.events_path.parents[3].name, ".transcripts")
        self.assertEqual(os.stat(writer.events_path).st_mode & 0o077, 0)

    def test_batch_prevalidation_and_quota_fail_without_any_projection(self) -> None:
        invalid = record("invalid-batch-member")
        invalid["status"] = "complete"
        writer = self.writer()
        writer.prepare()
        with self.assertRaises(TranscriptError):
            writer.append_batch(event_batch("valid-batch-member", "second-valid-batch-member") + [invalid])
        self.assertEqual(writer.records(), [])
        self.assertFalse(writer.events_path.exists())

        constrained = TranscriptWriter(
            transcript_session_id("quota-batch"),
            patient_root=self.root,
            max_record_bytes=640,
            max_session_bytes=640,
        )
        with self.assertRaisesRegex(TranscriptError, "quota"):
            constrained.append_batch(event_batch("quota-one", "quota-two"))
        self.assertFalse(constrained.events_path.exists())
        self.assertFalse(constrained.manifest_path.exists())
        self.assertEqual(constrained.records(), [])

    def test_batch_is_one_storage_boundary_and_retry_is_idempotent(self) -> None:
        batch = event_batch("batch-one", "batch-two")

        def crash_after_append(point: str) -> None:
            if point == "after_append":
                raise RuntimeError("synthetic crash")

        crashing = self.writer(failure_injector=crash_after_append)
        with self.assertRaises(RuntimeError):
            crashing.append_batch(batch)
        self.assertEqual(len(crashing.events_path.read_text(encoding="utf-8").splitlines()), 1)

        restarted = self.writer()
        self.assertEqual([item["dedupe_key"] for item in restarted.records()], ["batch-one", "batch-two"])
        self.assertEqual(restarted.append_batch(batch).status, "duplicate")
        manifest = json.loads(restarted.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["last_committed_sequence"], 2)
        self.assertEqual(manifest["last_record_id"], restarted.records()[-1]["record_id"])

    def test_batch_failure_injection_before_write_leaves_no_projection(self) -> None:
        def fail_before_append(point: str) -> None:
            if point == "before_append":
                raise RuntimeError("synthetic pre-write failure")

        writer = self.writer(failure_injector=fail_before_append)
        with self.assertRaises(RuntimeError):
            writer.append_batch(event_batch("prewrite-one", "prewrite-two"))
        self.assertFalse(writer.events_path.exists())
        self.assertFalse(writer.manifest_path.exists())
        self.assertEqual(self.writer().records(), [])

    def test_batch_retry_requires_the_exact_original_group(self) -> None:
        writer = self.writer()
        writer.append(record("existing-assistant", kind="assistant_message"))
        writer.append(record("existing-tool", kind="tool_event"))
        with self.assertRaisesRegex(TranscriptCorruptionError, "overlaps"):
            writer.append_batch(event_batch("existing-assistant", "existing-tool"))

    def test_manifest_cannot_commit_inside_a_storage_batch(self) -> None:
        writer = self.writer()
        writer.append_batch(event_batch("boundary-one", "boundary-two"))
        stored = writer.records()
        manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
        manifest["last_committed_sequence"] = 1
        manifest["last_record_id"] = stored[0]["record_id"]
        manifest["integrity"]["last_verified_sequence"] = 1
        writer.manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(TranscriptCorruptionError, "lag is contradictory"):
            self.writer().records()

    def test_persisted_batch_revalidates_event_identity_and_dedupe_uniqueness(self) -> None:
        for name in ("mismatched-event", "duplicate-key"):
            writer = TranscriptWriter(transcript_session_id(name), patient_root=self.root)
            writer.append_batch(event_batch(f"{name}-one", f"{name}-two"))
            envelope = json.loads(writer.events_path.read_text(encoding="utf-8"))
            if name == "mismatched-event":
                envelope["records"][1]["adk"]["event_id"] = "ev_" + "e" * 32
            else:
                envelope["records"][1]["dedupe_key"] = envelope["records"][0]["dedupe_key"]
            raw = (json.dumps(envelope) + "\n").encode("utf-8")
            writer.events_path.write_bytes(raw)
            with self.subTest(name=name):
                with self.assertRaises(TranscriptCorruptionError):
                    TranscriptWriter(transcript_session_id(name), patient_root=self.root).records()
                self.assertEqual(writer.events_path.read_bytes(), raw)

    def test_partial_batch_write_recovers_none_then_retry_commits_all(self) -> None:
        writer = self.writer()
        writer.prepare()
        real_write = os.write
        calls = 0

        def crash_mid_batch(descriptor: int, data) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                chunk = bytes(data)
                return real_write(descriptor, chunk[: len(chunk) // 2])
            raise OSError("synthetic crash")

        batch = event_batch("partial-one", "partial-two")
        with patch("psyclaw.transcript.os.write", side_effect=crash_mid_batch):
            with self.assertRaises(OSError):
                writer.append_batch(batch)
        self.assertNotEqual(writer.events_path.read_bytes(), b"")

        restarted = self.writer()
        self.assertEqual(restarted.records(), [])
        self.assertEqual(restarted.append_batch(batch).sequence, 2)
        self.assertEqual([item["dedupe_key"] for item in restarted.records()], ["partial-one", "partial-two"])

    def test_invalid_identity_symlink_and_quota_are_rejected(self) -> None:
        with self.assertRaises(TranscriptError):
            TranscriptWriter("../../escape", patient_root=self.root)
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        self.root.parent.mkdir(exist_ok=True)
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(TranscriptError):
            self.writer().prepare()
        self.root.unlink()
        constrained = self.writer(max_record_bytes=512, max_session_bytes=512)
        constrained.append(record("fits-once"))
        with self.assertRaises(TranscriptError):
            constrained.append(record("too-large"))

    def test_configured_patient_root_is_the_only_storage_root(self) -> None:
        with patch.dict(os.environ, {"PSYCLAW_PATIENT_DIR": str(self.root)}):
            writer = TranscriptWriter(self.session_id)
            writer.append(record("configured-root"))
        self.assertTrue((self.root / ".transcripts" / "v1" / "sessions" / self.session_id).is_dir())

    def test_closed_schema_rejects_unknown_or_malformed_nested_values(self) -> None:
        invalid_records = []
        extra_top_level = record("extra-top")
        extra_top_level["unexpected"] = True
        invalid_records.append(extra_top_level)
        extra_adk = record("extra-adk")
        extra_adk["adk"]["unexpected"] = True
        invalid_records.append(extra_adk)
        invalid_content = record("extra-content")
        invalid_content["content"]["unexpected"] = True
        invalid_records.append(invalid_content)
        invalid_timestamp = record("invalid-time")
        invalid_timestamp["captured_at"] = "not-a-timestamp"
        invalid_records.append(invalid_timestamp)
        invalid_identifier = record("invalid-id")
        invalid_identifier["adk"]["invocation_id"] = "iv_not-hex"
        invalid_records.append(invalid_identifier)
        invalid_tool = record("invalid-tool", kind="tool_event")
        invalid_tool["tool"] = {"calls": [{"function_call_id": "fc_" + "a" * 32, "name": "read_file", "status": "returned", "unexpected": True}]}
        invalid_records.append(invalid_tool)
        for invalid in invalid_records:
            with self.subTest(invalid=invalid["dedupe_key"]):
                with self.assertRaises(TranscriptError):
                    self.writer().append(invalid)

    def test_kind_status_content_tool_and_outcome_combinations_are_exact(self) -> None:
        invalid_records = []
        wrong_status = record("wrong-status")
        wrong_status["status"] = "complete"
        invalid_records.append(wrong_status)
        assistant_with_user_content = record("wrong-assistant-content", kind="assistant_message")
        assistant_with_user_content["content"] = record("template")["content"]
        invalid_records.append(assistant_with_user_content)
        tool_without_calls = record("missing-tool", kind="tool_event")
        tool_without_calls["tool"] = None
        invalid_records.append(tool_without_calls)
        user_with_tool = record("user-tool")
        user_with_tool["tool"] = record("template-tool", kind="tool_event")["tool"]
        invalid_records.append(user_with_tool)
        success_with_error = record("success-error", kind="assistant_message")
        success_with_error["outcome"]["error_category"] = "event_error"
        invalid_records.append(success_with_error)
        failure_without_error = record("failure-no-error", kind="event")
        failure_without_error["outcome"]["error_category"] = None
        invalid_records.append(failure_without_error)
        typed_with_event_id = record("typed-event-id")
        typed_with_event_id["adk"]["event_id"] = "ev_" + "d" * 32
        invalid_records.append(typed_with_event_id)
        for invalid in invalid_records:
            with self.subTest(invalid=invalid["dedupe_key"]):
                with self.assertRaises(TranscriptError):
                    self.writer().append(invalid)

    def test_generated_fields_count_toward_record_limit(self) -> None:
        candidate = record("generated-size", kind="assistant_message")
        candidate["content"] = {"input_modality": None, "role": "assistant", "text": "x" * 20, "transcription": None}
        probe = self.writer()
        validated = probe._validate_record(candidate, stored=False)
        limit = len(probe._encode(validated)) + 1
        constrained = self.writer(max_record_bytes=limit, max_session_bytes=limit * 2)
        with self.assertRaises(TranscriptError):
            constrained.append(candidate)

    def test_existing_log_is_capped_before_reading(self) -> None:
        writer = self.writer(max_record_bytes=512, max_session_bytes=512)
        writer.prepare()
        writer.events_path.write_bytes(b"x" * 513)
        with self.assertRaises(TranscriptCorruptionError):
            self.writer(max_record_bytes=512, max_session_bytes=512).records()

    def test_existing_record_unknown_key_is_rejected_unchanged(self) -> None:
        writer = self.writer()
        writer.append(record("closed-reader"))
        stored = json.loads(writer.events_path.read_text(encoding="utf-8"))
        stored["unexpected"] = "synthetic"
        raw = (json.dumps(stored) + "\n").encode("utf-8")
        writer.events_path.write_bytes(raw)
        with self.assertRaises(TranscriptCorruptionError):
            self.writer().records()
        self.assertEqual(writer.events_path.read_bytes(), raw)

    def test_owned_file_symlinks_are_rejected(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside"
        outside.write_text("outside", encoding="utf-8")
        for name in ("events", "manifest", "lock"):
            writer = TranscriptWriter(transcript_session_id(name), patient_root=self.root)
            writer.append(record(f"safe-{name}"))
            path = {"events": writer.events_path, "manifest": writer.manifest_path, "lock": writer.directory / "writer.lock"}[name]
            path.unlink()
            path.symlink_to(outside)
            with self.subTest(path=path.name):
                with self.assertRaises(TranscriptError):
                    writer.records() if name != "lock" else writer.append(record("lock-test"))

    def test_lock_replacement_is_detected(self) -> None:
        writer = self.writer()
        writer.prepare()
        lock_path = writer.directory / "writer.lock"
        with self.assertRaisesRegex(TranscriptError, "lock file was replaced"):
            with writer._file_lock():
                lock_path.unlink()
                lock_path.write_bytes(b"")

    def test_recovery_file_symlink_is_rejected(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside"
        outside.write_text("outside", encoding="utf-8")
        writer = self.writer()
        writer.append(record("safe"))
        writer.recovery_path.symlink_to(outside)
        with writer.events_path.open("ab") as events_file:
            events_file.write(b'{"schema_version":')
        with self.assertRaises(TranscriptError):
            self.writer().records()

    def test_patient_root_mode_is_not_modified(self) -> None:
        self.root.mkdir()
        self.root.chmod(0o755)
        self.writer().append(record("root-mode"))
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o755)

    def test_concurrent_writers_preserve_one_monotonic_log(self) -> None:
        failures: list[Exception] = []

        def append(index: int) -> None:
            try:
                self.writer().append(record(f"concurrent:{index}"))
            except Exception as exc:
                failures.append(exc)

        threads = [threading.Thread(target=append, args=(index,)) for index in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual([item["sequence"] for item in self.writer().records()], list(range(1, 11)))

    def test_global_lock_registry_uses_weak_lifecycle_and_same_session_identity(self) -> None:
        first = self.writer()
        second = self.writer()
        first_lock = first._in_process_lock()
        second_lock = second._in_process_lock()
        self.assertIs(first_lock, second_lock)

        baseline = len(transcript_module._LOCKS)
        for index in range(100):
            transient = TranscriptWriter(transcript_session_id(f"weak-lock-{index}"), patient_root=self.root)
            transient._in_process_lock()
        gc.collect()
        self.assertLessEqual(len(transcript_module._LOCKS), baseline)

    def test_multiprocess_writers_preserve_one_monotonic_log(self) -> None:
        context = multiprocessing.get_context("fork")
        processes = [context.Process(target=_process_append, args=(str(self.root), self.session_id, index)) for index in range(6)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
        self.assertEqual([process.exitcode for process in processes], [0] * 6)
        self.assertEqual([item["sequence"] for item in self.writer().records()], list(range(1, 7)))

    def test_cached_writer_avoids_repeated_full_recovery(self) -> None:
        writer = self.writer()
        for index in range(10):
            writer.append(record(f"cached:{index}"))
        self.assertEqual(writer._full_recovery_count, 1)

        external = self.writer()
        external.append(record("external"))
        writer.append(record("after-external"))
        self.assertEqual(writer._full_recovery_count, 2)

    def test_manifest_same_sequence_contradiction_is_rejected(self) -> None:
        writer = self.writer()
        writer.append(record("first"))
        writer.append(record("second"))
        manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
        manifest["last_record_id"] = "rec_" + "f" * 32
        raw = (json.dumps(manifest) + "\n").encode("utf-8")
        writer.manifest_path.write_bytes(raw)
        with self.assertRaises(TranscriptCorruptionError):
            self.writer().records()
        self.assertEqual(writer.manifest_path.read_bytes(), raw)

    def test_empty_log_manifest_cannot_claim_a_record(self) -> None:
        writer = self.writer()
        writer.prepare()
        manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
        manifest["last_record_id"] = "rec_" + "f" * 32
        writer.manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        with self.assertRaises(TranscriptCorruptionError):
            self.writer().records()

    def test_manifest_honest_lag_is_repaired(self) -> None:
        writer = self.writer()
        writer.append(record("first"))
        writer.append(record("second"))
        stored = writer.records()
        manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
        manifest["last_committed_sequence"] = 1
        manifest["last_record_id"] = stored[0]["record_id"]
        manifest["integrity"]["last_verified_sequence"] = 1
        writer.manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        self.writer().records()
        repaired = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(repaired["last_committed_sequence"], 2)
        self.assertEqual(repaired["last_record_id"], stored[1]["record_id"])

    def test_recovery_log_is_bounded_and_validated_before_append(self) -> None:
        for name, contents in (("corrupt", b"not-json\n"), ("oversized", b"x" * (MAX_RECOVERY_BYTES + 1))):
            writer = TranscriptWriter(transcript_session_id(name), patient_root=self.root)
            writer.append(record(f"safe-{name}"))
            writer.recovery_path.write_bytes(contents)
            with writer.events_path.open("ab") as events_file:
                events_file.write(b'{"schema_version":')
            original_events = writer.events_path.read_bytes()
            with self.subTest(name=name):
                with self.assertRaises(TranscriptCorruptionError):
                    TranscriptWriter(transcript_session_id(name), patient_root=self.root).records()
                self.assertEqual(writer.events_path.read_bytes(), original_events)

    def test_recovery_rejects_duplicate_dedupe_keys(self) -> None:
        writer = self.writer()
        writer.append(record("first"))
        writer.append(record("second"))
        rows = [json.loads(line) for line in writer.events_path.read_text(encoding="utf-8").splitlines()]
        rows[1]["dedupe_key"] = rows[0]["dedupe_key"]
        raw = b"".join(writer._encode(row) for row in rows)
        writer.events_path.write_bytes(raw)
        with self.assertRaisesRegex(TranscriptCorruptionError, "duplicate dedupe keys"):
            self.writer().records()
        self.assertEqual(writer.events_path.read_bytes(), raw)

    def test_complete_write_loop_handles_short_and_zero_writes(self) -> None:
        writer = self.writer()
        writer.prepare()
        real_write = os.write

        def short_write(descriptor: int, data) -> int:
            chunk = bytes(data)
            return real_write(descriptor, chunk[: max(1, len(chunk) // 2)])

        with patch("psyclaw.transcript.os.write", side_effect=short_write):
            writer.append(record("short-writes"))
        self.assertEqual([item["dedupe_key"] for item in writer.records()], ["short-writes"])

        other = TranscriptWriter(transcript_session_id("zero-write"), patient_root=self.root)
        other.prepare()
        with patch("psyclaw.transcript.os.write", return_value=0):
            with self.assertRaises(TranscriptError):
                other.append(record("zero-write"))

    def test_relative_root_is_absolutized_and_symlink_checked(self) -> None:
        original_directory = Path.cwd()
        try:
            os.chdir(self.temporary_directory.name)
            with patch.dict(os.environ, {"PSYCLAW_PATIENT_DIR": "relative-patient"}):
                writer = TranscriptWriter(self.session_id)
                writer.append(record("relative"))
                self.assertTrue(writer.patient_root.is_absolute())
            outside = Path(self.temporary_directory.name) / "relative-outside"
            outside.mkdir()
            (Path(self.temporary_directory.name) / "linked-root").symlink_to(outside, target_is_directory=True)
            with patch.dict(os.environ, {"PSYCLAW_PATIENT_DIR": "linked-root/patient"}):
                with self.assertRaises(TranscriptError):
                    TranscriptWriter(transcript_session_id("linked-relative")).prepare()
        finally:
            os.chdir(original_directory)

    def test_record_size_and_future_schema_are_rejected_without_rewrite(self) -> None:
        writer = self.writer(max_record_bytes=600)
        oversized = record("large", kind="assistant_message")
        oversized["content"] = {"role": "assistant", "text": "x" * 1_000, "input_modality": None, "transcription": None}
        with self.assertRaises(TranscriptError):
            writer.append(oversized)

        writer = self.writer()
        writer.append(record("valid"))
        original = writer.events_path.read_bytes()
        future = json.loads(original.decode("utf-8"))
        future["schema_version"] = 2
        writer.events_path.write_text(json.dumps(future) + "\n", encoding="utf-8")
        with self.assertRaises(TranscriptCorruptionError):
            self.writer().records()
        self.assertEqual(writer.events_path.read_bytes(), json.dumps(future).encode("utf-8") + b"\n")

    def test_truncated_tail_and_manifest_lag_are_recovered(self) -> None:
        writer = self.writer()
        writer.append(record("first"))
        with writer.events_path.open("ab") as events_file:
            events_file.write(b'{"schema_version":')
        recovered = self.writer()
        self.assertEqual([item["dedupe_key"] for item in recovered.records()], ["first"])
        self.assertIn("truncated_final_record", recovered.recovery_path.read_text(encoding="utf-8"))

        def fail_after_append(point: str) -> None:
            if point == "after_append":
                raise RuntimeError("synthetic crash")

        crashing = self.writer(failure_injector=fail_after_append)
        with self.assertRaises(RuntimeError):
            crashing.append(record("second"))
        after_restart = self.writer()
        self.assertEqual([item["dedupe_key"] for item in after_restart.records()], ["first", "second"])
        self.assertEqual(after_restart.append(record("second")).status, "duplicate")

    def test_recovery_intent_survives_crash_after_truncation(self) -> None:
        writer = self.writer()
        writer.append(record("before-tail"))
        with writer.events_path.open("ab") as events_file:
            events_file.write(b'{"schema_version":')

        def crash_after_truncate(point: str) -> None:
            if point == "after_recovery_truncate":
                raise RuntimeError("synthetic recovery crash")

        with self.assertRaises(RuntimeError):
            self.writer(failure_injector=crash_after_truncate).records()
        markers = [json.loads(line) for line in writer.recovery_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([marker["state"] for marker in markers], ["intent"])
        self.assertEqual(writer.events_path.read_text(encoding="utf-8").count("\n"), 1)

        self.assertEqual([item["dedupe_key"] for item in self.writer().records()], ["before-tail"])
        markers = [json.loads(line) for line in writer.recovery_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([marker["state"] for marker in markers], ["intent", "resolved"])
        self.assertEqual(markers[0]["recovery_id"], markers[1]["recovery_id"])

    def test_partial_recovery_intent_is_discarded_before_event_mutation(self) -> None:
        writer = TranscriptWriter(transcript_session_id("partial-intent"), patient_root=self.root)
        writer.append(record("before-partial-intent"))
        with writer.events_path.open("ab") as events_file:
            events_file.write(b'{"schema_version":')
        original_events = writer.events_path.read_bytes()

        recovering = TranscriptWriter(transcript_session_id("partial-intent"), patient_root=self.root)
        real_append = recovering._append_bytes

        def crash_during_intent(path: Path, data: bytes) -> None:
            if path == recovering.recovery_path:
                real_append(path, data[: len(data) // 2])
                raise OSError("synthetic partial intent")
            real_append(path, data)

        with patch.object(recovering, "_append_bytes", side_effect=crash_during_intent):
            with self.assertRaises(OSError):
                recovering.records()
        self.assertEqual(writer.events_path.read_bytes(), original_events)
        self.assertFalse(writer.recovery_path.read_bytes().endswith(b"\n"))

        restarted = TranscriptWriter(transcript_session_id("partial-intent"), patient_root=self.root)
        self.assertEqual([item["dedupe_key"] for item in restarted.records()], ["before-partial-intent"])
        markers = [json.loads(line) for line in writer.recovery_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([marker["state"] for marker in markers], ["intent", "resolved"])

    def test_partial_recovery_resolution_completes_from_durable_intent(self) -> None:
        writer = TranscriptWriter(transcript_session_id("partial-resolution"), patient_root=self.root)
        writer.append(record("before-partial-resolution"))
        with writer.events_path.open("ab") as events_file:
            events_file.write(b'{"schema_version":')

        recovering = TranscriptWriter(transcript_session_id("partial-resolution"), patient_root=self.root)
        real_append = recovering._append_bytes
        recovery_appends = 0

        def crash_during_resolution(path: Path, data: bytes) -> None:
            nonlocal recovery_appends
            if path == recovering.recovery_path:
                recovery_appends += 1
                if recovery_appends == 2:
                    real_append(path, data[: len(data) // 2])
                    raise OSError("synthetic partial resolution")
            real_append(path, data)

        with patch.object(recovering, "_append_bytes", side_effect=crash_during_resolution):
            with self.assertRaises(OSError):
                recovering.records()
        self.assertEqual(writer.events_path.read_text(encoding="utf-8").count("\n"), 1)
        recovery_raw = writer.recovery_path.read_bytes()
        self.assertEqual(recovery_raw.count(b"\n"), 1)
        self.assertFalse(recovery_raw.endswith(b"\n"))

        restarted = TranscriptWriter(transcript_session_id("partial-resolution"), patient_root=self.root)
        self.assertEqual([item["dedupe_key"] for item in restarted.records()], ["before-partial-resolution"])
        markers = [json.loads(line) for line in writer.recovery_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([marker["state"] for marker in markers], ["intent", "resolved"])
        self.assertEqual(markers[0]["recovery_id"], markers[1]["recovery_id"])

    def test_crash_before_and_after_manifest_keep_honest_boundaries(self) -> None:
        def fail_before_append(point: str) -> None:
            if point == "before_append":
                raise RuntimeError("synthetic crash")

        with self.assertRaises(RuntimeError):
            self.writer(failure_injector=fail_before_append).append(record("before"))
        self.assertEqual(self.writer().records(), [])

        def fail_after_manifest(point: str) -> None:
            if point == "after_manifest":
                raise RuntimeError("synthetic crash")

        with self.assertRaises(RuntimeError):
            self.writer(failure_injector=fail_after_manifest).append(record("after"))
        self.assertEqual([item["dedupe_key"] for item in self.writer().records()], ["after"])

    def test_nonterminal_corruption_is_not_silently_rewritten(self) -> None:
        writer = self.writer()
        writer.append(record("first"))
        writer.append(record("second"))
        raw = writer.events_path.read_bytes().replace(b'"first"', b'not-json-data', 1)
        writer.events_path.write_bytes(raw)
        with self.assertRaises(TranscriptCorruptionError):
            self.writer().records()
        self.assertEqual(writer.events_path.read_bytes(), raw)


class TranscriptPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "patient"
        self.session = SimpleNamespace(id="synthetic-adk-session")

        def factory(session_id: str) -> TranscriptWriter:
            return TranscriptWriter(session_id, patient_root=self.root)

        self.plugin = TranscriptPlugin(
            conversation_author="psyclaw_agent",
            persistence_failure_strategy=fail_closed_persistence,
            writer_factory=factory,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def context(self, invocation: str) -> SimpleNamespace:
        return SimpleNamespace(session=self.session, invocation_id=invocation, agent_name="psyclaw_agent")

    def callback_context(self, invocation: str, agent_name: str) -> CallbackContext:
        session = Session(id=self.session.id, app_name="psyclaw", user_id="synthetic-user")
        invocation_context = InvocationContext.model_construct(
            invocation_id=invocation,
            session=session,
            agent=SimpleNamespace(name=agent_name),
        )
        return CallbackContext(invocation_context)

    def tool_context(self, invocation: str, agent_name: str, call_id: str) -> ToolContext:
        session = Session(id=self.session.id, app_name="psyclaw", user_id="synthetic-user")
        invocation_context = InvocationContext.model_construct(
            invocation_id=invocation,
            session=session,
            agent=SimpleNamespace(name=agent_name),
        )
        return ToolContext(invocation_context, function_call_id=call_id)

    def records(self) -> list[dict]:
        return TranscriptWriter(transcript_session_id(self.session.id), patient_root=self.root).records()

    def test_normalised_input_complete_assistant_and_partial_filtering(self) -> None:
        context = self.context("invocation-one")
        typed = SimpleNamespace(parts=[SimpleNamespace(text="  hello\r\nworld  ", thought=False)])
        asyncio.run(self.plugin.on_user_message_callback(invocation_context=context, user_message=typed))
        for delta in ("hel", "hello", "hello world"):
            partial = SimpleNamespace(id=f"partial-{delta}", invocation_id="invocation-one", author="psyclaw_agent", partial=True, content=SimpleNamespace(parts=[SimpleNamespace(text=delta, thought=False)]), input_transcription=None, output_transcription=None, error_code=None, interrupted=False, finish_reason=None)
            asyncio.run(self.plugin.on_event_callback(invocation_context=context, event=partial))
        complete = SimpleNamespace(id="assistant-complete", invocation_id="invocation-one", author="psyclaw_agent", partial=False, content=SimpleNamespace(parts=[SimpleNamespace(text="A complete answer", thought=False), SimpleNamespace(text="hidden reasoning", thought=True)]), input_transcription=None, output_transcription=None, error_code=None, interrupted=False, finish_reason="STOP")
        asyncio.run(self.plugin.on_event_callback(invocation_context=context, event=complete))

        records = self.records()
        self.assertEqual([item["kind"] for item in records], ["user_message", "assistant_message"])
        self.assertEqual(records[0]["content"]["text"], "hello\nworld")
        self.assertEqual(records[1]["content"]["text"], "A complete answer")
        self.assertNotIn("hidden reasoning", json.dumps(records))

    def test_identical_inputs_in_distinct_invocations_remain_distinct(self) -> None:
        typed = SimpleNamespace(parts=[SimpleNamespace(text="same", thought=False)])
        asyncio.run(self.plugin.on_user_message_callback(invocation_context=self.context("one"), user_message=typed))
        asyncio.run(self.plugin.on_user_message_callback(invocation_context=self.context("two"), user_message=typed))
        self.assertEqual([item["content"]["text"] for item in self.records()], ["same", "same"])

    def test_safe_tool_projection_and_privacy_defaults(self) -> None:
        context = self.context("tool-invocation")
        function_response = SimpleNamespace(id="call-id", name="read_file", response={"content": "PATIENT-FILE-CONTENT", "path": "/private/host/path", "token": "sk-secret"})
        event = SimpleNamespace(id="tool-event", invocation_id="tool-invocation", author="psyclaw_agent", partial=False, content=SimpleNamespace(parts=[SimpleNamespace(function_call=None, function_response=function_response, text=None, thought=False)]), input_transcription=None, output_transcription=None, error_code=None, interrupted=False, finish_reason=None)
        asyncio.run(self.plugin.on_event_callback(invocation_context=context, event=event))
        stored = self.records()[0]
        serialized = json.dumps(stored)
        self.assertEqual(stored["tool"]["calls"][0]["name"], "read_file")
        self.assertEqual(stored["tool"]["calls"][0]["status"], "returned")
        for prohibited in ("PATIENT-FILE-CONTENT", "/private/host/path", "sk-secret", "response"):
            self.assertNotIn(prohibited, serialized)

    def test_speech_transcript_and_error_status_are_safe(self) -> None:
        context = self.context("voice-invocation")
        speech = Event(id="speech-event", invocation_id="voice-invocation", author="user", input_transcription=types.Transcription(text="spoken words", language_code="fr"))
        asyncio.run(self.plugin.on_event_callback(invocation_context=context, event=speech))
        asyncio.run(self.plugin.on_run_error_callback(invocation_context=context, error=RuntimeError("secret /absolute/path")))
        records = self.records()
        self.assertEqual(records[0]["content"]["input_modality"], "speech_transcript")
        self.assertEqual(records[1]["kind"], "invocation_failed")
        self.assertNotIn("secret /absolute/path", json.dumps(records))

    def test_real_output_transcription_event_is_captured(self) -> None:
        event = Event(
            id="output-speech-event",
            invocation_id="output-speech-invocation",
            author="psyclaw_agent",
            output_transcription=types.Transcription(text="spoken response", language_code="en"),
        )
        asyncio.run(self.plugin.on_event_callback(invocation_context=self.context("output-speech-invocation"), event=event))
        stored = self.records()[0]
        self.assertEqual(stored["kind"], "assistant_message")
        self.assertEqual(stored["content"]["text"], "spoken response")
        self.assertEqual(stored["content"]["transcription"], {"language": "und", "source": "adk_output_transcription"})

    def test_real_mixed_text_and_tool_event_produces_distinct_records(self) -> None:
        event = Event(
            id="mixed-event",
            invocation_id="mixed-invocation",
            author="psyclaw_agent",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(text="Visible answer"),
                    types.Part(function_call=types.FunctionCall(id="call-mixed", name="read_file", args={"path": "/private/secret"})),
                ],
            ),
        )
        asyncio.run(self.plugin.on_event_callback(invocation_context=self.context("mixed-invocation"), event=event))
        records = self.records()
        self.assertEqual([item["kind"] for item in records], ["assistant_message", "tool_event"])
        self.assertEqual(records[0]["content"]["text"], "Visible answer")
        self.assertEqual(records[1]["tool"]["calls"][0]["name"], "read_file")
        self.assertNotEqual(records[0]["dedupe_key"], records[1]["dedupe_key"])
        self.assertNotIn("/private/secret", json.dumps(records))
        writer = TranscriptWriter(transcript_session_id(self.session.id), patient_root=self.root)
        self.assertEqual(len(writer.events_path.read_text(encoding="utf-8").splitlines()), 1)
        self.assertIn("batch_id", json.loads(writer.events_path.read_text(encoding="utf-8")))

    def test_non_conversation_author_text_is_not_mislabeled_assistant(self) -> None:
        event = Event(
            id="internal-event",
            invocation_id="internal-invocation",
            author="internal_agent",
            content=types.Content(role="model", parts=[types.Part(text="internal planning")]),
        )
        asyncio.run(self.plugin.on_event_callback(invocation_context=self.context("internal-invocation"), event=event))
        self.assertEqual(self.records(), [])
        self.assertNotIn("internal planning", json.dumps(self.plugin.diagnostics))

    def test_internal_author_input_transcription_is_not_patient_speech(self) -> None:
        event = Event(
            id="internal-speech-event",
            invocation_id="internal-speech-invocation",
            author="memory_agent",
            input_transcription=types.Transcription(text="internal transcript", language_code="en"),
        )
        asyncio.run(self.plugin.on_event_callback(invocation_context=self.context("internal-speech-invocation"), event=event))
        self.assertEqual(self.records(), [])

    def test_required_root_event_identifiers_fail_closed_and_sanitized(self) -> None:
        event = Event(author="psyclaw_agent", content=types.Content(role="model", parts=[types.Part(text="answer")]))
        with self.assertRaisesRegex(TranscriptCaptureError, "^Transcript capture requires valid ADK identifiers\\.$"):
            asyncio.run(self.plugin.on_event_callback(invocation_context=self.context("valid"), event=event))

    def test_model_and_run_error_callbacks_share_one_terminal_record(self) -> None:
        context = self.callback_context("failed-invocation", "psyclaw_agent")
        asyncio.run(self.plugin.on_model_error_callback(callback_context=context, llm_request=object(), error=RuntimeError("synthetic")))
        asyncio.run(self.plugin.on_run_error_callback(invocation_context=context, error=RuntimeError("synthetic")))
        records = self.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"]["error_category"], "model_error")

    def test_internal_memory_agent_model_error_is_not_root_failure(self) -> None:
        context = self.callback_context("memory-failure", "memory_agent")
        asyncio.run(self.plugin.on_model_error_callback(callback_context=context, llm_request=object(), error=RuntimeError("internal secret")))
        self.assertEqual(self.records(), [])
        self.assertEqual(self.plugin.diagnostics, ["transcript_internal_model_error_skipped"])

    def test_tool_error_is_nonterminal_safe_metadata(self) -> None:
        tool_context = self.tool_context("tool-failure", "psyclaw_agent", "call-failure")
        tool = SimpleNamespace(name="read_file")
        asyncio.run(self.plugin.on_tool_error_callback(tool=tool, tool_args={"path": "/private/sensitive.md"}, tool_context=tool_context, error=RuntimeError("secret")))
        records = self.records()
        self.assertEqual(records[0]["kind"], "tool_error")
        self.assertEqual(records[0]["outcome"]["error_category"], "tool_error")
        self.assertNotIn("invocation_failed", json.dumps(records))
        self.assertNotIn("sensitive", json.dumps(records))

    def test_internal_memory_agent_tool_error_is_skipped(self) -> None:
        tool_context = self.tool_context("memory-tool-failure", "memory_agent", "internal-call")
        tool = SimpleNamespace(name="internal_memory_lookup")
        asyncio.run(self.plugin.on_tool_error_callback(
            tool=tool,
            tool_args={"secret": "patient detail"},
            tool_context=tool_context,
            error=RuntimeError("internal secret"),
        ))
        self.assertEqual(self.records(), [])
        self.assertEqual(self.plugin.diagnostics, ["transcript_internal_tool_error_skipped"])

    def test_missing_callback_id_skips_without_deduplication_collision(self) -> None:
        context = self.context("valid-invocation")
        missing = SimpleNamespace(id=None, invocation_id=None, author="internal_agent", partial=False, content=SimpleNamespace(parts=[]), input_transcription=None, output_transcription=None, error_code=None, interrupted=False, finish_reason=None)
        asyncio.run(self.plugin.on_event_callback(invocation_context=context, event=missing))
        asyncio.run(self.plugin.on_event_callback(invocation_context=context, event=missing))
        self.assertEqual(self.records(), [])
        self.assertEqual(self.plugin.diagnostics, ["transcript_optional_event_skipped", "transcript_optional_event_skipped"])

    def test_diagnostics_are_bounded(self) -> None:
        missing_event = SimpleNamespace(id=None, invocation_id=None, author="internal_agent", partial=False, content=SimpleNamespace(parts=[]), input_transcription=None, output_transcription=None, error_code=None, interrupted=False, finish_reason=None)
        for _ in range(40):
            asyncio.run(self.plugin.on_event_callback(invocation_context=self.context("valid"), event=missing_event))
        self.assertEqual(len(self.plugin.diagnostics), 32)

    def test_capture_failure_is_fail_closed_and_sanitized(self) -> None:
        def broken_factory(session_id: str) -> TranscriptWriter:
            raise TranscriptError("sensitive failure")

        plugin = TranscriptPlugin(
            conversation_author="psyclaw_agent",
            persistence_failure_strategy=fail_closed_persistence,
            writer_factory=broken_factory,
        )
        message = SimpleNamespace(parts=[SimpleNamespace(text="synthetic", thought=False)])
        with self.assertRaisesRegex(TranscriptCaptureError, "^Transcript capture failed\\.$"):
            asyncio.run(plugin.on_user_message_callback(invocation_context=self.context("failure"), user_message=message))
        self.assertEqual(plugin.diagnostics, [])

    def test_storage_quota_and_corruption_failures_are_sanitized(self) -> None:
        message = SimpleNamespace(parts=[SimpleNamespace(text="synthetic", thought=False)])
        for failure in (
            TranscriptError("quota with /private/path"),
            TranscriptCorruptionError("corrupt secret"),
            OSError("disk failure with secret"),
            PermissionError("permission failure with secret"),
        ):
            class BrokenWriter:
                def append_batch(self, captured_records):
                    raise failure

            plugin = TranscriptPlugin(
                conversation_author="psyclaw_agent",
                persistence_failure_strategy=fail_closed_persistence,
                writer_factory=lambda session_id: BrokenWriter(),
            )
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaisesRegex(TranscriptCaptureError, "^Transcript capture failed\\.$"):
                    asyncio.run(plugin.on_user_message_callback(invocation_context=self.context("failure"), user_message=message))
                self.assertEqual(self.records(), [])

    def test_short_write_failure_is_fail_closed_without_a_healthy_record(self) -> None:
        message = SimpleNamespace(parts=[SimpleNamespace(text="synthetic", thought=False)])
        with patch("psyclaw.transcript.os.write", return_value=0):
            with self.assertRaisesRegex(TranscriptCaptureError, "^Transcript capture failed\\.$"):
                asyncio.run(self.plugin.on_user_message_callback(invocation_context=self.context("short-write"), user_message=message))
        self.assertEqual(self.records(), [])

    def test_persistence_failure_strategy_is_explicitly_required(self) -> None:
        with self.assertRaises(TypeError):
            TranscriptPlugin(conversation_author="psyclaw_agent")

    def test_plugin_reuses_one_writer_per_session(self) -> None:
        creations: list[str] = []

        def factory(session_id: str) -> TranscriptWriter:
            creations.append(session_id)
            return TranscriptWriter(session_id, patient_root=self.root)

        plugin = TranscriptPlugin(
            conversation_author="psyclaw_agent",
            persistence_failure_strategy=fail_closed_persistence,
            writer_factory=factory,
        )
        for invocation in ("one", "two"):
            message = SimpleNamespace(parts=[SimpleNamespace(text=invocation, thought=False)])
            asyncio.run(plugin.on_user_message_callback(invocation_context=self.context(invocation), user_message=message))
        self.assertEqual(len(creations), 1)

    def test_writer_cache_is_bounded_lru(self) -> None:
        class NoopWriter:
            def append_batch(self, records):
                return None

        plugin = TranscriptPlugin(
            conversation_author="psyclaw_agent",
            persistence_failure_strategy=fail_closed_persistence,
            writer_factory=lambda session_id: NoopWriter(),
        )
        message = SimpleNamespace(parts=[SimpleNamespace(text="synthetic", thought=False)])
        for index in range(MAX_CACHED_WRITERS + 2):
            context = SimpleNamespace(
                session=SimpleNamespace(id=f"session-{index}"),
                invocation_id=f"invocation-{index}",
                agent_name="psyclaw_agent",
            )
            asyncio.run(plugin.on_user_message_callback(invocation_context=context, user_message=message))
        self.assertEqual(len(plugin._writers), MAX_CACHED_WRITERS)
        self.assertNotIn(transcript_session_id("session-0"), plugin._writers)
        self.assertIn(transcript_session_id(f"session-{MAX_CACHED_WRITERS + 1}"), plugin._writers)


class TextNormalisationTest(unittest.TestCase):
    def test_text_normalisation_is_deterministic(self) -> None:
        self.assertEqual(normalise_text("  cafe\u0301\rtest  "), "café\ntest")


if __name__ == "__main__":
    unittest.main()
