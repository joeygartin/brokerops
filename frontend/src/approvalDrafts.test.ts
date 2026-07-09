import { afterEach, describe, expect, it } from "vitest";
import {
  anyDraftDirty,
  clearAllDrafts,
  clearDraft,
  getDraft,
  setDraft,
} from "./approvalDrafts";

afterEach(() => clearAllDrafts());

describe("approvalDrafts store", () => {
  it("stores and reads a draft body by id", () => {
    setDraft("a", "edited", "original");
    expect(getDraft("a")).toBe("edited");
    expect(getDraft("missing")).toBeUndefined();
  });

  it("is dirty only when a stored body differs from its original", () => {
    setDraft("a", "same", "same");
    expect(anyDraftDirty()).toBe(false);
    setDraft("a", "changed", "same");
    expect(anyDraftDirty()).toBe(true);
  });

  it("clearDraft drops a single entry; clearAllDrafts drops everything", () => {
    setDraft("a", "x", "orig");
    setDraft("b", "y", "orig");
    clearDraft("a");
    expect(getDraft("a")).toBeUndefined();
    expect(getDraft("b")).toBe("y");
    clearAllDrafts();
    expect(getDraft("b")).toBeUndefined();
    expect(anyDraftDirty()).toBe(false);
  });

  it("registers a hard-unload guard that blocks only when a draft is dirty", () => {
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    setDraft("a", "changed", "orig");
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });
});
