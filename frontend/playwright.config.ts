import { defineConfig, devices } from "@playwright/test";

// Browser smoke of the DEMO.md golden path (BOP-029). This is a spine, not a
// coverage suite — deep interaction coverage stays in vitest. The spec drives a
// real Chromium against the compose stack (frontend :5173 → api :8000), so no
// `webServer` is configured here: CI (and a local run) bring the stack up with
// `make demo` / `docker compose up` first, then invoke `playwright test`.
//
// Flake posture (task requirement): no sleeps — every wait is an explicit
// assertion on app state via Playwright's auto-waiting locators. `retries: 1` in
// CI only, so a genuine regression still fails the job while a rare timing blip
// (cold container, first paint) gets one retry with a trace captured.
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  // A workflow start + HITL approve round-trips through the api and Postgres, so
  // give assertions headroom over the 5s default without resorting to sleeps.
  expect: { timeout: 15_000 },
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
