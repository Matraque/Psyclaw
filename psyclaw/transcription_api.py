"""Local, in-memory HTTP boundary for browser speech-to-text requests."""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from psyclaw.litellm_transcription import (
    LiteLLMTranscriptionService,
    load_litellm_transcription_configuration,
)
from psyclaw.transcription import (
    TranscriptionCapabilities,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    TranscriptionResult,
    transcribe,
)


MAX_AUDIO_BYTES = 100 * 1024 * 1024
SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/x-m4a",
        "audio/webm",
        "video/mp4",
        "video/webm",
    }
)
LOCAL_UI_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")


class TranscriptionService(Protocol):
    capabilities: TranscriptionCapabilities

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Return a text-only transcript for one in-memory recording."""


def create_local_transcription_service(
    environment: Mapping[str, str] | None = None,
) -> LiteLLMTranscriptionService:
    """Build the one configured STT service without provider defaults."""
    if environment is None:
        load_dotenv(Path(__file__).resolve().parent / ".env")
        environment = os.environ
    capabilities = TranscriptionCapabilities(
        supported_content_types=SUPPORTED_CONTENT_TYPES,
        max_audio_bytes=MAX_AUDIO_BYTES,
    )
    configuration = load_litellm_transcription_configuration(
        environment,
        capabilities=capabilities,
    )
    return LiteLLMTranscriptionService(configuration)


def create_transcription_api(
    service_factory: Callable[[], TranscriptionService] = create_local_transcription_service,
) -> FastAPI:
    """Create a loopback-only API that never writes or logs audio bytes."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(LOCAL_UI_ORIGINS),
        allow_credentials=False,
        allow_methods=["POST"],
        allow_headers=["Content-Type"],
    )

    @app.post("/transcriptions")
    async def create_transcription(request: Request) -> JSONResponse:
        service = _service_or_error(service_factory)
        if isinstance(service, JSONResponse):
            return service
        if not isinstance(getattr(service, "capabilities", None), TranscriptionCapabilities):
            return _error_response(TranscriptionErrorCode.CONFIGURATION_ERROR)

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                return _error_response(TranscriptionErrorCode.INVALID_REQUEST)
            if declared_size < 0:
                return _error_response(TranscriptionErrorCode.INVALID_REQUEST)
            if declared_size > service.capabilities.max_audio_bytes:
                return _error_response(TranscriptionErrorCode.AUDIO_TOO_LARGE)

        audio = await _read_bounded_audio(
            request, service.capabilities.max_audio_bytes
        )
        if audio is None:
            return _error_response(TranscriptionErrorCode.AUDIO_TOO_LARGE)
        try:
            result = await transcribe(
                service,
                TranscriptionRequest(
                    audio=audio,
                    content_type=request.headers.get("content-type", ""),
                    request_id=secrets.token_hex(16),
                ),
            )
        except TranscriptionError as error:
            return _error_response(error.code)
        finally:
            del audio

        return JSONResponse({"text": result.text})

    return app


async def _read_bounded_audio(request: Request, maximum_size: int) -> bytes | None:
    """Read one request into memory without trusting its declared length.

    A browser normally sends ``Content-Length``, which is rejected above when it
    exceeds the configured limit.  This guard also covers omitted or false
    headers, so the process never first buffers an arbitrary upload.
    """
    audio = bytearray()
    try:
        async for chunk in request.stream():
            if len(audio) + len(chunk) > maximum_size:
                return None
            audio.extend(chunk)
    except Exception:
        return None
    return bytes(audio)


def _service_or_error(
    service_factory: Callable[[], TranscriptionService],
) -> TranscriptionService | JSONResponse:
    try:
        return service_factory()
    except Exception:
        return _error_response(TranscriptionErrorCode.CONFIGURATION_ERROR)


def _error_response(code: TranscriptionErrorCode) -> JSONResponse:
    status_codes = {
        TranscriptionErrorCode.INVALID_REQUEST: 400,
        TranscriptionErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
        TranscriptionErrorCode.AUDIO_TOO_LARGE: 413,
        TranscriptionErrorCode.EMPTY_TRANSCRIPT: 422,
        TranscriptionErrorCode.CONFIGURATION_ERROR: 503,
        TranscriptionErrorCode.PROVIDER_UNAVAILABLE: 503,
        TranscriptionErrorCode.TRANSCRIPTION_FAILED: 502,
        TranscriptionErrorCode.INVALID_PROVIDER_RESPONSE: 502,
    }
    return JSONResponse(
        status_code=status_codes.get(code, 502),
        content={"error": {"code": code.value}},
    )


app = create_transcription_api()
