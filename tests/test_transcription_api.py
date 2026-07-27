import re
import unittest

from fastapi.testclient import TestClient

from psyclaw.transcription import (
    TranscriptionCapabilities,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    TranscriptionResult,
)
from psyclaw.transcription_api import MAX_AUDIO_BYTES, create_transcription_api


class RecordingService:
    capabilities = TranscriptionCapabilities(frozenset({"audio/webm"}), 64)

    def __init__(self, result: TranscriptionResult | None = None) -> None:
        self.result = result or TranscriptionResult(text="Synthetic transcript.")
        self.requests: list[TranscriptionRequest] = []

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.requests.append(request)
        return self.result


class FailingService(RecordingService):
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        raise TranscriptionError(TranscriptionErrorCode.TRANSCRIPTION_FAILED)


class TranscriptionApiTest(unittest.TestCase):
    def test_transcribes_one_raw_in_memory_audio_body(self) -> None:
        service = RecordingService()
        client = TestClient(create_transcription_api(lambda: service))

        response = client.post(
            "/transcriptions",
            content=b"synthetic-audio",
            headers={"content-type": "audio/webm; codecs=opus"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"text": "Synthetic transcript."})
        self.assertEqual(len(service.requests), 1)
        request = service.requests[0]
        self.assertEqual(request.audio, b"synthetic-audio")
        self.assertEqual(request.content_type, "audio/webm")
        self.assertRegex(request.request_id, re.compile(r"^[a-f0-9]{32}$"))

    def test_rejects_declared_audio_larger_than_the_explicit_service_limit(self) -> None:
        service = RecordingService()
        client = TestClient(create_transcription_api(lambda: service))

        response = client.post(
            "/transcriptions",
            content=b"small",
            headers={"content-type": "audio/webm", "content-length": "65"},
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"error": {"code": "audio_too_large"}})
        self.assertEqual(service.requests, [])

    def test_rejects_body_larger_than_the_limit_even_with_a_false_header(self) -> None:
        service = RecordingService()
        client = TestClient(create_transcription_api(lambda: service))

        response = client.post(
            "/transcriptions",
            content=b"x" * 65,
            headers={"content-type": "audio/webm", "content-length": "1"},
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"error": {"code": "audio_too_large"}})
        self.assertEqual(service.requests, [])

    def test_returns_only_safe_error_codes(self) -> None:
        secret = "provider-secret-that-must-not-escape"
        client = TestClient(create_transcription_api(lambda: FailingService()))

        response = client.post(
            "/transcriptions",
            content=b"audio",
            headers={"content-type": "audio/webm"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"error": {"code": "transcription_failed"}})
        self.assertNotIn(secret, response.text)

    def test_invalid_content_type_has_a_safe_error(self) -> None:
        client = TestClient(create_transcription_api(RecordingService))

        response = client.post("/transcriptions", content=b"audio")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": {"code": "invalid_request"}})

    def test_configuration_failure_is_safe(self) -> None:
        secret = "configuration-secret-that-must-not-escape"

        def broken_factory() -> RecordingService:
            raise RuntimeError(secret)

        client = TestClient(create_transcription_api(broken_factory))
        response = client.post(
            "/transcriptions", content=b"audio", headers={"content-type": "audio/webm"}
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": {"code": "configuration_error"}})
        self.assertNotIn(secret, response.text)

    def test_global_limit_remains_one_hundred_mebibytes(self) -> None:
        self.assertEqual(MAX_AUDIO_BYTES, 100 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
