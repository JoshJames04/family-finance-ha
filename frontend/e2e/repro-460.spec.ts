import { expect, test } from "@playwright/test";

/** Issue #460 regression — runs against the live dev stack.
 * Fault injection happens at the browser network layer, which is
 * exactly where ingress/upstream failures surface to the SPA.
 * These specs assert the FIXED contract:
 *  - a readiness delay renders loading, never false empty/£0 states
 *  - a transient 503 retries and recovers without a refresh
 *  - an exhausted failure shows an error with a working Retry action */

const FALSE_EMPTY = [
  "No transactions yet",
  "Nothing scheduled",
  "No projection yet",
  "£0.00",
];

test("readiness window: loading shown, never false empty states", async ({ page }) => {
  // Hold the identity chain hostage for ~6s — simulates the backend
  // warm-up / ingress retry window after an add-on restart.
  await page.route("**/api/v1/profiles", async (route) => {
    await new Promise((r) => setTimeout(r, 6000));
    await route.continue();
  });
  await page.goto("./");
  await page.waitForTimeout(2500);
  await page.screenshot({ path: "e2e/artifacts/460-readiness.png", fullPage: true });
  const body = await page.locator("body").innerText();
  const leaked = FALSE_EMPTY.filter((t) => body.includes(t));
  expect(leaked, `false empty states shown: ${leaked.join(", ")}`).toEqual([]);
  // And the app must visibly be loading, not blank.
  await expect(page.locator("[role='status']").first()).toBeAttached();
  // After the window, real data flows in without a refresh.
  await page.waitForTimeout(6000);
  await expect(page.locator("body")).not.toContainText("Couldn't load", { timeout: 15000 });
  await page.screenshot({ path: "e2e/artifacts/460-recovered.png", fullPage: true });
});

test("one-shot 503: widget retries and recovers on its own", async ({ page }) => {
  let hit = 0;
  await page.route("**/api/v1/expected-movements*", (route) => {
    hit += 1;
    return hit === 1
      ? route.fulfill({ status: 503, body: "upstream unavailable" })
      : route.continue();
  });
  await page.goto("./");
  const widget = page.locator("[data-widget='upcoming']");
  // The first request 503s; backoff retries land ~500ms later and
  // the real response replaces the failure — no refresh needed.
  await expect.poll(() => hit, { timeout: 15000 }).toBeGreaterThanOrEqual(2);
  await expect(widget).not.toContainText("Couldn't load", { timeout: 15000 });
  // Verify displayed content matches the real API response.
  const api = await page.request.get(
    "/api/v1/expected-movements?start=" + new Date().toISOString().slice(0, 10)
    + "&end=" + new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10));
  const moves = await api.json() as { label: string }[];
  if (moves.length > 0) {
    await expect(widget).toContainText(moves[0]!.label);
  } else {
    await expect(widget).toContainText("Nothing scheduled"); // honest empty
  }
  await page.screenshot({ path: "e2e/artifacts/460-503-recovered.png", fullPage: true });
});

test("persistent failure: error state + working Retry action", async ({ page }) => {
  let hit = 0;
  await page.route("**/api/v1/expected-movements*", (route) => {
    hit += 1;
    // Fail the whole bounded retry budget (4 attempts), then heal.
    return hit <= 4
      ? route.fulfill({ status: 503, body: "upstream unavailable" })
      : route.continue();
  });
  await page.goto("./");
  const widget = page.locator("[data-widget='upcoming']");
  // Exhausted retries → honest terminal error, never a fake empty.
  await expect(widget.getByRole("alert")).toBeVisible({ timeout: 20000 });
  await expect(widget).not.toContainText("Nothing scheduled");
  // The Retry action must actually recover once the API is healthy.
  const before = hit;
  await widget.getByRole("button", { name: "Retry" }).click();
  await expect.poll(() => hit, { timeout: 15000 }).toBeGreaterThan(before);
  const api = await page.request.get(
    "/api/v1/expected-movements?start=" + new Date().toISOString().slice(0, 10)
    + "&end=" + new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10));
  const moves = await api.json() as { label: string }[];
  if (moves.length > 0) {
    await expect(widget).toContainText(moves[0]!.label);
  } else {
    await expect(widget).toContainText("Nothing scheduled");
  }
  await page.screenshot({ path: "e2e/artifacts/460-retry-recovered.png", fullPage: true });
});
