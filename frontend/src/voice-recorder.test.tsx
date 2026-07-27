import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "./conversation";
import { TranscriptionClientError, type TranscriptionClient } from "./transcription";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

class MockMediaRecorder extends EventTarget {
  static instances: MockMediaRecorder[] = [];
  static isTypeSupported = vi.fn(() => true);
  readonly mimeType: string;
  state: RecordingState = "inactive";

  constructor(readonly stream: MediaStream, options?: MediaRecorderOptions) {
    super();
    this.mimeType = options?.mimeType ?? "audio/webm";
    MockMediaRecorder.instances.push(this);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.dispatchEvent(new Event("stop"));
  }

  emitAudio(audio = new Blob(["synthetic audio"], { type: "audio/webm" })) {
    const event = new Event("dataavailable");
    Object.defineProperty(event, "data", { value: audio });
    this.dispatchEvent(event);
  }
}

const track = { stop: vi.fn() } as unknown as MediaStreamTrack;
const stream = { getTracks: () => [track] } as unknown as MediaStream;

describe("voice recording", () => {
  beforeEach(() => {
    cleanup();
    globalThis.ResizeObserver = ResizeObserverStub;
    MockMediaRecorder.instances = [];
    MockMediaRecorder.isTypeSupported.mockClear();
    Object.defineProperty(globalThis, "MediaRecorder", { configurable: true, value: MockMediaRecorder });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("inserts a successful transcript into the editable composer without sending it", async () => {
    const transcribe = vi.fn<TranscriptionClient["transcribe"]>().mockResolvedValue("A synthetic transcript.");
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    MockMediaRecorder.instances[0].emitAudio();
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));

    await waitFor(() => expect(transcribe).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("textbox", { name: "Message Psyclaw" })).toHaveValue("A synthetic transcript.");
    expect(screen.getByText("Transcript added to your message. Review it before sending.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("appends a completed transcript to composer text changed while transcription was in flight", async () => {
    let resolveTranscript: ((transcript: string) => void) | undefined;
    const transcribe = vi.fn<TranscriptionClient["transcribe"]>().mockImplementation(
      () => new Promise<string>((resolve) => { resolveTranscript = resolve; }),
    );
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    MockMediaRecorder.instances[0].emitAudio();
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));
    await waitFor(() => expect(transcribe).toHaveBeenCalledTimes(1));

    const composer = screen.getByRole("textbox", { name: "Message Psyclaw" });
    fireEvent.change(composer, { target: { value: "Text written while waiting" } });
    resolveTranscript?.("Synthetic transcript.");

    await waitFor(() => expect(composer).toHaveValue("Text written while waiting\n\nSynthetic transcript."));
  });

  it("reports denied microphone permission and keeps text input usable", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")) },
    });
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe: vi.fn() }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Microphone permission was denied");
    const composer = screen.getByRole("textbox", { name: "Message Psyclaw" });
    fireEvent.change(composer, { target: { value: "Text still works" } });
    expect(composer).toHaveValue("Text still works");
  });

  it("keeps a failed recording in memory only for an explicit retry or discard", async () => {
    const transcribe = vi
      .fn<TranscriptionClient["transcribe"]>()
      .mockRejectedValueOnce(new TranscriptionClientError("transcription_failed"))
      .mockResolvedValueOnce("Recovered transcript.");
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    MockMediaRecorder.instances[0].emitAudio();
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Transcription did not complete");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(transcribe).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("textbox", { name: "Message Psyclaw" })).toHaveValue("Recovered transcript.");
  });
});
