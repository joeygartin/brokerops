import { describe, expect, it } from "vitest";
import { homeFor, roleAtLeast, type Role } from "./roles";

const ROLES: Role[] = ["viewer", "operator", "admin"];

describe("roleAtLeast", () => {
  // The full hierarchy: a role clears every minimum at or below its own rank
  // (viewer < operator < admin) and nothing above it.
  const expected: Record<Role, Record<Role, boolean>> = {
    viewer: { viewer: true, operator: false, admin: false },
    operator: { viewer: true, operator: true, admin: false },
    admin: { viewer: true, operator: true, admin: true },
  };

  for (const role of ROLES) {
    for (const minimum of ROLES) {
      it(`${role} vs minimum ${minimum} → ${expected[role][minimum]}`, () => {
        expect(roleAtLeast(role, minimum)).toBe(expected[role][minimum]);
      });
    }
  }

  it("null role never clears any minimum (unauthenticated)", () => {
    for (const minimum of ROLES) {
      expect(roleAtLeast(null, minimum)).toBe(false);
    }
  });
});

describe("homeFor", () => {
  // BOP-030: each role lands on their work at "/".
  it("routes admin to the approval inbox", () => {
    expect(homeFor("admin")).toBe("/approvals");
  });

  it("routes operator to the deadline queue", () => {
    expect(homeFor("operator")).toBe("/deadlines");
  });

  it("routes viewer to search", () => {
    expect(homeFor("viewer")).toBe("/search");
  });

  it("defaults an unknown/absent role to the coordinator queue (PII-free)", () => {
    expect(homeFor(null)).toBe("/deadlines");
  });
});
