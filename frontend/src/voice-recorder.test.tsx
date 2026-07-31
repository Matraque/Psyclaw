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
  static isTypeSupported = vi.fn<(mimeType: string) => boolean>(() => true);
  readonly mimeType: string;
  state: RecordingState = "inactive";
  startTimeslice: number | undefined;

  constructor(readonly stream: MediaStream, options?: MediaRecorderOptions) {
    super();
    this.mimeType = options?.mimeType ?? "audio/webm";
    MockMediaRecorder.instances.push(this);
  }

  start(timeslice?: number) {
    this.startTimeslice = timeslice;
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

  emitError() {
    this.dispatchEvent(new Event("error"));
  }
}

const track = { stop: vi.fn() } as unknown as MediaStreamTrack;
const stream = { getTracks: () => [track] } as unknown as MediaStream;

describe("voice recording", () => {
  beforeEach(() => {
    cleanup();
    globalThis.ResizeObserver = ResizeObserverStub;
    MockMediaRecorder.instances = [];
    MockMediaRecorder.isTypeSupported.mockReset();
    MockMediaRecorder.isTypeSupported.mockReturnValue(true);
    Object.defineProperty(globalThis, "MediaRecorder", { configurable: true, value: MockMediaRecorder });
    (track.stop as ReturnType<typeof vi.fn>).mockReset();
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
    const successNotice = screen.getByRole("status");
    expect(successNotice).toHaveTextContent("Transcript added to your message. Review it before sending.");
    expect(successNotice).toBeVisible();
    expect(successNotice.closest(".voice-recorder")).not.toBeNull();
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

  it("keeps a completed transcript separate from text written while recording", async () => {
    const transcribe = vi.fn<TranscriptionClient["transcribe"]>().mockResolvedValue("Synthetic transcript.");
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    const composer = screen.getByRole("textbox", { name: "Message Psyclaw" });
    fireEvent.change(composer, { target: { value: "Text written while recording" } });
    MockMediaRecorder.instances[0].emitAudio();
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));

    await waitFor(() => expect(composer).toHaveValue("Text written while recording\n\nSynthetic transcript."));
  });

  it("reports denied microphone permission and keeps text input usable", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")) },
    });
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe: vi.fn() }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));

    const errorNotice = await screen.findByRole("alert");
    expect(errorNotice).toHaveTextContent("Microphone permission was denied");
    expect(errorNotice.closest(".voice-recorder")).not.toBeNull();
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

  it("uses short chunks but has no arbitrary recording duration limit", async () => {
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe: vi.fn() }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });

    expect(MockMediaRecorder.instances[0].startTimeslice).toBe(1_000);
  });

  it("allows audio exactly at the byte cap and rejects the next byte before transcription", async () => {
    const transcribe = vi.fn<TranscriptionClient["transcribe"]>().mockResolvedValue("Synthetic transcript.");
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    const exactCap = new Blob(["x"], { type: "audio/webm" });
    Object.defineProperty(exactCap, "size", { value: 100 * 1024 * 1024 });
    MockMediaRecorder.instances[0].emitAudio(exactCap);
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));
    await waitFor(() => expect(transcribe).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    const overCap = new Blob(["x"], { type: "audio/webm" });
    Object.defineProperty(overCap, "size", { value: 100 * 1024 * 1024 + 1 });
    MockMediaRecorder.instances[1].emitAudio(overCap);

    expect(await screen.findByRole("alert")).toHaveTextContent("larger than the transcription limit");
    expect(transcribe).toHaveBeenCalledTimes(1);
  });

  it("discards partial data and never transcribes after a recorder error", async () => {
    const transcribe = vi.fn<TranscriptionClient["transcribe"]>();
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    const recorder = MockMediaRecorder.instances[0];
    recorder.emitError();
    recorder.emitAudio();
    recorder.stop();

    expect(await screen.findByRole("alert")).toHaveTextContent("Recording failed");
    expect(transcribe).not.toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalled();
  });

  it("does not request the microphone when the browser has no supported audio MIME", async () => {
    MockMediaRecorder.isTypeSupported.mockReturnValue(false);
    const getUserMedia = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe: vi.fn() }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("does not provide an audio format supported");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("does not fall back to Safari's audio/mp4 before the service supports it", async () => {
    MockMediaRecorder.isTypeSupported.mockImplementation((mimeType: string) => mimeType === "audio/mp4");
    const getUserMedia = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe: vi.fn() }} />);

    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("does not provide an audio format supported");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("cleans up an active recording when the composer unmounts", async () => {
    const { unmount } = render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe: vi.fn() }} />);
    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });

    unmount();

    expect(track.stop).toHaveBeenCalled();
  });

  it("releases a stream that arrives after the composer unmounts", async () => {
    let resolveStream: ((value: MediaStream) => void) | undefined;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn(() => new Promise<MediaStream>((resolve) => { resolveStream = resolve; })) },
    });
    const { unmount } = render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe: vi.fn() }} />);
    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));

    unmount();
    resolveStream?.(stream);

    await waitFor(() => expect(track.stop).toHaveBeenCalled());
    expect(MockMediaRecorder.instances).toHaveLength(0);
  });

  it("aborts an in-flight transcription and ignores its late completion after unmount", async () => {
    let resolveTranscript: ((value: string) => void) | undefined;
    let signal: AbortSignal | undefined;
    const transcribe = vi.fn<TranscriptionClient["transcribe"]>().mockImplementation((_audio, receivedSignal) => {
      signal = receivedSignal;
      return new Promise<string>((resolve) => { resolveTranscript = resolve; });
    });
    const { unmount } = render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe }} />);
    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    MockMediaRecorder.instances[0].emitAudio();
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));
    await waitFor(() => expect(transcribe).toHaveBeenCalledTimes(1));

    unmount();
    resolveTranscript?.("Late synthetic transcript.");

    expect(signal?.aborted).toBe(true);
  });

  it("aborts an in-flight transcription when the recording is discarded", async () => {
    let signal: AbortSignal | undefined;
    const transcribe = vi.fn<TranscriptionClient["transcribe"]>().mockImplementation((_audio, receivedSignal) => {
      signal = receivedSignal;
      return new Promise<string>(() => {});
    });
    render(<Conversation config={{ mode: "demo" }} transcriptionClient={{ transcribe }} />);
    fireEvent.click(screen.getByRole("button", { name: "Record a voice message" }));
    await screen.findByRole("button", { name: "Stop recording" });
    MockMediaRecorder.instances[0].emitAudio();
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));
    await waitFor(() => expect(transcribe).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Discard recording" }));

    expect(signal?.aborted).toBe(true);
    expect(screen.getByText("Recording discarded.")).toBeVisible();
  });
});
