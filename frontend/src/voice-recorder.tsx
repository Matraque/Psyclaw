import { useCallback, useRef, useState } from "react";

import {
  type TranscriptionClient,
  TranscriptionClientError,
} from "./transcription";

type VoiceRecorderProps = {
  client?: TranscriptionClient;
  onTranscript: (text: string) => void;
};

type RecorderPhase = "idle" | "requesting" | "recording" | "transcribing" | "failure";

const MAX_AUDIO_BYTES = 100 * 1024 * 1024;
const MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
] as const;

function messageFor(error: unknown) {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "Microphone permission was denied. Allow it in your browser settings to try again.";
  }
  if (error instanceof TranscriptionClientError) {
    if (error.code === "audio_too_large") return "This recording is larger than the transcription limit.";
    if (error.code === "unsupported_media_type") return "This audio format is not supported by the local transcription service.";
    if (error.code === "configuration_error") return "The local transcription service is not ready.";
  }
  return "Transcription did not complete. You can retry or discard this recording.";
}

function chooseMimeType() {
  if (typeof MediaRecorder === "undefined") return undefined;
  return MIME_TYPES.find((mimeType) => MediaRecorder.isTypeSupported(mimeType));
}

export function VoiceRecorder({ client, onTranscript }: VoiceRecorderProps) {
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const discardRef = useRef(false);
  const retryBlobRef = useRef<Blob | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [phase, setPhase] = useState<RecorderPhase>("idle");
  const [notice, setNotice] = useState("");

  const releaseRecorder = useCallback(() => {
    const recorder = recorderRef.current;
    recorderRef.current = null;
    recorder?.stream.getTracks().forEach((track) => track.stop());
  }, []);

  const sendForTranscription = useCallback(
    async (audio: Blob) => {
      if (!client) {
        setPhase("failure");
        setNotice("Voice transcription needs a local transcription connection.");
        return;
      }
      if (!audio.size) {
        setPhase("failure");
        setNotice("No audio was captured. Try recording again.");
        return;
      }
      if (audio.size > MAX_AUDIO_BYTES) {
        setPhase("failure");
        setNotice("This recording is larger than the transcription limit.");
        return;
      }

      setPhase("transcribing");
      setNotice("Transcribing recording…");
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const transcript = await client.transcribe(audio, controller.signal);
        retryBlobRef.current = null;
        onTranscript(transcript);
        setPhase("idle");
        setNotice("Transcript added to your message. Review it before sending.");
      } catch (error) {
        if (controller.signal.aborted) return;
        retryBlobRef.current = audio;
        setPhase("failure");
        setNotice(messageFor(error));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [client, onTranscript],
  );

  const startRecording = useCallback(async () => {
    if (!client) {
      setPhase("failure");
      setNotice("Voice transcription needs a local transcription connection.");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setPhase("failure");
      setNotice("This browser cannot record audio for transcription.");
      return;
    }

    setPhase("requesting");
    setNotice("Requesting microphone permission…");
    let stream: MediaStream | undefined;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = chooseMimeType();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      chunksRef.current = [];
      discardRef.current = false;
      recorderRef.current = recorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        const shouldDiscard = discardRef.current;
        const audio = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType || "audio/webm" });
        chunksRef.current = [];
        releaseRecorder();
        if (shouldDiscard) {
          retryBlobRef.current = null;
          setPhase("idle");
          setNotice("Recording discarded.");
          return;
        }
        void sendForTranscription(audio);
      });
      recorder.start();
      setPhase("recording");
      setNotice("Recording. Your audio stays in memory until you transcribe or discard it.");
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      releaseRecorder();
      setPhase("failure");
      setNotice(messageFor(error));
    }
  }, [client, releaseRecorder, sendForTranscription]);

  const stopRecording = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") {
      setPhase("transcribing");
      setNotice("Preparing recording…");
      recorder.stop();
    }
  }, []);

  const discardRecording = useCallback(() => {
    retryBlobRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") {
      discardRef.current = true;
      recorder.stop();
      return;
    }
    setPhase("idle");
    setNotice("Recording discarded.");
  }, []);

  const retry = useCallback(() => {
    const audio = retryBlobRef.current;
    if (audio) void sendForTranscription(audio);
    else void startRecording();
  }, [sendForTranscription, startRecording]);

  return (
    <div className="voice-controls">
      {phase === "recording" ? (
        <>
          <button className="recording-button" type="button" onClick={stopRecording}>
            <span aria-hidden="true" className="recording-indicator" />
            Stop recording
          </button>
          <button className="voice-text-button" type="button" onClick={discardRecording}>Discard</button>
        </>
      ) : phase === "transcribing" ? (
        <button className="voice-text-button" type="button" onClick={discardRecording}>Discard recording</button>
      ) : (
        <button
          className="microphone-button"
          type="button"
          onClick={startRecording}
          disabled={phase === "requesting"}
          aria-label="Record a voice message"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="8" y="3" width="8" height="12" rx="4" />
            <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M8.5 21h7" />
          </svg>
          <span>Record</span>
        </button>
      )}
      {phase === "failure" && (
        <>
          <button className="voice-text-button" type="button" onClick={retry}>Retry</button>
          <button className="voice-text-button" type="button" onClick={discardRecording}>Discard</button>
        </>
      )}
      {notice && (
        <p className={phase === "failure" ? "voice-notice voice-error" : "voice-notice"} role={phase === "failure" ? "alert" : "status"}>
          {notice}
        </p>
      )}
    </div>
  );
}
