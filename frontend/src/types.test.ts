import { describe, expect, it } from "vitest";
import { roleAtLeast, type Role } from "./types";

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
