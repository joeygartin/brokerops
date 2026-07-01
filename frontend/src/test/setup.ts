// Registers the jest-dom matchers (toBeInTheDocument, toBeDisabled, …) and
// clears the DOM + sessionStorage between specs so module-level state in
// auth.ts can't leak across tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
});
