import { expect, test } from "@playwright/test";

const API = process.env["FF_E2E_API"] ?? "http://localhost:8099/api/v1";

/** The household the UI resolves — first in the list, matching
 * `useHouseholds` (fresh CI DB → the seeded one; reused dev backend →
 * whichever household the dev data has). */
let householdName = "";

test.beforeAll(async ({ request }) => {
  const res = await request.post(`${API}/households`, {
    data: { name: "E2E Household" },
  });
  expect(res.ok()).toBe(true);
  const list = await request.get(`${API}/households`);
  expect(list.ok()).toBe(true);
  const body = (await list.json()) as {
    items: { id: number; name: string }[];
  };
  householdName = body.items[0]?.name ?? "";
  expect(householdName.length).toBeGreaterThan(0);
});

test("shell drives the real dev stack end-to-end", async ({ page }) => {
  await page.goto("/");
  // Dashboard renders an h1 (the greeting) through PageHeader.
  await expect(
    page.getByRole("heading", { level: 1 }),
  ).toBeVisible();
  // The resolved household renders via the typed client — it shows in
  // the sidebar brand block and the topbar breadcrumb.
  await expect(page.getByText(householdName).first()).toBeVisible();
  // Nav exposes every section.
  for (const label of [
    "Dashboard",
    "Transactions",
    "Budget",
    "Accounts",
    "Upcoming",
  ]) {
    await expect(
      page.getByRole("link", { name: label }).first(),
    ).toBeVisible();
  }
});

test("navigating to Accounts renders the accounts page", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Accounts" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Accounts" }),
  ).toBeVisible();
});
