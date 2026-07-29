// @vitest-environment node
import { describe, expect, it } from "vitest";

import config from "./vite.config";

describe("local Vite server", () => {
  it("uses the one explicit loopback origin accepted by the transcription service", () => {
    expect(config.server).toEqual({
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
    });
  });
});
