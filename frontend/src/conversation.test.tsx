import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it } from "vitest";

import { Conversation } from "./conversation";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub;

describe("Conversation", () => {
  it("keeps the standard Assistant UI composer keyboard-accessible in credential-free demo mode", async () => {
    render(<Conversation config={{ mode: "demo" }} />);

    const composer = screen.getByRole("textbox", { name: "Message Psyclaw" });
    expect(composer).toHaveAttribute("placeholder", "Write what is on your mind…");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    fireEvent.change(composer, { target: { value: "Synthetic check" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    expect(await screen.findByText(/No message was sent to an ADK server/)).toBeVisible();
    expect(screen.getByText("Local interface demonstration")).toHaveAttribute("aria-live", "polite");
  });
});
