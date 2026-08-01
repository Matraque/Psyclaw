import type { AdkEvent, AdkMessage, AdkStreamCallback } from "@assistant-ui/react-google-adk";
import { describe, expect, it } from "vitest";

import {
  createPrivateNoteSafeSessionLoad,
  createPrivateNoteSafeStream,
  filterPrivateNoteEvents,
  filterPrivateNoteMessages,
} from "./adk-privacy";

async function collect<T>(items: AsyncIterable<T>): Promise<T[]> {
  const collected: T[] = [];
  for await (const item of items) collected.push(item);
  return collected;
}

async function* events(items: AdkEvent[]): AsyncGenerator<AdkEvent> {
  yield* items;
}

describe("ADK private note adapter", () => {
  it("removes only note-taker events from a live stream without changing order", async () => {
    const streamEvents: AdkEvent[] = [
      { id: "user", author: "user", content: { parts: [{ text: "Synthetic user message." }] } },
      { id: "call", author: "psyclaw_agent", content: { parts: [{ functionCall: { name: "note_taker", id: "call-1", args: {} } }] } },
      { id: "private-text", author: "note_taker", content: { parts: [{ text: "Synthetic private note." }] } },
      { id: "private-error", author: "note_taker", errorCode: "INTERNAL", errorMessage: "Synthetic private error." },
      { id: "result", author: "psyclaw_agent", content: { parts: [{ functionResponse: { name: "note_taker", id: "call-1", response: {} } }] } },
      { id: "final", author: "psyclaw_agent", content: { parts: [{ text: "Synthetic psychologist response." }] } },
      { id: "anonymous", content: { parts: [{ text: "Synthetic system event." }] } },
      { id: "other", author: "other_agent", content: { parts: [{ text: "Synthetic other response." }] } },
    ];

    await expect(collect(filterPrivateNoteEvents(events(streamEvents)))).resolves.toEqual([
      streamEvents[0],
      streamEvents[1],
      streamEvents[4],
      streamEvents[5],
      streamEvents[6],
      streamEvents[7],
    ]);
    expect(streamEvents).toHaveLength(8);
  });

  it("wraps the live stream before Assistant UI can accumulate private events", async () => {
    const source: AdkStreamCallback = async () => events([
      { id: "private", author: "note_taker", content: { parts: [{ text: "Synthetic private note." }] } },
      { id: "final", author: "psyclaw_agent", content: { parts: [{ text: "Synthetic psychologist response." }] } },
    ]);

    const stream = createPrivateNoteSafeStream(source);
    const result = await stream([], {
      abortSignal: new AbortController().signal,
      initialize: async () => ({ remoteId: "synthetic", externalId: undefined }),
    });

    await expect(collect(result)).resolves.toEqual([
      { id: "final", author: "psyclaw_agent", content: { parts: [{ text: "Synthetic psychologist response." }] } },
    ]);
  });

  it("removes only note-taker messages when a session is reloaded", async () => {
    const messages: AdkMessage[] = [
      { id: "user", type: "human", content: "Synthetic user message." },
      { id: "call", type: "ai", content: "", author: "psyclaw_agent", tool_calls: [{ id: "call-1", name: "note_taker", args: {} }] },
      { id: "private-text", type: "ai", content: "Synthetic private note.", author: "note_taker" },
      { id: "private-error", type: "ai", content: "Synthetic private error.", author: "note_taker", status: { type: "incomplete", reason: "error" } },
      { id: "result", type: "tool", content: "Synthetic tool result.", tool_call_id: "call-1", name: "note_taker" },
      { id: "final", type: "ai", content: "Synthetic psychologist response.", author: "psyclaw_agent" },
      { id: "anonymous", type: "ai", content: "Synthetic anonymous response." },
      { id: "other", type: "ai", content: "Synthetic other response.", author: "other_agent" },
    ];

    expect(filterPrivateNoteMessages(messages)).toEqual([
      messages[0],
      messages[1],
      messages[4],
      messages[5],
      messages[6],
      messages[7],
    ]);
    expect(messages).toHaveLength(8);

    const load = createPrivateNoteSafeSessionLoad(async () => ({ messages }));
    await expect(load("synthetic-session")).resolves.toEqual({
      messages: [messages[0], messages[1], messages[4], messages[5], messages[6], messages[7]],
    });
  });
});
