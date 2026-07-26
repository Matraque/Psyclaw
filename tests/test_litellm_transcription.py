import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from litellm.litellm_core_utils.audio_utils.utils import process_audio_file
from litellm.llms.mistral.audio_transcription.transformation import (
    MistralAudioTranscriptionConfig,
)

from psyclaw.litellm_transcription import (
    LiteLLMTranscriptionConfiguration,
    LiteLLMTranscriptionService,
    create_litellm_transcription_service,
    load_litellm_transcription_configuration,
)
from psyclaw.transcription import (
    TranscriptionCapabilities,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    transcribe,
)


class LiteLLMTranscriptionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = TranscriptionCapabilities(
            supported_content_types=frozenset({"audio/webm"}), max_audio_bytes=1024
        )

    def _service(
        self, model: str, *, api_base: str | None = None
    ) -> LiteLLMTranscriptionService:
        return LiteLLMTranscriptionService(
            LiteLLMTranscriptionConfiguration(
                model=model,
                api_key="test-key-not-a-real-secret",
                api_base=api_base,
                capabilities=self.capabilities,
            )
        )

    @patch("psyclaw.litellm_transcription.litellm.atranscription", new_callable=AsyncMock)
    def test_mistral_prefixed_model_uses_one_in_memory_request(
        self, transcription: AsyncMock
    ) -> None:
        transcription.return_value = {"text": "Synthetic transcript"}

        result = asyncio.run(
            transcribe(
                self._service("mistral/voxtral-mini-latest"),
                TranscriptionRequest(b"audio", "audio/webm", "req-1", "en"),
            )
        )

        self.assertEqual(result.text, "Synthetic transcript")
        transcription.assert_awaited_once_with(
            model="mistral/voxtral-mini-latest",
            file=("recording.webm", b"audio", "video/webm"),
            api_key="test-key-not-a-real-secret",
            language="en",
        )

    @patch("psyclaw.litellm_transcription.litellm.atranscription", new_callable=AsyncMock)
    def test_file_policy_matches_installed_litellm_mime_inference(
        self, transcription: AsyncMock
    ) -> None:
        transcription.return_value = {"text": "Synthetic transcript"}
        cases = (
            ("audio/webm", "recording.webm", "video/webm"),
            ("audio/x-m4a", "recording.m4a", "audio/x-m4a"),
            ("video/mp4", "recording.mp4", "video/mp4"),
        )

        for content_type, expected_filename, expected_inferred_type in cases:
            with self.subTest(content_type=content_type):
                service = LiteLLMTranscriptionService(
                    LiteLLMTranscriptionConfiguration(
                        model="mistral/voxtral-mini-latest",
                        api_key="test-key-not-a-real-secret",
                        capabilities=TranscriptionCapabilities(
                            frozenset({content_type}), 1024
                        ),
                    )
                )
                asyncio.run(
                    service.transcribe(
                        TranscriptionRequest(b"audio", content_type, "req-1")
                    )
                )

                processed = process_audio_file(
                    transcription.await_args.kwargs["file"]
                )
                self.assertEqual(processed.file_content, b"audio")
                self.assertEqual(processed.filename, expected_filename)
                self.assertEqual(processed.content_type, expected_inferred_type)

        browser_webm = (
            MistralAudioTranscriptionConfig().transform_audio_transcription_request(
                model="voxtral-mini-latest",
                audio_file=("recording.webm", b"audio", "audio/webm"),
                optional_params={},
                litellm_params={},
            )
        )
        self.assertEqual(
            browser_webm.files["file"],
            ("recording.webm", b"audio", "video/webm"),
        )

    def test_configuration_rejects_mime_types_litellm_cannot_represent(self) -> None:
        for content_type in ("audio/mp4", "application/octet-stream"):
            with self.subTest(content_type=content_type):
                with self.assertRaises(TranscriptionError) as context:
                    LiteLLMTranscriptionConfiguration(
                        model="openai/whisper-1",
                        api_key="test-key-not-a-real-secret",
                        capabilities=TranscriptionCapabilities(
                            frozenset({content_type}), 1024
                        ),
                    )
                self.assertEqual(
                    context.exception.code, TranscriptionErrorCode.CONFIGURATION_ERROR
                )

    @patch("psyclaw.litellm_transcription.litellm.atranscription", new_callable=AsyncMock)
    def test_openai_prefixed_model_forwards_optional_api_base(
        self, transcription: AsyncMock
    ) -> None:
        transcription.return_value = {"text": "Synthetic transcript"}

        asyncio.run(
            transcribe(
                self._service("openai/whisper-1", api_base="http://localhost:8000/v1"),
                TranscriptionRequest(b"audio", "audio/webm", "req-1"),
            )
        )

        self.assertEqual(transcription.await_count, 1)
        self.assertEqual(transcription.await_args.kwargs["model"], "openai/whisper-1")
        self.assertEqual(transcription.await_args.kwargs["api_base"], "http://localhost:8000/v1")

    def test_missing_or_unprefixed_configuration_has_safe_error(self) -> None:
        configurations = (
            {},
            {
                "model": "whisper-1",
                "api_key": "key",
                "supported_content_types": ["audio/webm"],
                "max_audio_bytes": 1024,
            },
            {
                "model": "openai/whisper-1",
                "api_key": "",
                "supported_content_types": ["audio/webm"],
                "max_audio_bytes": 1024,
            },
        )
        for configuration in configurations:
            with self.subTest(configuration=configuration):
                with self.assertRaises(TranscriptionError) as context:
                    create_litellm_transcription_service(configuration)
                self.assertEqual(context.exception.code, TranscriptionErrorCode.CONFIGURATION_ERROR)

    def test_environment_loader_uses_only_explicit_generic_settings(self) -> None:
        configuration = load_litellm_transcription_configuration(
            {
                "PSYCLAW_STT_MODEL": "mistral/voxtral-mini-latest",
                "PSYCLAW_STT_API_KEY": "test-key-not-a-real-secret",
                "PSYCLAW_STT_API_BASE": "http://localhost:8000/v1",
                "MISTRAL_API_KEY": "must-not-be-used",
                "OPENAI_API_KEY": "must-not-be-used",
            },
            capabilities=self.capabilities,
        )

        self.assertEqual(configuration.model, "mistral/voxtral-mini-latest")
        self.assertEqual(configuration.api_base, "http://localhost:8000/v1")
        self.assertIs(configuration.capabilities, self.capabilities)

    def test_environment_loader_rejects_missing_values_without_key_leakage(self) -> None:
        secret = "super-secret-api-key"
        environments = (
            {"PSYCLAW_STT_API_KEY": secret},
            {"PSYCLAW_STT_MODEL": "openai/whisper-1"},
            {
                "PSYCLAW_STT_MODEL": "openai/whisper-1",
                "PSYCLAW_STT_API_KEY": "",
            },
        )
        for environment in environments:
            with self.subTest(environment=environment):
                with self.assertRaises(TranscriptionError) as context:
                    load_litellm_transcription_configuration(
                        environment, capabilities=self.capabilities
                    )
                self.assertEqual(
                    context.exception.code, TranscriptionErrorCode.CONFIGURATION_ERROR
                )
                self.assertNotIn(secret, str(context.exception))
                self.assertIsNone(context.exception.__cause__)
                self.assertIsNone(context.exception.__context__)

    @patch("psyclaw.litellm_transcription.litellm.atranscription", new_callable=AsyncMock)
    def test_provider_failure_is_safe_and_never_leaks_api_key(
        self, transcription: AsyncMock
    ) -> None:
        secret = "super-secret-api-key"
        transcription.side_effect = RuntimeError("provider failed with " + secret)
        configuration = LiteLLMTranscriptionConfiguration(
            model="openai/whisper-1", api_key=secret, capabilities=self.capabilities
        )
        service = LiteLLMTranscriptionService(
            configuration
        )

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(
                service.transcribe(
                    TranscriptionRequest(b"audio", "audio/webm", "req-1")
                )
            )

        self.assertEqual(context.exception.code, TranscriptionErrorCode.TRANSCRIPTION_FAILED)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception))
        self.assertNotIn(secret, repr(configuration))
        self.assertNotIn(secret, repr(service))
        self.assertIsNone(context.exception.__cause__)
        self.assertIsNone(context.exception.__context__)

    @patch("psyclaw.litellm_transcription.litellm.atranscription", new_callable=AsyncMock)
    def test_malformed_provider_response_maps_to_safe_contract_error(
        self, transcription: AsyncMock
    ) -> None:
        transcription.return_value = {"unexpected": "response"}

        with self.assertRaises(TranscriptionError) as context:
            asyncio.run(
                self._service("openai/whisper-1").transcribe(
                    TranscriptionRequest(b"audio", "audio/webm", "req-1"),
                )
            )

        self.assertEqual(
            context.exception.code, TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )
        self.assertIsNone(context.exception.__cause__)
        self.assertIsNone(context.exception.__context__)

    @patch("psyclaw.litellm_transcription.litellm.atranscription", new_callable=AsyncMock)
    def test_invalid_provider_and_response_error_shapes_are_rebuilt_safely(
        self, transcription: AsyncMock
    ) -> None:
        secret = "provider-secret-error-shape"
        invalid_provider_error = TranscriptionError(
            TranscriptionErrorCode.PROVIDER_UNAVAILABLE
        )
        invalid_provider_error.code = secret  # type: ignore[assignment]
        transcription.side_effect = invalid_provider_error
        service = self._service("openai/whisper-1")

        with self.assertRaises(TranscriptionError) as provider_context:
            asyncio.run(
                service.transcribe(
                    TranscriptionRequest(b"audio", "audio/webm", "req-1")
                )
            )

        self.assertEqual(
            provider_context.exception.code,
            TranscriptionErrorCode.TRANSCRIPTION_FAILED,
        )
        self.assertNotIn(secret, str(provider_context.exception))
        self.assertNotIn(secret, repr(provider_context.exception))
        self.assertIsNone(provider_context.exception.__cause__)
        self.assertIsNone(provider_context.exception.__context__)

        class HostileResponse(dict[str, object]):
            def get(self, key: str, default: object = None) -> object:
                error = TranscriptionError(
                    TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
                )
                error.code = secret  # type: ignore[assignment]
                try:
                    raise RuntimeError(secret)
                except RuntimeError as provider_error:
                    raise error from provider_error

        transcription.reset_mock(side_effect=True)
        transcription.return_value = HostileResponse()

        with self.assertRaises(TranscriptionError) as response_context:
            asyncio.run(
                service.transcribe(
                    TranscriptionRequest(b"audio", "audio/webm", "req-2")
                )
            )

        self.assertEqual(
            response_context.exception.code,
            TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE,
        )
        self.assertNotIn(secret, str(response_context.exception))
        self.assertNotIn(secret, repr(response_context.exception))
        self.assertIsNone(response_context.exception.__cause__)
        self.assertIsNone(response_context.exception.__context__)
