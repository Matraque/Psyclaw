import { describe, expect, it } from "vitest";

import { getConnectionConfig } from "./config";

describe("getConnectionConfig", () => {
  it("is disconnected until every public direct-ADK value is explicit", () => {
    expect(getConnectionConfig({ VITE_ADK_URL: "http://127.0.0.1:8000" })).toEqual({
      mode: "disconnected",
      missing: ["VITE_ADK_APP_NAME", "VITE_ADK_USER_ID"],
    });
  });

  it("uses direct ADK mode without inventing provider configuration", () => {
    expect(
      getConnectionConfig({
        VITE_ADK_URL: " http://127.0.0.1:8000 ",
        VITE_ADK_APP_NAME: "psyclaw",
        VITE_ADK_USER_ID: "local-synthetic-user",
      }),
    ).toEqual({
      mode: "connected",
      adkUrl: "http://127.0.0.1:8000",
      appName: "psyclaw",
      userId: "local-synthetic-user",
    });
  });

  it("accepts an explicit loopback URL for the local transcription service", () => {
    expect(
      getConnectionConfig({
        VITE_ADK_URL: "http://127.0.0.1:8000",
        VITE_ADK_APP_NAME: "psyclaw",
        VITE_ADK_USER_ID: "local-synthetic-user",
        VITE_STT_URL: "http://127.0.0.1:8001",
      }),
    ).toMatchObject({ mode: "connected", sttUrl: "http://127.0.0.1:8001" });
  });

  it.each([
    "https://adk.example.test",
    "ftp://127.0.0.1:8000",
    "http://patient:secret@127.0.0.1:8000",
    "http://127.0.0.1:8000?session=private",
    "http://127.0.0.1:8000#private",
  ])("rejects an unsafe local-MVP ADK URL: %s", (adkUrl) => {
    expect(
      getConnectionConfig({
        VITE_ADK_URL: adkUrl,
        VITE_ADK_APP_NAME: "psyclaw",
        VITE_ADK_USER_ID: "local-synthetic-user",
      }),
    ).toEqual({ mode: "disconnected", missing: ["VITE_ADK_URL"] });
  });

  it("allows the credential-free demo only when it is intentionally enabled", () => {
    expect(getConnectionConfig({ VITE_PSYCLAW_DEMO: "true" })).toEqual({ mode: "demo" });
  });

  it("allows an explicit loopback transcription service in the credential-free demo", () => {
    expect(
      getConnectionConfig({
        VITE_PSYCLAW_DEMO: "true",
        VITE_STT_URL: "http://127.0.0.1:8001",
      }),
    ).toEqual({ mode: "demo", sttUrl: "http://127.0.0.1:8001" });
  });
});
