import {
  AssistantRuntimeProvider,
  AuiIf,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  type ChatModelAdapter,
  useLocalRuntime,
  useAui,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import {
  createAdkSessionAdapter,
  createAdkStream,
  useAdkRuntime,
} from "@assistant-ui/react-google-adk";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ConnectionConfig } from "./config";
import {
  readSessionId,
  restoreSession,
  type SessionRestorationResult,
  syncSessionIdInUrl,
} from "./session-url";
import { createTranscriptionClient, type TranscriptionClient } from "./transcription";
import { VoiceRecorder } from "./voice-recorder";

const demoModel: ChatModelAdapter = {
  async run() {
    return {
      content: [
        {
          type: "text",
          text: "## Local interface demonstration\n\n- **No message was sent** to an ADK server.\n- _Markdown_ stays readable with `local` formatting and [Assistant UI](https://www.assistant-ui.com/).\n\n<img src=\"invalid\" alt=\"Unsafe markup\">",
        },
      ],
    };
  },
};

function UserMessage() {
  return (
    <MessagePrimitive.Root className="message message-user">
      <MessagePrimitive.Parts />
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message message-assistant">
      <MessagePrimitive.Parts>
        {({ part }) =>
          part.type === "text" ? (
            <>
              <MarkdownTextPrimitive className="assistant-markdown" defer skipHtml />
              <MessagePartPrimitive.InProgress>
                <span className="stream-cursor" aria-label="Responding" />
              </MessagePartPrimitive.InProgress>
            </>
          ) : null
        }
      </MessagePrimitive.Parts>
      <MessagePrimitive.Error>
        <ErrorPrimitive.Root className="message-error">
          <ErrorPrimitive.Message />
        </ErrorPrimitive.Root>
      </MessagePrimitive.Error>
    </MessagePrimitive.Root>
  );
}

function Composer({ sttUrl, transcriptionClient }: { sttUrl?: string; transcriptionClient?: TranscriptionClient }) {
  const aui = useAui();
  const configuredTranscriptionClient = useMemo(
    () => (sttUrl ? createTranscriptionClient(sttUrl) : undefined),
    [sttUrl],
  );
  const voiceClient = transcriptionClient ?? configuredTranscriptionClient;

  const insertTranscript = (transcript: string) => {
    const composerText = aui.composer().getState().text;
    aui.composer().setText(
      composerText.trim() ? `${composerText.trim()}\n\n${transcript}` : transcript,
    );
  };

  return (
    <ThreadPrimitive.ViewportFooter className="composer-region">
      <AuiIf condition={(state) => state.thread.isRunning}>
        <p className="run-state" role="status">
          Responding.
        </p>
      </AuiIf>
      <ComposerPrimitive.Root className="composer" compact>
        <ComposerPrimitive.Input
          aria-label="Message Psyclaw"
          className="composer-input"
          placeholder="Write what is on your mind…"
          rows={1}
          cancelOnEscape
        />
        <div className="composer-actions">
          {voiceClient && (
            <VoiceRecorder
              client={voiceClient}
              onTranscript={insertTranscript}
            />
          )}
          <AuiIf condition={(state) => state.thread.isRunning}>
            <ComposerPrimitive.Cancel className="stop-button">Stop</ComposerPrimitive.Cancel>
          </AuiIf>
          <ComposerPrimitive.Send className="send-button">Send</ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
      <p className="composer-help">Enter sends · Shift + Enter adds a line</p>
    </ThreadPrimitive.ViewportFooter>
  );
}

function ConversationSurface({
  sttUrl,
  transcriptionClient,
  restorationWarning,
}: {
  sttUrl?: string;
  transcriptionClient?: TranscriptionClient;
  restorationWarning?: boolean;
}) {
  return (
    <section className="conversation" aria-label="Conversation">
      {restorationWarning ? (
        <p className="session-warning" role="status">
          This conversation is no longer available. A new conversation has been started.
        </p>
      ) : null}
      <ThreadPrimitive.Root className="thread">
        <ThreadPrimitive.Viewport className="thread-viewport" turnAnchor="top">
          <AuiIf condition={(state) => state.thread.isEmpty}>
            <div className="empty-state">
              <h1>Take your time.</h1>
            </div>
          </AuiIf>
          <ThreadPrimitive.Messages>
            {({ message }) => (message.role === "user" ? <UserMessage /> : <AssistantMessage />)}
          </ThreadPrimitive.Messages>
          <Composer sttUrl={sttUrl} transcriptionClient={transcriptionClient} />
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </section>
  );
}

export type SessionRestorationState = "pending" | "ready" | "retryable-error";

