const MAX_AUDIO_BYTES = 100 * 1024 * 1024;

export type TranscriptionClient = {
  transcribe(audio: Blob, signal?: AbortSignal): Promise<string>;
};

export class TranscriptionClientError extends Error {
  constructor(public readonly code: string) {
    super(code);
    this.name = "TranscriptionClientError";
  }
}

export function createTranscriptionClient(apiUrl: string): TranscriptionClient {
  return {
    async transcribe(audio: Blob, signal?: AbortSignal): Promise<string> {
      if (!audio.size) throw new TranscriptionClientError("invalid_request");
      if (audio.size > MAX_AUDIO_BYTES) {
        throw new TranscriptionClientError("audio_too_large");
      }

      let response: Response;
      try {
        response = await fetch(new URL("/transcriptions", apiUrl), {
          method: "POST",
          headers: { "Content-Type": audio.type || "audio/webm" },
          body: audio,
          signal,
        });
      } catch {
        throw new TranscriptionClientError("transcription_failed");
      }

      const body = await response.json().catch(() => undefined);
      if (!response.ok) {
        throw new TranscriptionClientError(errorCodeFrom(body));
      }
      if (!isTranscript(body)) {
        throw new TranscriptionClientError("invalid_provider_response");
      }
      return body.text;
    },
  };
}

function errorCodeFrom(body: unknown): string {
  if (
    typeof body === "object" &&
    body !== null &&
    "error" in body &&
    typeof body.error === "object" &&
    body.error !== null &&
    "code" in body.error &&
    typeof body.error.code === "string"
  ) {
    return body.error.code;
  }
  return "transcription_failed";
}

function isTranscript(body: unknown): body is { text: string } {
  return (
    typeof body === "object" &&
    body !== null &&
    "text" in body &&
    typeof body.text === "string" &&
    Boolean(body.text.trim())
  );
}
