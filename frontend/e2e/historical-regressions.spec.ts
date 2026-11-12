import { expect, test, type Page } from "@playwright/test";

const now = new Date();
const start = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
const end = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()}`;
const shared = { id: 1, owner_profile_id: null, name: "Synthetic shared budget", period_type: "monthly", start_date: start, end_date: end, status: "approved" };
const categories = [{ id: 2, name: "Utilities", parent_id: null, archived: false }, { id: 1, name: "Groceries", parent_id: null, archived: false }];
const line = { id: 11, category_id: 1, side: "expense", original_pence: 50000, revised_pence: null, effective_pence: 50000, rollover: false };
const detail = { ...shared, id: 0, name: "All", lines: [line], read_only: true };
const actuals = { budget_id: 0, lines: { "11": { ...line, actual_pence: 8000, variance_pence: 42000, accuracy_pct: 16 } }, spent_pence: 8000, budget_covered_pence: 8000, unallocated_pence: 0, uncategorised_pence: 0, income_received_pence: 0, bills_remaining_pence: 0, bills_remaining_count: 0, as_of: start, spend_items: [] };

async function synthetic(page: Page, unavailable: () => boolean = () => false) {
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/v1/expected-movements" && unavailable()) {
      await route.fulfill({ status: 503, body: "Synthetic upgrade outage" });
      return;
    }
    let body: unknown = [];
    if (path === "/api/v1/households") body = { items: [{ id: 1, name: "Synthetic household" }] };
    else if (path === "/api/v1/profiles") body = { items: [{ id: 1, display_name: "Test member", role: "admin", is_active: true }, { id: 7, display_name: "Second member", role: "member", is_active: true }] };
    else if (path.endsWith("/profiles/me/dashboard")) body = { role: "admin", layout: { order: ["upcoming"], hidden: [] } };
    else if (path === "/api/v1/auth/me") body = { id: 1, role: "admin", display_name: "Test member" };
    else if (path === "/api/v1/categories") body = { items: categories };
    else if (path === "/api/v1/accounts") body = { items: [{ id: 1, name: "Synthetic account", type: "joint", archived: false }] };
    else if (path === "/api/v1/budgets") body = { items: [{ ...shared, id: 2, owner_profile_id: 7 }, shared] };
    else if (path.endsWith("/reports/combined")) body = { detail, actuals };
    else if (path.endsWith("/actuals")) body = actuals;
    else if (/\/budgets\/\d+$/.test(path)) body = { ...detail, id: 1 };
    else if (path === "/api/v1/transactions") body = { items: [{ id: 1, account_id: 1, date: start, payee: "Synthetic shop", amount_pence: -8000, category_id: 1, status: "cleared", source: "manual", splits: [] }], total: 1, page: 1, size: 200 };
    else if (path === "/api/v1/version") body = { version: "1.4.1" };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

test("All Budget keeps the current period and complete spending visible", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await synthetic(page);
  await page.goto("/budget");
  await expect(page.getByRole("radio", { name: "All", exact: true })).toHaveAttribute("aria-checked", "true");
  await expect(page.locator('select[aria-label="Budget"]')).toHaveValue("1");
  await expect(page.getByText("Spent so far", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("£80.00").first()).toBeVisible();
  await page.screenshot({ path: "e2e/artifacts/overnight-budget.png", fullPage: true });
});

test("selected transaction category keeps contrasting native option colours", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await synthetic(page);
  await page.goto("/transactions");
  const select = page.getByRole("combobox", { name: "Category for Synthetic shop", exact: true });
  await expect(select).toHaveValue("1");
  expect(await select.locator("option").allTextContents()).toEqual(["Uncategorised", "Groceries", "Utilities"]);
  const colours = await select.locator("option").evaluateAll(options => options.map(option => ({ colour: getComputedStyle(option).color, background: getComputedStyle(option).backgroundColor })));
  for (const colour of colours) expect(colour.colour).not.toBe(colour.background);
  await page.screenshot({ path: "e2e/artifacts/overnight-categories.png", fullPage: true });
});

test("a prolonged upstream outage recovers after the first retry burst", async ({ page }) => {
  let unavailable = true;
  await page.clock.install();
  await synthetic(page, () => unavailable);
  await page.goto("/");
  const upcoming = page.locator("[data-widget='upcoming']");
  await expect(upcoming).toBeVisible();
  // Let initial requests settle, then exhaust the short retry window.
  await page.clock.runFor(4_000);
  await expect(upcoming.getByRole("alert")).toBeVisible();
  unavailable = false;
  await page.clock.runFor(31_000);
  await expect(upcoming.getByRole("alert")).toHaveCount(0);
  await expect(upcoming).toContainText("Nothing scheduled");
});
