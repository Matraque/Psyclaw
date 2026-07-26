"""Provider-neutral, in-memory speech-to-text boundary.

This module deliberately has no HTTP, provider SDK, persistence, logging, or
ADK integration.  A product server can adapt its authenticated request into a
``TranscriptionRequest`` and explicitly choose one registered provider.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol


_MAX_REQUEST_ID_LENGTH = 128
_MAX_LANGUAGE_HINT_LENGTH = 32
_MAX_TRANSCRIPT_LENGTH = 100_000
_MAX_AUDIO_BYTES_LIMIT = 100 * 1024 * 1024
_MAX_DIAGNOSTICS = 16
_MAX_CONTENT_TYPE_LENGTH = 256
_MAX_CONTENT_TYPE_PARAMETERS = 8
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONTENT_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_MIME_TOKEN_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_LANGUAGE_HINT_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


class TranscriptionErrorCode(str, Enum):
    """Public, provider-independent failure categories."""

    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    AUDIO_TOO_LARGE = "audio_too_large"
    EMPTY_TRANSCRIPT = "empty_transcript"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TRANSCRIPTION_FAILED = "transcription_failed"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    CONFIGURATION_ERROR = "configuration_error"


class TranscriptionError(Exception):
    """An error that is safe to expose to an API caller.

    Provider exception text is intentionally not retained: it can contain
    credentials, audio metadata, or other sensitive request details.
    """

    def __init__(self, code: TranscriptionErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class TranscriptionCapabilities:
    """Explicit size and media-type limits for one configured service."""

    supported_content_types: frozenset[str]
    max_audio_bytes: int

    def __post_init__(self) -> None:
        normalized_types = frozenset(
            _validate_content_type(content_type, allow_parameters=False)
            for content_type in self.supported_content_types
        )
        if not normalized_types:
            raise ValueError("At least one supported content type is required.")
        if not isinstance(self.max_audio_bytes, int) or isinstance(
            self.max_audio_bytes, bool
        ):
            raise ValueError("max_audio_bytes must be an integer.")
        if not 0 < self.max_audio_bytes <= _MAX_AUDIO_BYTES_LIMIT:
            raise ValueError(
                f"max_audio_bytes must be between 1 and {_MAX_AUDIO_BYTES_LIMIT}."
            )
        object.__setattr__(self, "supported_content_types", normalized_types)


@dataclass(frozen=True)
class TranscriptionRequest:
    """Sensitive in-memory input passed from a server boundary to an STT service."""

    audio: bytes = field(repr=False, compare=False)
    content_type: str
    request_id: str = field(repr=False)
    language_hint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.audio, bytes) or not self.audio:
            raise TranscriptionError(TranscriptionErrorCode.INVALID_REQUEST)
        if len(self.audio) > _MAX_AUDIO_BYTES_LIMIT:
            raise TranscriptionError(TranscriptionErrorCode.AUDIO_TOO_LARGE)
        if not isinstance(self.content_type, str):
            raise TranscriptionError(TranscriptionErrorCode.INVALID_REQUEST)
        try:
            content_type = _validate_content_type(self.content_type)
        except ValueError:
            raise TranscriptionError(TranscriptionErrorCode.INVALID_REQUEST) from None
        object.__setattr__(self, "content_type", content_type)
        if (
            not isinstance(self.request_id, str)
            or not self.request_id
            or len(self.request_id) > _MAX_REQUEST_ID_LENGTH
            or not _TOKEN_PATTERN.fullmatch(self.request_id)
        ):
            raise TranscriptionError(TranscriptionErrorCode.INVALID_REQUEST)
        if self.language_hint is not None and (
            not isinstance(self.language_hint, str)
            or len(self.language_hint) > _MAX_LANGUAGE_HINT_LENGTH
            or not _LANGUAGE_HINT_PATTERN.fullmatch(self.language_hint)
        ):
            raise TranscriptionError(TranscriptionErrorCode.INVALID_REQUEST)


class TranscriptionDiagnosticCode(str, Enum):
    """The small, reviewable diagnostic vocabulary allowed across the boundary."""

    PARTIAL_RESULT = "partial_result"
    LANGUAGE_DETECTED = "language_detected"


@dataclass(frozen=True)
class TranscriptionDiagnostic:
    """A non-sensitive status value; never include provider messages or IDs."""

    code: TranscriptionDiagnosticCode
    value: str | bool

    def __post_init__(self) -> None:
        if not isinstance(self.code, TranscriptionDiagnosticCode):
            raise ValueError("A diagnostic code must be from the safe vocabulary.")
        if self.code is TranscriptionDiagnosticCode.PARTIAL_RESULT:
            if not isinstance(self.value, bool):
                raise ValueError("A partial-result diagnostic must be a boolean.")
            return
        if self.code is TranscriptionDiagnosticCode.LANGUAGE_DETECTED:
            if (
                not isinstance(self.value, str)
                or len(self.value) > _MAX_LANGUAGE_HINT_LENGTH
                or not _LANGUAGE_HINT_PATTERN.fullmatch(self.value)
            ):
                raise ValueError("A language diagnostic must be a valid language tag.")
            return
        raise ValueError("A diagnostic code must have a defined value shape.")


@dataclass(frozen=True)
class TranscriptionResult:
    """Validated text-only output. Its transcript is hidden from ``repr``."""

    text: str = field(repr=False)
    detected_language: str | None = None
    diagnostics: tuple[TranscriptionDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or len(self.text) > _MAX_TRANSCRIPT_LENGTH:
            raise TranscriptionError(TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE)
        if not self.text.strip():
            raise TranscriptionError(TranscriptionErrorCode.EMPTY_TRANSCRIPT)
        if self.detected_language is not None and (
            not isinstance(self.detected_language, str)
            or len(self.detected_language) > _MAX_LANGUAGE_HINT_LENGTH
            or not _LANGUAGE_HINT_PATTERN.fullmatch(self.detected_language)
        ):
            raise TranscriptionError(TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE)
        if (
            not isinstance(self.diagnostics, tuple)
            or len(self.diagnostics) > _MAX_DIAGNOSTICS
            or not all(
                isinstance(diagnostic, TranscriptionDiagnostic)
                for diagnostic in self.diagnostics
            )
        ):
            raise TranscriptionError(TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE)


class TranscriptionService(Protocol):
    """Port implemented by exactly one intentionally configured STT provider."""

    capabilities: TranscriptionCapabilities

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Return a text-only transcription or raise ``TranscriptionError``."""


