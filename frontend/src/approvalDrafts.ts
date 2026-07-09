// Editable outbound-message draft bodies, lifted out of the (freely unmounted)
// approval card so no in-app path silently discards an edit (BOP-028). Kept in its
// own module — not the inbox view — so session teardown (authContext) can clear it
// without a view→context import inversion.
//
// Lifecycle: the card seeds its react-hook-form default from here and writes back
// on every keystroke, so an edit survives optimistic removal + rollback on a failed
// decide, filtering the card out and back, tab switches, and navigation to the
// permalink (same card, same store). An entry is cleared on a successful decide
// (consumed) and on every session-teardown path (sign-out / terminal-401), so an
// edited draft never crosses an operator/session boundary in a shared browser
// (ADR-0022 §Decision 6). Only a hard page unload can lose an edit, guarded below.
const drafts = new Map<string, { body: string; original: string }>();

export function getDraft(id: string): string | undefined {
  return drafts.get(id)?.body;
}

export function setDraft(id: string, body: string, original: string): void {
  drafts.set(id, { body, original });
}

export function clearDraft(id: string): void {
  drafts.delete(id);
}

// Clears every draft — used on session teardown and to reset state between tests.
export function clearAllDrafts(): void {
  drafts.clear();
}

export function anyDraftDirty(): boolean {
  for (const { body, original } of drafts.values()) {
    if (body !== original) return true;
  }
  return false;
}

// The hard-unload guard must outlive every card AND the inbox route itself — a
// draft can be dirty while no card is mounted (filtered out, on another tab/route,
// or mid optimistic-removal), and a reload/close would then lose it silently. So it
// is a single listener registered once at module load, reading the store directly
// and independent of any component lifecycle; it is inert (never preventDefault)
// whenever nothing is dirty.
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (event) => {
    if (anyDraftDirty()) event.preventDefault();
  });
}
