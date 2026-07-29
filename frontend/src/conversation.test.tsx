import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Conversation, SessionRestorationGate } from "./conversation";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub;
Object.defineProperty(HTMLElement.prototype, "scrollTo", {
  configurable: true,
  value: () => {},
});

afterEach(() => {
  cleanup();
});

describe("Conversation", () => {
  it("keeps the standard Assistant UI composer keyboard-accessible in credential-free demo mode", async () => {
    render(<Conversation config={{ mode: "demo" }} />);

    const composer = screen.getByRole("textbox", { name: "Message Psyclaw" });
    expect(composer).toHaveAttribute("placeholder", "Write what is on your mind…");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(await screen.findByRole("heading", { name: "Take your time." })).toBeVisible();

    fireEvent.change(composer, { target: { value: "Synthetic check" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    expect(await screen.findByText(/No message was sent to an ADK server/)).toBeVisible();
  });

  it("blocks the composer while reopening a conversation", () => {
    render(
      <SessionRestorationGate state="pending" onRetry={() => {}}>
        <textarea aria-label="Message Psyclaw" />
      </SessionRestorationGate>,
    );

    expect(screen.getByLabelText("Reopening conversation")).toHaveTextContent("Reopening your conversation…");
    expect(screen.queryByRole("textbox", { name: "Message Psyclaw" })).not.toBeInTheDocument();
  });

  it("shows a retry action instead of the composer when reopening fails", () => {
    const retry = vi.fn();
    render(
      <SessionRestorationGate state="retryable-error" onRetry={retry}>
        <textarea aria-label="Message Psyclaw" />
      </SessionRestorationGate>,
    );

    expect(screen.queryByRole("textbox", { name: "Message Psyclaw" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("renders the composer after restoration", () => {
    render(
      <SessionRestorationGate state="ready" onRetry={() => {}}>
        <textarea aria-label="Message Psyclaw" />
      </SessionRestorationGate>,
    );

    expect(screen.getByRole("textbox", { name: "Message Psyclaw" })).toBeInTheDocument();
  });

  it("hides the microphone until a local transcription service is configured", () => {
    render(<Conversation config={{ mode: "demo" }} />);

    expect(screen.queryByRole("button", { name: "Record a voice message" })).not.toBeInTheDocument();
  });
});