class TranscriptionProviderFactory(Protocol):
    """Creates a service from provider-owned configuration, without a default."""

    def __call__(self, configuration: Mapping[str, Any]) -> TranscriptionService:
        """Create one configured service instance."""


class TranscriptionRegistry:
    """Explicit provider registry with no built-ins, default, or fallback chain."""

    def __init__(self) -> None:
        self._factories: dict[str, TranscriptionProviderFactory] = {}

    def register(self, provider_name: str, factory: TranscriptionProviderFactory) -> None:
        if (
            not isinstance(provider_name, str)
            or not provider_name
            or not _TOKEN_PATTERN.fullmatch(provider_name)
            or not callable(factory)
        ):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        if provider_name in self._factories:
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        self._factories[provider_name] = factory

    def create(
        self, provider_name: str, configuration: Mapping[str, Any]
    ) -> TranscriptionService:
        if not _is_token(provider_name):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        factory = self._factories.get(provider_name)
        if factory is None or not isinstance(configuration, Mapping):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        try:
            service = factory(MappingProxyType(dict(configuration)))
        except TranscriptionError:
            raise
        except Exception:
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR) from None
        if not _is_service(service):
            raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
        return service


async def transcribe(
    service: TranscriptionService, request: TranscriptionRequest
) -> TranscriptionResult:
    """Validate an in-memory request and normalize an untrusted provider result."""

    if not isinstance(request, TranscriptionRequest):
        raise TranscriptionError(TranscriptionErrorCode.INVALID_REQUEST)
    service_parts = _service_parts(service)
    if service_parts is None:
        raise TranscriptionError(TranscriptionErrorCode.CONFIGURATION_ERROR)
    capabilities, provider_transcribe = service_parts
    if request.content_type not in capabilities.supported_content_types:
        raise TranscriptionError(TranscriptionErrorCode.UNSUPPORTED_MEDIA_TYPE)
    if len(request.audio) > capabilities.max_audio_bytes:
        raise TranscriptionError(TranscriptionErrorCode.AUDIO_TOO_LARGE)
    normalized_error: TranscriptionError | None = None
    try:
        result = await provider_transcribe(request)
    except TranscriptionError as error:
        code = (
            error.code
            if isinstance(error.code, TranscriptionErrorCode)
            else TranscriptionErrorCode.TRANSCRIPTION_FAILED
        )
        normalized_error = TranscriptionError(code)
    except Exception:
        normalized_error = TranscriptionError(
            TranscriptionErrorCode.TRANSCRIPTION_FAILED
        )
    if normalized_error is not None:
        # Raise outside the provider exception handler so neither __cause__ nor
        # __context__ retains provider-owned details in logs or error objects.
        raise normalized_error from None
    return _validate_provider_result(result)


