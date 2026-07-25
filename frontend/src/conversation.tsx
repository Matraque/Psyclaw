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
} from "@assistant-ui/react";
import {
  createAdkSessionAdapter,
  createAdkStream,
  useAdkRuntime,
} from "@assistant-ui/react-google-adk";
import { useMemo } from "react";

import type { ConnectionConfig } from "./config";

const demoModel: ChatModelAdapter = {
  async run() {
    return {
      content: [
        {
          type: "text",
          text: "This is a local interface demonstration. No message was sent to an ADK server.",
        },
      ],
    };
  },
};

function StatusLine({ mode }: { mode: "connected" | "demo" }) {
  return (
    <p className="connection-status" aria-live="polite">
      <span aria-hidden="true" className={mode === "demo" ? "status-dot demo" : "status-dot"} />
      {mode === "demo" ? "Local interface demonstration" : "Connected to local ADK"}
    </p>
  );
}

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
            <p className="assistant-text">
              <MessagePartPrimitive.Text />
              <MessagePartPrimitive.InProgress>
                <span className="stream-cursor" aria-label="Responding" />
              </MessagePartPrimitive.InProgress>
            </p>
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

function Composer() {
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

function ConversationSurface({ mode }: { mode: "connected" | "demo" }) {
  return (
    <section className="conversation" aria-label="Conversation">
      <StatusLine mode={mode} />
      <ThreadPrimitive.Root className="thread">
        <ThreadPrimitive.Viewport className="thread-viewport" turnAnchor="top">
          <AuiIf condition={(state) => state.thread.isEmpty}>
            <div className="empty-state">
              <p className="eyebrow">Private local session</p>
              <h1>Start where you are.</h1>
              <p>Your conversation uses the providers and local session service you configure.</p>
            </div>
          </AuiIf>
          <ThreadPrimitive.Messages>
            {({ message }) => (message.role === "user" ? <UserMessage /> : <AssistantMessage />)}
          </ThreadPrimitive.Messages>
          <Composer />
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </section>
  );
}

function ConnectedConversation({ config }: { config: Extract<ConnectionConfig, { mode: "connected" }> }) {
  const session = useMemo(
    () =>
      createAdkSessionAdapter({
        apiUrl: config.adkUrl,
        appName: config.appName,
        userId: config.userId,
      }),
    [config.adkUrl, config.appName, config.userId],
  );
  const runtime = useAdkRuntime({
    stream: createAdkStream({
      api: config.adkUrl,
      appName: config.appName,
      userId: config.userId,
    }),
    sessionAdapter: session.adapter,
    load: session.load,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ConversationSurface mode="connected" />
    </AssistantRuntimeProvider>
  );
}

function DemoConversation() {
  const runtime = useLocalRuntime(demoModel);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ConversationSurface mode="demo" />
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
        <h1 id="connection-title">Connect a local ADK server.</h1>
        <p>
          Start the existing ADK Web server, then add the public connection values below to
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

export function Conversation({ config }: { config: ConnectionConfig }) {
  if (config.mode === "connected") return <ConnectedConversation config={config} />;
  if (config.mode === "demo") return <DemoConversation />;
  return <DisconnectedState missing={config.missing} />;
}
