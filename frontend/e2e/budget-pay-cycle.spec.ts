import { expect, test } from "@playwright/test";

for (const width of [375, 1280]) test(`personal cycle, calendar household and funding detail at ${width}px`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const requests: string[] = [];
  const settingsWrites: unknown[] = [];
  const budgets = [null, 7].map((owner, index) => ({id: index + 1, owner_profile_id: owner, name: "November", period_type: "monthly", start_date: "2026-11-01", end_date: "2026-11-30", status: "locked"}));
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url()); requests.push(url.pathname + url.search);
    const path = url.pathname;
    const personal = path.includes("/budgets/2");
    const detail = {...budgets[personal ? 1 : 0], lines: [], read_only: true,
      period: personal ? {start: "2026-10-30", end: "2026-11-26", basis: "pay_cycle", employment_id: 4, warning: null} : null};
    const actuals = {budget_id: personal ? 2 : 1, totals: {}, lines: {}, spent_pence: 0,
      external_income_pence: 12300, income_received_pence: 12300, contributions_pence: 8900,
      opening_funds_pence: -4500, unattributed_internal_pence: 200,
      income_items: [{transaction_id: 77, date: "2026-10-30", account_id: 1, payee: "Salary received outside manual plan", category_id: null, amount_pence: 12300, contributor_profile_id: null}],
      contribution_items: [{transaction_id: 78, date: "2026-11-02", account_id: 2, payee: "Verified funding", category_id: null, amount_pence: 8900, contributor_profile_id: 7}]};
    let json: unknown = {items: [], total: 0};
    if (path.endsWith("/households")) json = {items: [{id: 1, name: "Synthetic cycle household"}], total: 1};
    else if (path.endsWith("/profiles")) json = {items: [{id: 7, display_name: "Cycle member", role: "admin", is_active: true}], total: 1};
    else if (path.endsWith("/reports/combined")) json = {detail, actuals};
    else if (path.endsWith("/actuals")) json = actuals;
    else if (/\/budgets\/\d+$/.test(path)) json = detail;
    else if (path.endsWith("/budgets")) json = {items: budgets, total: 2};
    else if (path.endsWith("/pay-cycle-settings")) {
      if (route.request().method() === "PUT") settingsWrites.push(route.request().postDataJSON());
      json = {employment_id: null, payday_day: null, options: [{employment_id: 4, employer: "Monthly employer"}]};
    }
    else if (path.endsWith("/expected-movements")) json = [{id: 30, expected_date: "2026-10-30", amount_pence: 12300, outstanding_pence: 0, dismissed_at: null, source_type: "employment", label: "Matched salary"}];
    else if (path.endsWith("/queue") || path.endsWith("/contributions/by-member") || path.endsWith("/contributions")) json = [];
    await route.fulfill({json});
  });
  await page.goto("/budget");
  await page.getByRole("radio", {name: "Cycle member", exact: true}).click();
  await expect(page.getByText("Pay cycle: 30 Oct 2026 – 26 Nov 2026", {exact: true})).toBeVisible();
  await expect(page.getByRole("heading", {name: "November 2026", exact: true})).toBeVisible();
  await expect.poll(() => requests.some(url => url.includes("/budgets/2/actuals?") && url.includes("basis=pay_cycle"))).toBe(true);
  await expect.poll(() => requests.some(url => url.includes("expected-movements?") && url.includes("start=2026-10-30") && url.includes("end=2026-11-26"))).toBe(true);
  await expect(page.getByText("Planned salary (forecast)", {exact: true})).toHaveCount(0);
  await expect(page.getByRole("table", {name: "Received income", exact: true})).toContainText("Salary received outside manual plan");
  await expect(page.getByRole("button", {name: "Save source", exact: true})).toHaveCSS("min-height", "44px");
  await page.getByRole("combobox", {name: "Pay-cycle source", exact: true}).selectOption("fixed");
  const day = page.getByRole("spinbutton", {name: "Monthly payday", exact: true});
  await expect(day).toHaveCSS("min-height", "44px");
  await expect(page.getByRole("button", {name: "Save source", exact: true})).toBeDisabled();
  await day.fill("11");
  expect(settingsWrites).toHaveLength(0);
  await page.getByRole("button", {name: "Save source", exact: true}).click();
  await expect.poll(() => settingsWrites).toEqual([{owner_profile_id: 7, employment_id: null, payday_day: 11}]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("radio", {name: "Shared", exact: true}).click();
  await expect(page.getByText("Net contributions", {exact: true})).toBeVisible();
  await expect(page.getByText("Other external income", {exact: true})).toBeVisible();
  await expect(page.getByRole("table", {name: "Contributions", exact: true})).toContainText("Cycle member");
  await page.getByRole("radio", {name: "All", exact: true}).click();
  await expect(page.getByText("Income received", {exact: true})).toBeVisible();
  await expect(page.getByText("Net contributions", {exact: true})).toHaveCount(0);
});