def _validate_content_type(content_type: str, *, allow_parameters: bool = True) -> str:
    """Return a canonical media type after strictly parsing bounded parameters.

    Parameters are accepted because browser uploads normally include values such
    as ``audio/webm; codecs=opus``.  They are intentionally not part of service
    capability matching: the configured capability is the canonical media type.
    """
    if (
        not isinstance(content_type, str)
        or not content_type
        or len(content_type) > _MAX_CONTENT_TYPE_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in content_type)
    ):
        raise ValueError("A content type must be a bounded printable string.")

    parts = content_type.strip().split(";")
    media_type = parts[0].strip().lower()
    if not _CONTENT_TYPE_PATTERN.fullmatch(media_type):
        raise ValueError("A content type must start with a valid media type.")
    if not allow_parameters and len(parts) != 1:
        raise ValueError("A capability content type cannot include parameters.")
    if len(parts) - 1 > _MAX_CONTENT_TYPE_PARAMETERS:
        raise ValueError("A content type has too many parameters.")

    parameter_names: set[str] = set()
    for raw_parameter in parts[1:]:
        parameter = raw_parameter.strip()
        if parameter.count("=") != 1:
            raise ValueError("A content type parameter must have one value.")
        name, value = (part.strip() for part in parameter.split("=", 1))
        normalized_name = name.lower()
        if (
            not _MIME_TOKEN_PATTERN.fullmatch(name)
            or not value
            or normalized_name in parameter_names
        ):
            raise ValueError("A content type parameter is malformed.")
        if value.startswith('"'):
            if len(value) < 2 or not value.endswith('"') or '"' in value[1:-1]:
                raise ValueError("A quoted content type parameter is malformed.")
        elif not _MIME_TOKEN_PATTERN.fullmatch(value):
            raise ValueError("A content type parameter value is malformed.")
        parameter_names.add(normalized_name)
    return media_type


def _validate_provider_result(result: object) -> TranscriptionResult:
    """Reconstruct provider output so frozen dataclasses are not trust anchors."""
    if not isinstance(result, TranscriptionResult):
        raise TranscriptionError(TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE)
    try:
        provider_diagnostics = result.diagnostics
        if (
            type(provider_diagnostics) is not tuple
            or len(provider_diagnostics) > _MAX_DIAGNOSTICS
        ):
            raise ValueError("Provider diagnostics must be a bounded tuple.")
        diagnostics = tuple(
            TranscriptionDiagnostic(diagnostic.code, diagnostic.value)
            for diagnostic in provider_diagnostics
        )
        return TranscriptionResult(
            text=result.text,
            detected_language=result.detected_language,
            diagnostics=diagnostics,
        )
    except Exception:
        raise TranscriptionError(TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE) from None


def _is_service(candidate: object) -> bool:
    return _service_parts(candidate) is not None


def _service_parts(
    candidate: object,
) -> tuple[TranscriptionCapabilities, Any] | None:
    try:
        capabilities = getattr(candidate, "capabilities", None)
        provider_transcribe = getattr(candidate, "transcribe", None)
    except Exception:
        return None
    if not isinstance(capabilities, TranscriptionCapabilities) or not callable(
        provider_transcribe
    ):
        return None
    return capabilities, provider_transcribe


def _is_token(value: object) -> bool:
    return isinstance(value, str) and bool(value) and bool(_TOKEN_PATTERN.fullmatch(value))
