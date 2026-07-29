import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({
  getConnectionConfig: () => ({ mode: "disconnected", missing: ["VITE_ADK_URL"] }),
}));

import { App } from "./App";

describe("App", () => {
  it("does not render a chat composer until an explicit local server connection exists", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Connect a local Psyclaw server." })).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Message Psyclaw" })).not.toBeInTheDocument();
    expect(screen.getByText("VITE_ADK_URL=…")).toBeVisible();
  });
});
