import { afterEach, describe, expect, it, vi } from "vitest";

import {
  readSessionId,
  restoreSession,
  syncSessionIdInUrl,
  withSessionId,
} from "./session-url";

describe("session URL helpers", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
  });

  it("reads only the opaque session ID from the query string", () => {
    expect(readSessionId("?session=adk-session-123&view=compact")).toBe("adk-session-123");
    expect(readSessionId("?view=compact")).toBeUndefined();
    expect(readSessionId("?session=")).toBeUndefined();
  });

  it("adds, replaces, and removes only the session query parameter", () => {
    expect(withSessionId("?view=compact", "opaque-id")).toBe("?view=compact&session=opaque-id");
    expect(withSessionId("?view=compact&session=old", "new-id")).toBe("?view=compact&session=new-id");
    expect(withSessionId("?view=compact&session=old", undefined)).toBe("?view=compact");
  });

  it("syncs the canonical thread ID from onThreadIdChange without changing other query parameters or the hash", () => {
    window.history.replaceState({}, "", "/chat?view=compact#composer");
    const replaceState = vi.spyOn(window.history, "replaceState");
    const setLocalStorage = vi.spyOn(Storage.prototype, "setItem");
    const setSessionStorage = vi.spyOn(window.sessionStorage, "setItem");

    syncSessionIdInUrl("opaque-id");

    expect(replaceState).toHaveBeenCalledWith({}, "", "/chat?view=compact&session=opaque-id#composer");
    expect(window.location.search).toBe("?view=compact&session=opaque-id");
    expect(setLocalStorage).not.toHaveBeenCalled();
    expect(setSessionStorage).not.toHaveBeenCalled();
  });

  it("does not write a duplicate history entry when the ID is already current", () => {
    window.history.replaceState({}, "", "/?session=opaque-id");
    const replaceState = vi.spyOn(window.history, "replaceState");

    syncSessionIdInUrl("opaque-id");

    expect(replaceState).not.toHaveBeenCalled();
  });
});

describe("restoreSession", () => {
  it("does not contact the runtime when no session was provided", async () => {
    const navigator = {
      switchToThread: vi.fn(),
      switchToNewThread: vi.fn(),
    };

    await expect(restoreSession(undefined, navigator)).resolves.toEqual({ status: "restored" });
    expect(navigator.switchToThread).not.toHaveBeenCalled();
    expect(navigator.switchToNewThread).not.toHaveBeenCalled();
  });

  it("restores the requested session through the public runtime API", async () => {
    const navigator = {
      switchToThread: vi.fn().mockResolvedValue(undefined),
      switchToNewThread: vi.fn(),
    };

    await expect(restoreSession("opaque-id", navigator)).resolves.toEqual({ status: "restored" });
    expect(navigator.switchToThread).toHaveBeenCalledOnce();
    expect(navigator.switchToThread).toHaveBeenCalledWith("opaque-id");
  });

  it("starts a new conversation after a confirmed 404", async () => {
    window.history.replaceState({}, "", "/?session=deleted-id&view=compact");
    const navigator = {
      switchToThread: vi.fn().mockRejectedValue(new Error("Session not found: 404")),
      switchToNewThread: vi.fn().mockResolvedValue(undefined),
    };

    await expect(restoreSession("deleted-id", navigator)).resolves.toEqual({ status: "not-found" });
    expect(navigator.switchToNewThread).toHaveBeenCalledOnce();
    expect(window.location.search).toBe("?session=deleted-id&view=compact");
  });

  it("keeps the session blocked when creating a new conversation fails after a confirmed 404", async () => {
    window.history.replaceState({}, "", "/?session=deleted-id&view=compact");
    const navigator = {
      switchToThread: vi.fn().mockRejectedValue(new Error("Session not found: 404")),
      switchToNewThread: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    };

    await expect(restoreSession("deleted-id", navigator)).resolves.toEqual({ status: "retryable-error" });
    expect(navigator.switchToNewThread).toHaveBeenCalledOnce();
    expect(window.location.search).toBe("?session=deleted-id&view=compact");
  });

  it("keeps the session and does not create a new conversation after a network error", async () => {
    window.history.replaceState({}, "", "/?session=existing-id&view=compact");
    const navigator = {
      switchToThread: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
      switchToNewThread: vi.fn(),
    };

    await expect(restoreSession("existing-id", navigator)).resolves.toEqual({ status: "retryable-error" });
    expect(navigator.switchToNewThread).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?session=existing-id&view=compact");
  });

  it("keeps the session and does not create a new conversation after a server error", async () => {
    const navigator = {
      switchToThread: vi.fn().mockRejectedValue(new Error("Session not found: 500")),
      switchToNewThread: vi.fn(),
    };

    await expect(restoreSession("existing-id", navigator)).resolves.toEqual({ status: "retryable-error" });
    expect(navigator.switchToNewThread).not.toHaveBeenCalled();
  });
});
