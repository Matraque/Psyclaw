import type {
  AdkEvent,
  AdkMessage,
  AdkStreamCallback,
} from "@assistant-ui/react-google-adk";

const privateNoteAuthor = "note_taker";

export function filterPrivateNoteEvents(events: AsyncIterable<AdkEvent>): AsyncGenerator<AdkEvent> {
  return (async function* () {
    for await (const event of events) {
      if (event.author !== privateNoteAuthor) yield event;
    }
  })();
}

export function filterPrivateNoteMessages(messages: readonly AdkMessage[]): AdkMessage[] {
  return messages.filter((message) => !("author" in message && message.author === privateNoteAuthor));
}

export function createPrivateNoteSafeStream(stream: AdkStreamCallback): AdkStreamCallback {
  return async (messages, config) => filterPrivateNoteEvents(await stream(messages, config));
}

type AdkSessionLoad = (sessionId: string) => Promise<{ messages: AdkMessage[] }>;

export function createPrivateNoteSafeSessionLoad(load: AdkSessionLoad): AdkSessionLoad {
  return async (sessionId) => {
    const session = await load(sessionId);
    return { ...session, messages: filterPrivateNoteMessages(session.messages) };
  };
}
