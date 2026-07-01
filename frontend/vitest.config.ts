import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Component + logic tests run under jsdom. `globals` lets specs use the bare
// `describe`/`it`/`expect`/`vi` names (mirrored into tsconfig `types`), and the
// setup file installs the jest-dom matchers.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
