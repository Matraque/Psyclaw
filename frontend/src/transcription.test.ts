import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createTranscriptionClient,
  TranscriptionClientError,
} from "./transcription";

describe("createTranscriptionClient", () => {
  afterEach(() => vi.restoreAllMocks());

  it("posts the in-memory browser blob to the local transcription boundary", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ text: "Synthetic transcript." }), { status: 200 }),
    );
    const audio = new Blob(["synthetic audio"], { type: "audio/webm" });

    await expect(
      createTranscriptionClient("http://127.0.0.1:8001").transcribe(audio),
    ).resolves.toBe("Synthetic transcript.");
    expect(fetchMock).toHaveBeenCalledWith(
      new URL("http://127.0.0.1:8001/transcriptions"),
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "audio/webm" },
        body: audio,
      }),
    );
  });

  it("maps a local safe error response to a retryable client error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "transcription_failed" } }), { status: 502 }),
    );

    await expect(
      createTranscriptionClient("http://127.0.0.1:8001").transcribe(
        new Blob(["synthetic audio"], { type: "audio/webm" }),
      ),
    ).rejects.toEqual(new TranscriptionClientError("transcription_failed"));
  });

  it("rejects an over-limit blob before the browser makes a request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const audio = new Blob(["synthetic audio"]);
    Object.defineProperty(audio, "size", { value: 100 * 1024 * 1024 + 1 });

    await expect(
      createTranscriptionClient("http://127.0.0.1:8001").transcribe(audio),
    ).rejects.toEqual(new TranscriptionClientError("audio_too_large"));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
