import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component + logic tests run under jsdom. `globals` lets specs use the bare
// `describe`/`it`/`expect`/`vi` names (mirrored into tsconfig `types`), and the
// setup file installs the jest-dom matchers. The `@/` alias mirrors vite/tsconfig
// so specs resolve the shared UI primitives (BOP-026). Tailwind is not run here
// (`css: false`) — tests assert on roles/text, never computed styles.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
