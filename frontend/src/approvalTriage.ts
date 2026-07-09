import { type ApprovalRequest } from "./client";

// Pure triage helpers for the approval inbox (BOP-028). The inbox is a
// volume-triage surface, so filtering, per-kind counts, and ordering are pulled
// out of the view into these framework-free functions — trivially unit-testable
// and reused by both the pending list and the decided-history view.

// Human labels for the approval kinds the inbox renders. An unknown kind falls
// back to its raw wire value so a new gate is still legible before it gets a
// label here.
const KIND_LABELS: Record<string, string> = {
  approve_marketing: "Marketing",
  approve_outbound_message: "Outbound message",
  approve_escalation: "Escalation",
  notify_agent: "Hot lead",
};

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

// Age filter buckets, in whole days. "all" is the no-op default; the others keep
// only approvals at least N days old (oldest, most-overdue work).
export type AgeFilter = "all" | "over1d" | "over3d" | "over7d";

const AGE_MIN_DAYS: Record<Exclude<AgeFilter, "all">, number> = {
  over1d: 1,
  over3d: 3,
  over7d: 7,
};

export const AGE_OPTIONS: { value: AgeFilter; label: string }[] = [
  { value: "all", label: "Any age" },
  { value: "over1d", label: "Over 1 day" },
  { value: "over3d", label: "Over 3 days" },
  { value: "over7d", label: "Over 7 days" },
];

const MS_PER_DAY = 86_400_000;

export function ageInDays(approval: ApprovalRequest, now: Date): number {
  return (now.getTime() - new Date(approval.created_at).getTime()) / MS_PER_DAY;
}

export type TriageFilters = {
  kind: string | "all";
  workflow: string | "all";
  age: AgeFilter;
};

export const NO_FILTERS: TriageFilters = { kind: "all", workflow: "all", age: "all" };

// Distinct values (kinds / workflows) present in a set, in first-seen order — the
// source of the filter dropdown options, so a filter only ever offers values that
// actually exist in the current inbox.
export function distinctKinds(approvals: ApprovalRequest[]): string[] {
  return [...new Set(approvals.map((a) => a.kind))];
}

export function distinctWorkflows(approvals: ApprovalRequest[]): string[] {
  return [...new Set(approvals.map((a) => a.workflow))];
}

// Per-kind counts over the UNFILTERED set, so each kind's badge shows its true
// total regardless of the active filter.
export function countByKind(approvals: ApprovalRequest[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const approval of approvals) {
    counts[approval.kind] = (counts[approval.kind] ?? 0) + 1;
  }
  return counts;
}

export function filterApprovals(
  approvals: ApprovalRequest[],
  filters: TriageFilters,
  now: Date,
): ApprovalRequest[] {
  return approvals.filter((a) => {
    if (filters.kind !== "all" && a.kind !== filters.kind) return false;
    if (filters.workflow !== "all" && a.workflow !== filters.workflow) return false;
    if (filters.age !== "all" && ageInDays(a, now) < AGE_MIN_DAYS[filters.age]) return false;
    return true;
  });
}

// Urgency of a deadline-adjacent gate: an escalation carries per-milestone
// `days_overdue` in its open payload, and the most-overdue milestone is how
// urgent the whole gate is. Every other kind scores 0 (no deadline signal), so
// this only ever reorders escalations relative to one another and to the base
// age order — never invents urgency where the payload carries none.
function urgencyScore(approval: ApprovalRequest): number {
  if (approval.kind !== "approve_escalation") return 0;
  const milestones = (approval.payload?.milestones ?? []) as { days_overdue?: number }[];
  return milestones.reduce((max, m) => Math.max(max, m.days_overdue ?? 0), 0);
}

// Triage order: most-overdue escalations first (the deadline-adjacent urgency the
// spec allows), then everything else oldest-first — the default that keeps the
// longest-waiting work at the top. A tuple comparison, so it stays a valid total
// order (transitive); non-escalations all score 0 and fall back to pure
// oldest-first among themselves. `id` is the final tiebreaker so equal-urgency,
// equal-timestamp rows have a deterministic order — otherwise focus/render order
// would churn with the fetch order across refetches.
export function sortForTriage(approvals: ApprovalRequest[]): ApprovalRequest[] {
  return [...approvals].sort((a, b) => {
    const byUrgency = urgencyScore(b) - urgencyScore(a);
    if (byUrgency !== 0) return byUrgency;
    const byAge = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
    if (byAge !== 0) return byAge;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

// Decided-history order: most-recently-decided first (a log, read newest-down),
// distinct from the pending list's oldest-first triage order.
export function sortByDecidedAt(approvals: ApprovalRequest[]): ApprovalRequest[] {
  return [...approvals].sort((a, b) => {
    const at = a.decided_at ? new Date(a.decided_at).getTime() : 0;
    const bt = b.decided_at ? new Date(b.decided_at).getTime() : 0;
    return bt - at;
  });
}
