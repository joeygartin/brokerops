// Operator authorization level (mirrors the backend's Role). This is the one
// wire shape that cannot come from the generated client yet: /auth/me is typed
// `dict[str, object]` on the backend, so the OpenAPI spec carries no Role
// schema to generate from (ADR-0018). Hierarchical: admin > operator > viewer.
// The API is the security boundary; the UI uses this only to hide controls a
// role can't use.
export type Role = "viewer" | "operator" | "admin";

const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

export function roleAtLeast(role: Role | null, minimum: Role): boolean {
  return role != null && ROLE_RANK[role] >= ROLE_RANK[minimum];
}

// The route each role lands on at "/" (BOP-030): the persona's work, not a tab
// bar. The broker (admin) lives in the approval inbox; the coordinator (operator)
// opens the cross-transaction deadline queue; the viewer starts at search. An
// unknown/absent role defaults to the coordinator queue — the safe, PII-free work
// surface (a stale token resolves to viewer server-side regardless).
export function homeFor(role: Role | null): "/approvals" | "/deadlines" | "/search" {
  if (role === "admin") return "/approvals";
  if (role === "viewer") return "/search";
  return "/deadlines";
}
