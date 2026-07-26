import asyncio
import unittest

from psyclaw.transcription import (
    TranscriptionCapabilities,
    TranscriptionDiagnostic,
    TranscriptionDiagnosticCode,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRegistry,
    TranscriptionRequest,
    TranscriptionResult,
    transcribe,
)


class RecordingService:
    capabilities = TranscriptionCapabilities(
        supported_content_types=frozenset({"audio/webm"}), max_audio_bytes=8
    )

    def __init__(self, result: TranscriptionResult | None = None) -> None:
        self.result = result or TranscriptionResult(text="A synthetic transcript.")
        self.requests: list[TranscriptionRequest] = []

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.requests.append(request)
        return self.result


class FailingService(RecordingService):
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        raise RuntimeError("provider secret should not cross the boundary")


class TranscriptionBoundaryTest(unittest.TestCase):
    def test_request_is_immutable_and_hides_raw_audio_in_repr(self) -> None:
        request = TranscriptionRequest(
            audio=b"sensitive-audio", content_type=" AUDIO/WEBM ", request_id="req-1"
        )

        self.assertEqual(request.content_type, "audio/webm")
        self.assertNotIn("sensitive-audio", repr(request))
        self.assertNotIn("req-1", repr(request))
        with self.assertRaises(AttributeError):
            request.request_id = "another"  # type: ignore[misc]

    def test_request_canonicalizes_common_browser_content_types(self) -> None:
        for content_type in (
            "audio/webm; codecs=opus",
            ' AUDIO/WEBM ; codecs="opus" ',
            "audio/webm; codecs=opus; rate=48000",
        ):
            with self.subTest(content_type=content_type):
                request = TranscriptionRequest(b"abc", content_type, "req-1")
                self.assertEqual(request.content_type, "audio/webm")

    def test_request_rejects_malformed_or_unbounded_content_type_parameters(self) -> None:
        invalid_content_types = (
            "audio/webm; codecs=opus; CODECS=vp9",
            "audio/webm; codecs",
            "audio/webm; codecs=",
            "audio/webm; codecs=opus=extra",
            "audio/webm; codecs=\x01opus",
            "audio/webm; codecs=\"unterminated",
            "audio/webm; a=1; b=2; c=3; d=4; e=5; f=6; g=7; h=8; i=9",
            "audio/webm; codecs=" + "a" * 240,
        )
        for content_type in invalid_content_types:
            with self.subTest(content_type=content_type):
                with self.assertRaises(TranscriptionError) as context:
                    TranscriptionRequest(b"abc", content_type, "req-1")
                self.assertEqual(context.exception.code, TranscriptionErrorCode.INVALID_REQUEST)

    def test_request_rejects_hard_size_limit_to_safe_error(self) -> None:
        with self.assertRaises(TranscriptionError) as context:
            TranscriptionRequest(
                b"a" * (25 * 1024 * 1024 + 1), "audio/webm", "req-1"
            )
        self.assertEqual(context.exception.code, TranscriptionErrorCode.AUDIO_TOO_LARGE)

    def test_result_rejects_empty_text_and_hides_transcript_in_repr(self) -> None:
        with self.assertRaises(TranscriptionError) as context:
            TranscriptionResult(text="   ")
        self.assertEqual(context.exception.code, TranscriptionErrorCode.EMPTY_TRANSCRIPT)

        result = TranscriptionResult(text="Sensitive spoken words")
        self.assertNotIn("Sensitive spoken words", repr(result))

        with self.assertRaises(TranscriptionError) as context:
            TranscriptionResult(text="a" * 100_001)
        self.assertEqual(
            context.exception.code, TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )

    def test_result_rejects_unbounded_or_unsafe_diagnostics(self) -> None:
        diagnostic = TranscriptionDiagnostic(
            TranscriptionDiagnosticCode.PARTIAL_RESULT, True
        )
        with self.assertRaises(TranscriptionError) as context:
            TranscriptionResult(text="Valid", diagnostics=(diagnostic,) * 17)
        self.assertEqual(
            context.exception.code, TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )
        with self.assertRaises(ValueError):
            TranscriptionDiagnostic("provider_message", "do not expose this")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            TranscriptionDiagnostic(TranscriptionDiagnosticCode.PARTIAL_RESULT, "yes")
        with self.assertRaises(ValueError):
            TranscriptionDiagnostic(
                TranscriptionDiagnosticCode.LANGUAGE_DETECTED, "provider_secret"
            )

    def test_capabilities_require_an_explicit_bounded_media_allowlist(self) -> None:
        with self.assertRaises(ValueError):
            TranscriptionCapabilities(frozenset(), 8)
        with self.assertRaises(ValueError):
            TranscriptionCapabilities(frozenset({"audio/webm"}), 0)
        with self.assertRaises(ValueError):
            TranscriptionCapabilities(frozenset({"audio/webm; codecs=opus"}), 8)

    def test_boundary_rejects_unsupported_media_before_service_call(self) -> None:
        service = RecordingService()
        request = TranscriptionRequest(b"abc", "audio/wav", "req-1")

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(transcribe(service, request))

        self.assertEqual(context.exception.code, TranscriptionErrorCode.UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(service.requests, [])

    def test_boundary_matches_browser_content_type_against_canonical_capability(self) -> None:
        service = RecordingService()

        result = asyncio.run(
            transcribe(
                service,
                TranscriptionRequest(b"abc", "audio/webm; codecs=opus", "req-1"),
            )
        )

        self.assertEqual(result.text, "A synthetic transcript.")
        self.assertEqual(len(service.requests), 1)

    def test_boundary_rejects_oversized_audio_before_service_call(self) -> None:
        service = RecordingService()
        request = TranscriptionRequest(b"123456789", "audio/webm", "req-1")

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(transcribe(service, request))

        self.assertEqual(context.exception.code, TranscriptionErrorCode.AUDIO_TOO_LARGE)
        self.assertEqual(service.requests, [])

    def test_boundary_returns_only_validated_text_result(self) -> None:
        diagnostic = TranscriptionDiagnostic(
            TranscriptionDiagnosticCode.LANGUAGE_DETECTED, "en"
        )
        service = RecordingService(
            TranscriptionResult(
                text="A synthetic transcript.",
                detected_language="en",
                diagnostics=(diagnostic,),
            )
        )

        result = asyncio.run(
            transcribe(service, TranscriptionRequest(b"abc", "audio/webm", "req-1"))
        )

        self.assertEqual(result.text, "A synthetic transcript.")
        self.assertEqual(result.diagnostics, (diagnostic,))
        self.assertIsNot(result, service.result)

    def test_boundary_revalidates_mutated_provider_result_fields(self) -> None:
        invalid_values = (
            ("text", " "),
            ("text", "a" * 100_001),
            ("detected_language", "not_a_language_tag"),
            ("diagnostics", (object(),)),
        )
        for attribute, value in invalid_values:
            with self.subTest(attribute=attribute):
                result = TranscriptionResult(text="Valid transcript")
                object.__setattr__(result, attribute, value)
                with self.assertRaises(TranscriptionError) as context:
                    asyncio.run(
                        transcribe(
                            RecordingService(result),
                            TranscriptionRequest(b"abc", "audio/webm", "req-1"),
                        )
                    )
                self.assertEqual(
                    context.exception.code,
                    TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE,
                )

    def test_boundary_rejects_constructor_bypassed_provider_results(self) -> None:
        invalid_values = (
            ("", ()),
            ("a" * 100_001, ()),
            ("Valid transcript", (object(),)),
        )
        for text, diagnostics in invalid_values:
            with self.subTest(text_length=len(text), diagnostics=diagnostics):
                bypassed = TranscriptionResult.__new__(TranscriptionResult)
                object.__setattr__(bypassed, "text", text)
                object.__setattr__(bypassed, "detected_language", None)
                object.__setattr__(bypassed, "diagnostics", diagnostics)

                with self.assertRaises(TranscriptionError) as context:
                    asyncio.run(
                        transcribe(
                            RecordingService(bypassed),
                            TranscriptionRequest(b"abc", "audio/webm", "req-1"),
                        )
                    )

                self.assertEqual(
                    context.exception.code,
                    TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE,
                )

    def test_boundary_revalidates_mutated_provider_diagnostics(self) -> None:
        diagnostic = TranscriptionDiagnostic(
            TranscriptionDiagnosticCode.PARTIAL_RESULT, True
        )
        object.__setattr__(diagnostic, "value", "not-a-boolean")
        result = TranscriptionResult(text="Valid transcript", diagnostics=(diagnostic,))

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(
                transcribe(
                    RecordingService(result),
                    TranscriptionRequest(b"abc", "audio/webm", "req-1"),
                )
            )

        self.assertEqual(
            context.exception.code, TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )

    def test_boundary_rejects_mutated_diagnostics_list_without_iterating_it(self) -> None:
        result = TranscriptionResult(text="Valid transcript")
        object.__setattr__(result, "diagnostics", [])

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(
                transcribe(
                    RecordingService(result),
                    TranscriptionRequest(b"abc", "audio/webm", "req-1"),
                )
            )

        self.assertEqual(
            context.exception.code, TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )

    def test_boundary_rejects_oversized_diagnostics_tuple_before_reconstruction(self) -> None:
        diagnostic = TranscriptionDiagnostic(
            TranscriptionDiagnosticCode.PARTIAL_RESULT, True
        )
        result = TranscriptionResult(text="Valid transcript")
        object.__setattr__(result, "diagnostics", (diagnostic,) * 17)

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(
                transcribe(
                    RecordingService(result),
                    TranscriptionRequest(b"abc", "audio/webm", "req-1"),
                )
            )

        self.assertEqual(
            context.exception.code, TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )

    def test_boundary_never_iterates_hostile_diagnostics_object(self) -> None:
        class HostileIterable:
            iterated = False

            def __iter__(self) -> object:
                self.iterated = True
                raise AssertionError("diagnostics must not be iterated")

        hostile_diagnostics = HostileIterable()
        result = TranscriptionResult(text="Valid transcript")
        object.__setattr__(result, "diagnostics", hostile_diagnostics)

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(
                transcribe(
                    RecordingService(result),
                    TranscriptionRequest(b"abc", "audio/webm", "req-1"),
                )
            )

        self.assertEqual(
            context.exception.code, TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )
        self.assertFalse(hostile_diagnostics.iterated)

    def test_provider_failure_has_a_safe_normalized_error(self) -> None:
        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(
                transcribe(
                    FailingService(),
                    TranscriptionRequest(b"abc", "audio/webm", "req-1"),
                )
            )

        self.assertEqual(context.exception.code, TranscriptionErrorCode.TRANSCRIPTION_FAILED)
        self.assertNotIn("provider secret", str(context.exception))
        self.assertIsNone(context.exception.__cause__)

    def test_registry_has_no_default_or_fallback_provider(self) -> None:
        registry = TranscriptionRegistry()
        configured = []

        def create_service(configuration: object) -> RecordingService:
            configured.append(configuration)
            return RecordingService()

        registry.register("test-provider", create_service)
        service = registry.create("test-provider", {"token": "not logged"})

        self.assertIsInstance(service, RecordingService)
        self.assertEqual(len(configured), 1)
        with self.assertRaises(TypeError):
            configured[0]["token"] = "mutated"  # type: ignore[index]
        with self.assertRaises(TranscriptionError) as context:
            registry.create("missing-provider", {})
        self.assertEqual(context.exception.code, TranscriptionErrorCode.CONFIGURATION_ERROR)
        with self.assertRaises(TranscriptionError) as context:
            registry.create(["not-a-token"], {})  # type: ignore[arg-type]
        self.assertEqual(context.exception.code, TranscriptionErrorCode.CONFIGURATION_ERROR)

    def test_registry_rejects_duplicate_provider_registration(self) -> None:
        registry = TranscriptionRegistry()
        registry.register("test-provider", lambda configuration: RecordingService())

        with self.assertRaises(TranscriptionError) as context:
            registry.register("test-provider", lambda configuration: RecordingService())

        self.assertEqual(context.exception.code, TranscriptionErrorCode.CONFIGURATION_ERROR)

    def test_boundary_normalizes_malformed_service_and_request_objects(self) -> None:
        class BrokenService:
            @property
            def capabilities(self) -> TranscriptionCapabilities:
                raise RuntimeError("unsafe provider configuration")

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(
                transcribe(
                    BrokenService(),
                    TranscriptionRequest(b"abc", "audio/webm", "req-1"),
                )
            )  # type: ignore[arg-type]
        self.assertEqual(context.exception.code, TranscriptionErrorCode.CONFIGURATION_ERROR)
        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(transcribe(RecordingService(), object()))  # type: ignore[arg-type]
        self.assertEqual(context.exception.code, TranscriptionErrorCode.INVALID_REQUEST)


if __name__ == "__main__":
    unittest.main()
