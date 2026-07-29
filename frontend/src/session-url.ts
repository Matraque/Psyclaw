export type ThreadNavigator = {
  switchToThread(threadId: string): Promise<void>;
  switchToNewThread(): Promise<void>;
};

export type SessionRestorationResult =
  | { status: "restored" }
  | { status: "not-found" }
  | { status: "retryable-error" };

export function readSessionId(search: string): string | undefined {
  const value = new URLSearchParams(search).get("session");
  return value || undefined;
}

export function withSessionId(search: string, sessionId: string | undefined): string {
  const parameters = new URLSearchParams(search);
  if (sessionId) {
    parameters.set("session", sessionId);
  } else {
    parameters.delete("session");
  }

  const nextSearch = parameters.toString();
  return nextSearch ? `?${nextSearch}` : "";
}

export function syncSessionIdInUrl(sessionId: string | undefined): void {
  const nextSearch = withSessionId(window.location.search, sessionId);
  if (nextSearch === window.location.search) return;

  window.history.replaceState(
    window.history.state,
    "",
    `${window.location.pathname}${nextSearch}${window.location.hash}`,
  );
}

export async function restoreSession(
  sessionId: string | undefined,
  navigator: ThreadNavigator,
): Promise<SessionRestorationResult> {
  if (!sessionId) return { status: "restored" };

  try {
    await navigator.switchToThread(sessionId);
    return { status: "restored" };
  } catch (error) {
    if (!(error instanceof Error) || error.message !== "Session not found: 404") {
      return { status: "retryable-error" };
    }

    try {
      await navigator.switchToNewThread();
      return { status: "not-found" };
    } catch {
      return { status: "retryable-error" };
    }
  }
}
