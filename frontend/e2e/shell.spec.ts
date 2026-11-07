import { expect, test } from "@playwright/test";

const API = "http://localhost:8099/api/v1";

test.beforeAll(async ({ request }) => {
  const res = await request.post(`${API}/households`, {
    data: { name: "E2E Household" },
  });
  expect(res.ok()).toBe(true);
});

test("shell drives the real dev stack end-to-end", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Dashboard" }),
  ).toBeVisible();
  // Seeded household resolved and rendered via the typed client.
  await expect(page.getByText("Household: E2E Household")).toBeVisible();
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
