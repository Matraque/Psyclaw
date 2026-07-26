"""A single, explicitly configured in-memory LiteLLM transcription adapter.

This adapter intentionally has no provider defaults or fallback chain.  The
configured model must include the LiteLLM provider prefix, for example
``mistral/...`` or ``openai/...``.  Audio is sent to one ``atranscription``
call as an in-memory multipart tuple and is never written to disk.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import litellm

from psyclaw.transcription import (
    TranscriptionCapabilities,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    TranscriptionResult,
)


_MAX_MODEL_LENGTH = 512
_MAX_API_KEY_LENGTH = 4096
_MAX_API_BASE_LENGTH = 2048
_LITELLM_AUDIO_INPUTS = {
    "audio/mpeg": ("recording.mp3", "audio/mpeg"),
    "audio/ogg": ("recording.ogg", "audio/ogg"),
    "audio/wav": ("recording.wav", "audio/wav"),
    "audio/x-m4a": ("recording.m4a", "audio/x-m4a"),
    "video/mp4": ("recording.mp4", "video/mp4"),
    # Browsers label audio-only WebM blobs as audio/webm. LiteLLM ignores the
    # tuple MIME and infers video/webm from .webm, so declare that translation
    # explicitly while leaving the container bytes untouched.
    "audio/webm": ("recording.webm", "video/webm"),
    "video/webm": ("recording.webm", "video/webm"),
}


@dataclass(frozen=True)
class LiteLLMTranscriptionConfiguration:
    """All configuration required to make one STT request."""

    model: str
    api_key: str = field(repr=False)
    capabilities: TranscriptionCapabilities
    api_base: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not _is_provider_prefixed_model(self.model) or not _is_secret(self.api_key):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        if not isinstance(self.capabilities, TranscriptionCapabilities):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        if not self.capabilities.supported_content_types.issubset(
            _LITELLM_AUDIO_INPUTS
        ):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        if self.api_base is not None and not _is_api_base(self.api_base):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)


class LiteLLMTranscriptionService:
    """Transcribes one audio blob per call through LiteLLM's async interface."""

    def __init__(self, configuration: LiteLLMTranscriptionConfiguration) -> None:
        if not isinstance(configuration, LiteLLMTranscriptionConfiguration):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        self._configuration = configuration
        self.capabilities = configuration.capabilities

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Make one in-memory request and return its text-only result."""
        audio_input = _LITELLM_AUDIO_INPUTS.get(request.content_type)
        if audio_input is None:
            raise TranscriptionError(TranscriptionErrorCode.UNSUPPORTED_MEDIA_TYPE)
        filename, inferred_content_type = audio_input
        kwargs: dict[str, Any] = {
            "model": self._configuration.model,
            "file": (filename, request.audio, inferred_content_type),
            "api_key": self._configuration.api_key,
        }
        if request.language_hint is not None:
            kwargs["language"] = request.language_hint
        if self._configuration.api_base is not None:
            kwargs["api_base"] = self._configuration.api_base

        normalized_error: TranscriptionError | None = None
        try:
            response = await litellm.atranscription(**kwargs)
        except Exception:
            normalized_error = TranscriptionError(
                TranscriptionErrorCode.TRANSCRIPTION_FAILED
            )
        if normalized_error is not None:
            raise normalized_error from None

        return _result_from_response(response)


def create_litellm_transcription_service(
    configuration: Mapping[str, Any],
) -> LiteLLMTranscriptionService:
    """Build the adapter from explicit registry configuration, without defaults."""
    if not isinstance(configuration, Mapping):
        raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
    allowed_keys = {
        "model",
        "api_key",
        "api_base",
        "supported_content_types",
        "max_audio_bytes",
    }
    normalized_error: TranscriptionError | None = None
    service: LiteLLMTranscriptionService | None = None
    try:
        if set(configuration) - allowed_keys:
            raise ValueError("Unknown LiteLLM transcription configuration key.")
        capabilities = TranscriptionCapabilities(
            supported_content_types=frozenset(configuration["supported_content_types"]),
            max_audio_bytes=configuration["max_audio_bytes"],
        )
        adapter_configuration = LiteLLMTranscriptionConfiguration(
            model=configuration["model"],
            api_key=configuration["api_key"],
            api_base=configuration.get("api_base"),
            capabilities=capabilities,
        )
        service = LiteLLMTranscriptionService(adapter_configuration)
    except (KeyError, TypeError, ValueError, TranscriptionError):
        normalized_error = TranscriptionError(
            TranscriptionErrorCode.CONFIGURATION_ERROR
        )
    if normalized_error is not None:
        raise normalized_error from None
    if service is None:
        raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
    return service


def load_litellm_transcription_configuration(
    environment: Mapping[str, str],
    *,
    capabilities: TranscriptionCapabilities,
) -> LiteLLMTranscriptionConfiguration:
    """Load generic STT settings from an explicitly supplied environment mapping.

    The composition root deliberately chooses when and from where to read the
    process environment.  Provider capabilities remain an explicit application
    decision and are never inferred from the provider-prefixed model string.
    """
    if not isinstance(environment, Mapping):
        raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
    normalized_error: TranscriptionError | None = None
    configuration: LiteLLMTranscriptionConfiguration | None = None
    try:
        configuration = LiteLLMTranscriptionConfiguration(
            model=environment["PSYCLAW_STT_MODEL"],
            api_key=environment["PSYCLAW_STT_API_KEY"],
            api_base=environment.get("PSYCLAW_STT_API_BASE"),
            capabilities=capabilities,
        )
    except (KeyError, TypeError, ValueError, TranscriptionError):
        normalized_error = TranscriptionError(
            TranscriptionErrorCode.CONFIGURATION_ERROR
        )
    if normalized_error is not None:
        raise normalized_error from None
    if configuration is None:
        raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
    return configuration


def _result_from_response(response: object) -> TranscriptionResult:
    """Extract only the documented text field from an untrusted SDK response."""
    normalized_error: TranscriptionError | None = None
    result: TranscriptionResult | None = None
    try:
        text = (
            response.get("text")
            if isinstance(response, Mapping)
            else getattr(response, "text")
        )
        result = TranscriptionResult(text=text)
    except TranscriptionError as error:
        try:
            code = error.code
        except Exception:
            code = None
        normalized_error = TranscriptionError(
            TranscriptionErrorCode.EMPTY_TRANSCRIPT
            if code is TranscriptionErrorCode.EMPTY_TRANSCRIPT
            else TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )
    except Exception:
        normalized_error = TranscriptionError(
            TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE
        )
    if normalized_error is not None:
        raise normalized_error from None
    if result is None:
        raise TranscriptionError(TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE)
    return result


def _is_provider_prefixed_model(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_MODEL_LENGTH
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        return False
    provider, separator, model = value.partition("/")
    return bool(separator and provider and model)


def _is_secret(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_API_KEY_LENGTH
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _is_api_base(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > _MAX_API_BASE_LENGTH:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not (
        parsed.username or parsed.password
    )
