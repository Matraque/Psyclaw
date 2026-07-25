import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "assistant-ui": ["@assistant-ui/react", "@assistant-ui/react-google-adk"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
  },
});
