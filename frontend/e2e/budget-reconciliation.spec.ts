import { expect, test } from "@playwright/test";

for (const width of [375, 1280]) test(`income drills and calendar owner sections at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const budgets = [null, 7].map((owner, index) => ({ id: index + 1, owner_profile_id: owner,
    name: "Synthetic November", period_type: "monthly", start_date: "2026-11-01",
    end_date: "2026-11-30", status: "locked" }));
  const lines = [
    { id: 11, category_id: 1, side: "income", original_pence: 10000, effective_pence: 10000 },
    { id: 12, category_id: 2, side: "expense", original_pence: 2000, effective_pence: 2000 },
  ];
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const personal = path.includes("/budgets/2");
    const period = { start: "2026-11-01", end: "2026-11-30", basis: "calendar" };
    const detail = { ...budgets[personal ? 1 : 0], lines, read_only: true,
      period: personal ? { start: "2026-10-30", end: "2026-11-26", basis: "pay_cycle" } : period };
    const received = { transaction_id: 77, date: personal ? "2026-10-30" : "2026-11-03",
      account_id: 1, payee: "Synthetic received salary", category_id: 1, amount_pence: 10001 };
    const actuals = { budget_id: 0, totals: {}, lines: {}, spent_pence: 600,
      external_income_pence: 15001, income_received_pence: 15001, income_items: [received,
        { ...received, transaction_id: 78, date: "2026-11-04", payee: "Synthetic split receipt", category_id: 2, amount_pence: 2001 },
        { ...received, transaction_id: 78, date: "2026-11-04", payee: "Synthetic split receipt", category_id: null, amount_pence: 2999 }],
      opening_funds_pence: 0, contributions_pence: 0, contribution_items: [] };
    const transaction = { id: 77, household_id: 1, account_id: 1, date: "2026-10-30",
      payee: "Synthetic received salary", amount_pence: 10001, category_id: 1,
      status: "cleared", source: "manual", notes: null, splits: [] };
    let json: unknown = { items: [], total: 0 };
    if (path.endsWith("/households")) json = { items: [{ id: 1, name: "Synthetic" }], total: 1 };
    else if (path.endsWith("/profiles")) json = { items: [{ id: 7, display_name: "Member A", role: "admin", is_active: true }], total: 1 };
    else if (path.endsWith("/categories")) json = { items: [{ id: 1, name: "Salary" }, { id: 2, name: "Expense only" }], total: 2 };
    else if (path.endsWith("/accounts")) json = { items: [{ id: 1, name: "Synthetic account", owner_profile_id: 7, archived: false }], total: 1 };
    else if (path.endsWith("/reports/combined")) json = { detail, actuals, sections: [
      { owner_profile_id: null, name: "Shared", detail: { ...detail, lines: [] }, actuals: { ...actuals, period, spent_pence: 100, external_income_pence: 0, income_received_pence: 0, income_items: [] } },
      { owner_profile_id: 7, name: "Member A", detail, actuals: { ...actuals, period, spent_pence: 200, totals: { "11": 10001, "12": 200 } } },
      { owner_profile_id: 8, name: "Member B", detail: null, actuals: { ...actuals, period, spent_pence: 300, external_income_pence: 0, income_received_pence: 0, income_items: [] } },
    ] };
    else if (path.endsWith("/budgets")) json = { items: budgets, total: 2 };
    else if (path.endsWith("/actuals")) json = actuals;
    else if (/\/budgets\/\d+$/.test(path)) json = detail;
    else if (path.endsWith("/pay-cycle-settings")) json = { payday_day: 28, options: [] };
    else if (path.endsWith("/transactions/77")) json = transaction;
    else if (path.endsWith("/transactions")) json = { items: [transaction], total: 1, page: 1, size: 50 };
    else if (path.endsWith("/expected-movements") || path.endsWith("/queue") || path.endsWith("/contributions")) json = [];
    await route.fulfill({ json });
  });
  await page.goto("/budget");
  await expect(page.getByRole("region", { name: "Shared budget", exact: true })).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Member A budget", exact: true })).toContainText("1 Nov 2026 – 30 Nov 2026");
  await expect(page.getByRole("region", { name: "Member B budget", exact: true })).toContainText("No plan for this period");
  await expect(page.getByRole("region", { name: "Member B budget", exact: true })).toContainText("£3.00");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `e2e/artifacts/budget-reconciliation-all-${width}.png`, fullPage: true });
  await page.getByRole("radio", { name: "Member A", exact: true }).click();
  const categories = page.getByRole("table", { name: "Income by category", exact: true });
  await expect(categories.locator("tfoot")).toContainText("£100.01");
  await expect(categories.locator("tfoot")).toContainText("£50.00");
  await expect(categories.locator("tfoot")).toContainText("£150.01");
  const receipts = page.getByRole("table", { name: "Received income", exact: true });
  await expect(receipts).toContainText("30 Oct 2026");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `e2e/artifacts/budget-reconciliation-income-${width}.png`, fullPage: true });
  await receipts.getByRole("link", { name: "#77 · Synthetic received salary", exact: true }).click();
  await expect(page).toHaveURL(/\/transactions\?transaction_id=77$/);
  await expect(page.getByRole("region", { name: "Selected transaction", exact: true })).toContainText("Synthetic received salary");
  expect(errors).toEqual([]);
});
