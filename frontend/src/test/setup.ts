// Registers the jest-dom matchers (toBeInTheDocument, toBeDisabled, …) and
// clears the DOM + sessionStorage between specs so module-level state in
// auth.ts can't leak across tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom has no scroll implementation; the router touches window.scrollTo during
// navigation (scroll restoration), so stub it to keep specs quiet (BOP-025).
vi.stubGlobal("scrollTo", () => {});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
});
