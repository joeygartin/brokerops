import { expect, test } from "@playwright/test";

// The DEMO.md golden path as one browser smoke (BOP-029). It catches the class of
// bug the API e2e (scripted compose check) and the vitest suite each miss on their
// own: the API works, the components work, and the page still doesn't wire them
// together. LangGraph is the sole orchestrator (ADR-0019); there is deliberately
// no engine matrix here — the default ORCHESTRATOR carries the run.
//
// The stack boots login-free (AUTH_METHODS unset → demo operator, full admin), so
// there is no sign-in step: `/` redirects straight to the listings board.
test("marketing HITL golden path, then a transaction detail visit", async ({ page }) => {
  // 1. Boot → Listings. Twelve listings from the mock RESO MLS; at least one is
  //    active and therefore carries the operator controls.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "brokerops", level: 1 })).toBeVisible();
  const startButton = page.getByRole("button", { name: "Start marketing workflow" }).first();
  await expect(startButton).toBeVisible();

  // 2. Start the marketing workflow → it pauses at the HITL gate and the board
  //    reports the draft is waiting in the Approvals inbox. Capture the MLS id
  //    from the notice so the review leg can correlate the drafted card to the
  //    exact listing this run started (rather than any pending marketing gate).
  await startButton.click();
  const notice = page.getByText(/marketing draft is waiting in the Approvals inbox/);
  await expect(notice).toBeVisible();
  const mlsId = ((await notice.textContent()) ?? "").match(
    /(\S+):\s*marketing draft is waiting/,
  )?.[1];
  expect(mlsId, "notice should carry the started listing's MLS id").toBeTruthy();

  // 3. Approvals → REVIEW the drafted marketing card, then approve it. The review
  //    step is a real assertion, not a fly-by: the card for THIS listing must show
  //    the generated draft the operator is meant to read — the headline, the body
  //    (which cites the same MLS id, correlating draft→listing), and the channels.
  //    A regression that hides, blanks, or misassociates the draft fails here.
  await page.getByRole("link", { name: "Approvals" }).click();
  const marketingCard = page
    .getByRole("article")
    .filter({ hasText: new RegExp(`Approve marketing — ${mlsId}`) })
    .first();
  await expect(marketingCard).toBeVisible();
  await expect(marketingCard.getByRole("heading", { level: 3 })).toHaveText(/Just Listed/);
  await expect(marketingCard.getByText(`MLS# ${mlsId}`)).toBeVisible();
  await expect(marketingCard.getByText("mls_portals")).toBeVisible();
  await marketingCard.getByRole("button", { name: "Approve" }).click();

  // 4. Confirmation: the workflow resumed and fanned out CRM tasks. The notice
  //    carries both the resumed status and the CRM-task count — the acceptance
  //    signal (a broken approve button leaves no notice and fails the run).
  await expect(page.getByText(/approved — workflow status:/)).toBeVisible();
  await expect(page.getByText(/CRM task\(s\) created/)).toBeVisible();

  // 5. One transaction detail visit. Seed the demo transactions if this is a
  //    fresh stack. Wait for the board to *settle* first — the seed button also
  //    renders during the initial load and detaches the moment data arrives, so
  //    gate on the settled empty-state message (never shown mid-load) before
  //    clicking it, and otherwise the transactions are already present.
  await page.getByRole("link", { name: "Transactions" }).click();
  const openLinks = page.getByRole("link", { name: "Open ↗" });
  const emptyState = page.getByText(/No transactions yet/);
  await expect(openLinks.first().or(emptyState)).toBeVisible();
  if (await emptyState.isVisible().catch(() => false)) {
    await page.getByRole("button", { name: "Seed demo transactions" }).click();
  }
  const firstOpen = openLinks.first();
  await expect(firstOpen).toBeVisible();
  await firstOpen.click();

  // The hub page composes the deal's slices (BOP-027); its back link and the
  // "Comms history" section title are stable anchors unique to the detail page
  // (unlike "Audit trail", which also names a nav tab) that a working hub renders.
  await expect(page.getByRole("link", { name: "All transactions" })).toBeVisible();
  await expect(page.getByText("Comms history")).toBeVisible();
});
