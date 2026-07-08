import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Enforces the design-token accessibility floor (BOP-026, ADR-0024): every
// foreground/background pairing the UI actually renders must clear WCAG AA
// (contrast ≥ 4.5:1 for text). The palette in index.css is the single source of
// truth — this test parses it, so a future token tweak that quietly breaks
// contrast fails CI instead of shipping. "Checked, not eyeballed."

// Vitest runs with cwd at the frontend package root; the tokens file is the
// palette's single source of truth.
const css = readFileSync(resolve("src/index.css"), "utf8");

// Pull every `--color-<name>: #rrggbb;` declaration out of the tokens file.
const tokens: Record<string, string> = {};
for (const match of css.matchAll(/--color-([a-z-]+):\s*(#[0-9a-fA-F]{6});/g)) {
  tokens[match[1]] = match[2];
}

function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const channels = [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(fg: string, bg: string): number {
  const a = luminance(fg);
  const b = luminance(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

// [foreground token, background token] pairs, matching how the components pair
// them (bg-<x> with text-<x>-foreground, or a coloured text token on a surface).
const PAIRS: Array<[string, string]> = [
  // Body + surfaces
  ["foreground", "background"],
  ["card-foreground", "card"],
  ["muted-foreground", "card"],
  ["muted-foreground", "background"],
  ["muted-foreground", "muted"], // secondary badge, complete/waived, stage, role
  // Solid intents (white text)
  ["primary-foreground", "primary"],
  ["success-foreground", "success"],
  ["warning-foreground", "warning"],
  ["destructive-foreground", "destructive"],
  ["blocked-foreground", "blocked"],
  ["strong-foreground", "strong"], // nav-active tab, timeline toggle
  // Soft intent surfaces
  ["info-soft-foreground", "info-soft"], // channel / kind chips, notices
  ["success-soft-foreground", "success-soft"], // success notices, audit ok
  ["danger-soft-foreground", "danger-soft"], // audit failure
  // Coloured text on the page/card surfaces
  ["primary", "card"], // links, permalinks
  ["primary", "background"],
  ["destructive", "card"], // reject label, errors, escalation
  ["blocked", "card"], // blocked_reason
  ["warning", "card"], // "needs <doc>"
  ["success", "card"], // "doc attached"
];

const AA_NORMAL = 4.5;

describe("design tokens meet WCAG AA", () => {
  it("parsed every color token from index.css", () => {
    // Sanity check the regex actually found the palette.
    expect(Object.keys(tokens).length).toBeGreaterThan(15);
    expect(tokens.primary).toBeDefined();
  });

  it.each(PAIRS)("%s on %s clears AA (4.5:1)", (fgName, bgName) => {
    const fg = tokens[fgName];
    const bg = tokens[bgName];
    expect(fg, `missing token --color-${fgName}`).toBeDefined();
    expect(bg, `missing token --color-${bgName}`).toBeDefined();
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(AA_NORMAL);
  });
});
