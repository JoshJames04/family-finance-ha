import { expect, test, type APIResponse } from "@playwright/test";

const API = process.env["FF_E2E_API"] ?? "http://localhost:8099/api/v1";
async function json<T>(pending: Promise<APIResponse>): Promise<T> {
  const response = await pending;
  const body = await response.text();
  expect(response.ok(), `${response.status()} ${body}`).toBe(true);
  return JSON.parse(body) as T;
}

test("historical personal pay cycle and calendar household reconcile through the real API", async ({ request, page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1280, height: 1000 });
  const household = await json<{id: number}>(request.post(`${API}/households`, {
    data: {name: `Synthetic pay-cycle proof ${crypto.randomUUID()}`},
  }));
  const headers: Record<string, string> = {"X-Household-Id": String(household.id)};
  const post = (path: string, data?: object) => json<{id: number}>(request.post(`${API}${path}`, {headers, data}));
  const owner = await post("/profiles", {display_name: "Cycle proof owner", role: "admin"});
  headers["X-Profile-Id"] = String(owner.id);
  const personal = await post("/accounts", {name: "Synthetic personal", type: "personal", owner_profile_id: owner.id,
    opening_balance: 10000, opening_date: "2025-01-01"});
  const shared = await post("/accounts", {name: "Synthetic shared", type: "joint", opening_balance: 0, opening_date: "2025-01-01"});
  const category = await post("/categories", {name: "Synthetic essentials"});
  await post("/employments", {profile_id: owner.id, employer: "Synthetic monthly employment", contracted_hours: 35,
    paid_into_account_id: personal.id, start_date: "2025-01-01",
    payday_rule: JSON.stringify({kind: "fixed_day", day: 28, adjust: "previous"})});
  for (const [scope, amount] of [[null, 2000], [owner.id, 8000]] as const) {
    const plan = await post("/budgets", {name: "November synthetic plan", owner_profile_id: scope, period_type: "monthly",
      start_date: "2025-11-01", end_date: "2025-11-30"});
    await post(`/budgets/${plan.id}/lines`, {category_id: category.id, side: "expense", amount});
    await json(request.post(`${API}/budgets/${plan.id}/approve`, {headers}));
  }
  for (const [date, amount] of [["2025-10-27", -1000], ["2025-10-28", 30000],
    ["2025-10-28", -2000], ["2025-11-27", -3000], ["2025-11-28", -4000]] as const) {
    await post("/transactions", {account_id: personal.id, date, amount,
      category_id: amount > 0 ? null : category.id,
      payee: amount > 0 ? "Synthetic salary outside income plan" : "Synthetic expense"});
  }
  const debit = await post("/transactions", {account_id: personal.id, date: "2025-11-02", amount: -5000,
    payee: "Synthetic shared contribution"});
  const credit = await post("/transactions", {account_id: shared.id, date: "2025-11-02", amount: 5000,
    payee: "Synthetic shared contribution"});
  await post("/transfers", {debit_transaction_id: debit.id, credit_transaction_id: credit.id});
  await post("/transactions", {account_id: shared.id, date: "2025-11-04", amount: -1000,
    category_id: category.id, payee: "Synthetic joint bill"});
  await page.addInitScript(({hid, pid}) => {
    localStorage.setItem("ff.householdId", String(hid));
    localStorage.setItem("ff.profileId", String(pid));
  }, {hid: household.id, pid: owner.id});
  await page.goto("/budget");
  const stat = (label: string) => page.getByText(label, {exact: true}).first().locator("..").locator("..");
  await expect(stat("Spent so far").getByText("£80.00", {exact: true})).toBeVisible();
  await expect(stat("Income received").getByText("£0.00", {exact: true})).toBeVisible();
  await page.getByRole("radio", {name: "Cycle proof owner", exact: true}).click();
  await expect(page.getByText("Pay cycle: 28 Oct 2025 – 27 Nov 2025", {exact: true})).toBeVisible();
  await expect(stat("Spent so far").getByText("£50.00", {exact: true})).toBeVisible();
  await expect(stat("Income received").getByText("£300.00", {exact: true})).toBeVisible();
  await expect(stat("Opening funds").getByText("£90.00", {exact: true})).toBeVisible();
  await expect(page.getByRole("table", {name: "Received income", exact: true})).toContainText("Synthetic salary outside income plan");
  await page.getByText("Reconcile spending", {exact: true}).click();
  const bridge = page.getByRole("table", {name: "Transactions included in spent so far"});
  await expect(bridge.locator("tbody tr")).toHaveCount(2);
  await expect(bridge.locator("tfoot")).toContainText("£50.00");
  await page.screenshot({path: "e2e/artifacts/paycycle-personal-real-api.png", fullPage: true});
  await page.getByRole("radio", {name: "Shared", exact: true}).click();
  await expect(stat("Net contributions").getByText("£50.00", {exact: true})).toBeVisible();
  await expect(stat("Spent so far").getByText("£10.00", {exact: true})).toBeVisible();
  await expect(page.getByRole("table", {name: "Contributions", exact: true})).toContainText("Cycle proof owner");
  await expect(page.getByText(/Pay cycle:/)).toHaveCount(0);
  await page.screenshot({path: "e2e/artifacts/paycycle-shared-real-api.png", fullPage: true});
  await page.getByRole("radio", {name: "All", exact: true}).click();
  await expect(stat("Spent so far").getByText("£80.00", {exact: true})).toBeVisible();
  await expect(stat("Income received").getByText("£0.00", {exact: true})).toBeVisible();
  const fixedOwner = await post("/profiles", {display_name: "Fixed-cycle proof owner", role: "member"});
  const fixed = await json<{payday_day: number}>(request.put(`${API}/budgets/pay-cycle-settings`, {
    headers, data: {owner_profile_id: fixedOwner.id, payday_day: 11},
  }));
  expect(fixed.payday_day).toBe(11);
  const fixedPlan = await post("/budgets", {name: "Fixed payday November", owner_profile_id: fixedOwner.id,
    period_type: "monthly", start_date: "2025-11-01", end_date: "2025-11-30"});
  await post(`/budgets/${fixedPlan.id}/lines`, {category_id: category.id, side: "expense", amount: 1000});
  await page.reload();
  await page.getByRole("radio", {name: "Fixed-cycle proof owner", exact: true}).click();
  await expect(page.getByText("Pay cycle: 10 Oct 2025 – 10 Nov 2025", {exact: true})).toBeVisible();
  await expect(page.getByRole("combobox", {name: "Pay-cycle source", exact: true})).toHaveValue("fixed");
  await expect(page.getByRole("spinbutton", {name: "Monthly payday", exact: true})).toHaveValue("11");
  const jobs = await json<{items: {profile_id: number}[]}>(request.get(`${API}/employments`, {headers}));
  expect(jobs.items.some(job => job.profile_id === fixedOwner.id)).toBe(false);
});
