# ADR-0024: Design tokens + Tailwind + shadcn/ui — one styling system

**Status:** Accepted · **Date:** 2026-07-07

## Context

The UI had never had a design pass. Every view was styled with inline
`style={{…}}` objects carrying hardcoded hex colors (a GitHub-Primer-ish palette
repeated by hand across ~10 files). That has three costs: (1) no accessibility
floor — nothing enforced focus states or contrast, and the old CTA green
(white text) actually failed WCAG AA at 3.2:1; (2) no way to rebrand per client
without editing every component; (3) each new surface (the BOP-019 message
approval card, the BOP-021 Documents tab) reinvented the same buttons and badges.

We restyle once, now, so all subsequent surface is born in the new idiom. The
restyle is behavior-neutral: no route, data, copy, or DOM-semantics changes — the
70 existing vitest specs (which assert on roles, accessible names, aria-labels,
and visible text) stay green with zero selector edits.

## Decision

1. **Tailwind CSS v4 + one design-token file.** `src/index.css` is the single
   source of truth: a `@theme` block of semantic `--color-*` tokens (surfaces,
   text, lines/focus, solid intents, soft intent surfaces) plus `--radius`. Every
   component consumes tokens through utilities (`bg-card`,
   `text-muted-foreground`, `border-border`, `bg-success`) — **no view hardcodes
   a color** (grep-enforced: no hex outside `index.css`). Per-client branding is
   then a matter of swapping the token values, nothing else. Tailwind v4's
   CSS-first config (`@import "tailwindcss"`, `@theme`, the `@tailwindcss/vite`
   plugin) means no `tailwind.config.js` and no separate PostCSS step.

2. **shadcn/ui component layer — vendored primitives, not a dependency.** The
   primitives (`Button`, `Card`, `Badge`, `Input`, `Textarea`, `Select`) live
   in-repo under `src/components/ui/`, built with `class-variance-authority` over
   the tokens and merged with `cn()` (`clsx` + `tailwind-merge`, so a caller's
   `className` always wins). This is shadcn/ui's model: you own the code, there is
   no runtime component framework to upgrade or theme around, and variants encode
   the domain's real vocabulary (a `Badge` has `success`/`warning`/`destructive`/
   `blocked` for entity status and `info`/`successSoft`/`dangerSoft` for
   annotations). Radix enters through `@radix-ui/react-slot` — `Button`'s
   `asChild` lets a router `<Link>` render *as* a button without nesting `<a>` in
   `<button>`. The heavier Radix overlays (Dialog, DropdownMenu) are available for
   future surfaces but were not pulled in: today's UI has none.

3. **Native `<select>` kept — deliberately not Radix Select.** The Documents
   attach form's three pickers stay native `<select>` (styled via
   `components/ui/select.tsx`). Native selects keep the `combobox` role and full
   keyboard/screen-reader/mobile behavior for free, need no portal, and are what
   the attach-form tests drive (`selectOptions`). Swapping in Radix Select would
   have traded accessibility and test stability for a cosmetic gain — the wrong
   call under the code-minimalism ladder.

4. **Accessibility is enforced, not asserted.** Focus-visible rings ship on every
   interactive primitive. The token palette's every foreground/background pairing
   clears WCAG AA (≥4.5:1) — verified by `tokens.contrast.test.ts`, which parses
   `index.css` and fails CI on any regressing token, so contrast is *checked, not
   eyeballed*. The one AA failure in the old palette (the CTA green) was fixed by
   unifying the confirm/approve/attach green to the darker `success` token, which
   clears AA at 5.1:1 (the concrete color values live only in `index.css`).

## Rejected alternatives

- **MUI / Ant Design.** Large runtime, opinionated component theming that fights a
  token-swap rebrand model, and emotion/CSS-in-JS underneath (see below). We want
  to *own* a handful of primitives, not adopt a framework's whole component tree
  and theming API for an ops console of buttons, cards, and badges.
- **CSS-in-JS (emotion / styled-components).** Runtime style injection cost, no
  static extraction, and it is exactly the per-component-hardcoding pattern
  (inline styles) we are removing — just relocated into template literals. Tailwind
  extracts to one static stylesheet at build and centralizes the palette in tokens.
- **Keep inline styles, add a shared constants file.** Would centralize the hex
  values but still leave every component hand-authoring layout with no variant
  system, no focus states, no contrast gate, and no rebrand path. Half the work for
  none of the durable wins.

## Consequences

- New deps: `tailwindcss` + `@tailwindcss/vite` (dev), and
  `class-variance-authority` + `clsx` + `tailwind-merge` + `@radix-ui/react-slot`
  (runtime). Bundle grew ~10.6 kB gzip JS (the CVA/merge/slot runtime) plus a new
  ~4.3 kB gzip CSS file — the cost of the design system, paid once.
- A `@/` path alias (`src/*`) was added to `vite`, `vitest`, and `tsconfig` so the
  shadcn convention (`@/components/ui/…`, `@/lib/utils`) resolves everywhere.
- Future surfaces compose the primitives; adding a Dialog/DropdownMenu means
  vendoring that one shadcn component and its Radix dep, not re-choosing the stack.
- Per-client theming (BOP-032/fleet work) is now a token override, not a fork.
