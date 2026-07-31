import { useCallback, useEffect, useRef, useState } from "react";

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
const RECORDING_TIMESLICE_MS = 1_000;
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
  const capturedBytesRef = useRef(0);
  const discardRef = useRef(false);
  const captureInvalidRef = useRef(false);
  const captureFailureMessageRef = useRef("");
  const retryBlobRef = useRef<Blob | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const [phase, setPhase] = useState<RecorderPhase>("idle");
  const [notice, setNotice] = useState("");

  const releaseRecorder = useCallback((recorder = recorderRef.current) => {
    if (recorderRef.current === recorder) recorderRef.current = null;
    recorder?.stream.getTracks().forEach((track) => track.stop());
  }, []);

  const stopRecorder = useCallback((recorder: MediaRecorder) => {
    if (recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
        // A stopped recorder has nothing left to release beyond its tracks.
      }
    }
  }, []);

  const sendForTranscription = useCallback(
    async (audio: Blob, generation: number) => {
      if (!client) {
        if (generationRef.current === generation) {
          setPhase("failure");
          setNotice("Voice transcription needs a local transcription connection.");
        }
        return;
      }
      if (!audio.size) {
        if (generationRef.current === generation) {
          setPhase("failure");
          setNotice("No audio was captured. Try recording again.");
        }
        return;
      }
      if (audio.size > MAX_AUDIO_BYTES) {
        if (generationRef.current === generation) {
          setPhase("failure");
          setNotice("This recording is larger than the transcription limit.");
        }
        return;
      }

      if (generationRef.current !== generation) return;
      setPhase("transcribing");
      setNotice("Transcribing recording…");
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const transcript = await client.transcribe(audio, controller.signal);
        if (generationRef.current !== generation || controller.signal.aborted) return;
        retryBlobRef.current = null;
        onTranscript(transcript);
        setPhase("idle");
        setNotice("Transcript added to your message. Review it before sending.");
      } catch (error) {
        if (controller.signal.aborted || generationRef.current !== generation) return;
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
    const generation = ++generationRef.current;
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

    const mimeType = chooseMimeType();
    if (!mimeType) {
      setPhase("failure");
      setNotice("This browser does not provide an audio format supported by the local transcription service.");
      return;
    }

    setPhase("requesting");
    setNotice("Requesting microphone permission…");
    let stream: MediaStream | undefined;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (generationRef.current !== generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const recorder = new MediaRecorder(stream, { mimeType });
      chunksRef.current = [];
      capturedBytesRef.current = 0;
      discardRef.current = false;
      captureInvalidRef.current = false;
      captureFailureMessageRef.current = "";
      recorderRef.current = recorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (generationRef.current !== generation || discardRef.current || captureInvalidRef.current || !event.data.size) return;
        const nextSize = capturedBytesRef.current + event.data.size;
        if (nextSize > MAX_AUDIO_BYTES) {
          captureInvalidRef.current = true;
          captureFailureMessageRef.current = "This recording is larger than the transcription limit.";
          chunksRef.current = [];
          capturedBytesRef.current = 0;
          retryBlobRef.current = null;
          setPhase("failure");
          setNotice("This recording is larger than the transcription limit.");
          releaseRecorder(recorder);
          stopRecorder(recorder);
          return;
        }
        capturedBytesRef.current = nextSize;
        chunksRef.current.push(event.data);
      });
      recorder.addEventListener("error", () => {
        if (generationRef.current !== generation) return;
        captureInvalidRef.current = true;
        discardRef.current = true;
        captureFailureMessageRef.current = "Recording failed. You can retry or discard this recording.";
        chunksRef.current = [];
        capturedBytesRef.current = 0;
        retryBlobRef.current = null;
        releaseRecorder(recorder);
        stopRecorder(recorder);
        setPhase("failure");
        setNotice("Recording failed. You can retry or discard this recording.");
      });
      recorder.addEventListener("stop", () => {
        const shouldDiscard = discardRef.current;
        const captureFailed = captureInvalidRef.current;
        const audio = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType || "audio/webm" });
        chunksRef.current = [];
        capturedBytesRef.current = 0;
        releaseRecorder(recorder);
        if (generationRef.current !== generation) return;
        if (captureFailed) {
          setPhase("failure");
          setNotice(captureFailureMessageRef.current || "Recording failed. You can retry or discard this recording.");
          return;
        }
        if (shouldDiscard) {
          retryBlobRef.current = null;
          setPhase("idle");
          setNotice("Recording discarded.");
          return;
        }
        void sendForTranscription(audio, generation);
      });
      recorder.start(RECORDING_TIMESLICE_MS);
      setPhase("recording");
      setNotice("Recording. Your audio stays in memory until you transcribe or discard it.");
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      releaseRecorder();
      if (generationRef.current !== generation) return;
      setPhase("failure");
      setNotice(messageFor(error));
    }
  }, [client, releaseRecorder, sendForTranscription, stopRecorder]);

  const stopRecording = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") {
      setPhase("transcribing");
      setNotice("Preparing recording…");
      stopRecorder(recorder);
    }
  }, [stopRecorder]);

  const discardRecording = useCallback(() => {
    retryBlobRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    const recorder = recorderRef.current;
    if (recorder) {
      discardRef.current = true;
      if (recorder.state !== "inactive") stopRecorder(recorder);
      return;
    }
    setPhase("idle");
    setNotice("Recording discarded.");
  }, [stopRecorder]);

  const retry = useCallback(() => {
    const audio = retryBlobRef.current;
    if (audio) void sendForTranscription(audio, ++generationRef.current);
    else void startRecording();
  }, [sendForTranscription, startRecording]);

  useEffect(() => () => {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    retryBlobRef.current = null;
    chunksRef.current = [];
    capturedBytesRef.current = 0;
    discardRef.current = true;
    captureInvalidRef.current = true;
    const recorder = recorderRef.current;
    releaseRecorder(recorder);
    if (recorder) stopRecorder(recorder);
  }, [releaseRecorder, stopRecorder]);

  return (
    <div className="voice-recorder">
      {notice && (
        <p className={phase === "failure" ? "voice-notice voice-error" : "voice-notice"} role={phase === "failure" ? "alert" : "status"}>
          {notice}
        </p>
      )}
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
      </div>
    </div>
  );
}