export function SessionRestorationGate({
  state,
  onRetry,
  children,
}: {
  state: SessionRestorationState;
  onRetry: () => void;
  children: ReactNode;
}) {
  if (state === "pending") {
    return (
      <section className="session-state" aria-live="polite" aria-label="Reopening conversation">
        Reopening your conversation…
      </section>
    );
  }

  if (state === "retryable-error") {
    return (
      <section className="session-state" role="alert">
        <p>We could not reopen this conversation. Please check your local server and try again.</p>
        <button className="session-retry" type="button" onClick={onRetry}>Retry</button>
      </section>
    );
  }

  return children;
}

function ConnectedConversation({ config, transcriptionClient }: { config: Extract<ConnectionConfig, { mode: "connected" }>; transcriptionClient?: TranscriptionClient }) {
  const [initialSessionId] = useState(() => readSessionId(window.location.search));
  const [restorationWarning, setRestorationWarning] = useState(false);
  const [restorationState, setRestorationState] = useState<SessionRestorationState>(
    initialSessionId ? "pending" : "ready",
  );
  const restorationStartedRef = useRef(false);
  const session = useMemo(
    () =>
      createAdkSessionAdapter({
        apiUrl: config.adkUrl,
        appName: config.appName,
        userId: config.userId,
      }),
    [config.adkUrl, config.appName, config.userId],
  );
  const onThreadIdChange = useCallback((threadId: string | undefined) => {
    syncSessionIdInUrl(threadId);
  }, []);
  const runtime = useAdkRuntime({
    stream: createAdkStream({
      api: config.adkUrl,
      appName: config.appName,
      userId: config.userId,
    }),
    sessionAdapter: session.adapter,
    load: session.load,
    onThreadIdChange,
  });

  const applyRestorationResult = useCallback((result: SessionRestorationResult) => {
    if (result.status === "not-found") {
      syncSessionIdInUrl(undefined);
      setRestorationWarning(true);
      setRestorationState("ready");
      return;
    }

    setRestorationState(result.status === "restored" ? "ready" : "retryable-error");
  }, []);

  const reopenConversation = useCallback(async () => {
    if (!initialSessionId) return;
    setRestorationState("pending");
    applyRestorationResult(await restoreSession(initialSessionId, runtime.threads));
  }, [applyRestorationResult, initialSessionId, runtime]);

  useEffect(() => {
    if (!initialSessionId || restorationStartedRef.current) return;
    restorationStartedRef.current = true;
    void reopenConversation();
  }, [initialSessionId, reopenConversation]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <SessionRestorationGate state={restorationState} onRetry={() => void reopenConversation()}>
        <ConversationSurface
          sttUrl={config.sttUrl}
          transcriptionClient={transcriptionClient}
          restorationWarning={restorationWarning}
        />
      </SessionRestorationGate>
    </AssistantRuntimeProvider>
  );
}

function DemoConversation({ sttUrl, transcriptionClient }: { sttUrl?: string; transcriptionClient?: TranscriptionClient }) {
  const runtime = useLocalRuntime(demoModel);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ConversationSurface sttUrl={sttUrl} transcriptionClient={transcriptionClient} />
    </AssistantRuntimeProvider>
  );
}

function DisconnectedState({ missing }: { missing: string[] }) {
  return (
    <section className="disconnected" aria-labelledby="connection-title">
      <p className="connection-status">
        <span aria-hidden="true" className="status-dot offline" />
        Local ADK connection needed
      </p>
      <div>
        <p className="eyebrow">Development setup</p>
        <h1 id="connection-title">Connect a local Psyclaw server.</h1>
        <p>
          Start the local Psyclaw server, then add the public connection values below to
          <code>frontend/.env.local</code>. This local MVP accepts only loopback HTTP(S)
          ADK URLs. Browser code never receives provider credentials.
        </p>
      </div>
      <pre aria-label="Required public environment variables">{missing
        .map((key) => `${key}=…`)
        .join("\n")}</pre>
      <p className="disconnected-note">Use <code>VITE_PSYCLAW_DEMO=true</code> for a credential-free visual check.</p>
    </section>
  );
}

export function Conversation({ config, transcriptionClient }: { config: ConnectionConfig; transcriptionClient?: TranscriptionClient }) {
  if (config.mode === "connected") return <ConnectedConversation config={config} transcriptionClient={transcriptionClient} />;
  if (config.mode === "demo") return <DemoConversation sttUrl={config.sttUrl} transcriptionClient={transcriptionClient} />;
  return <DisconnectedState missing={config.missing} />;
}
