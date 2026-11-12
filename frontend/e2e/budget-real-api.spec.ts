import { expect, test, type APIResponse } from "@playwright/test";

const API = process.env["FF_E2E_API"] ?? "http://localhost:8099/api/v1";

async function json<T>(pending: Promise<APIResponse>): Promise<T> {
  const response = await pending;
  const body = await response.text();
  expect(response.ok(), `${response.status()} ${response.url()}: ${body}`).toBe(true);
  return JSON.parse(body) as T;
}

test("real Budget scopes preserve periods and reconcile external spending", async ({ request, page }) => {
  test.setTimeout(60_000);
  // Keep this fixture out of the first household used by unrelated specs.
  const initial = await json<{ items: { id: number }[] }>(request.get(`${API}/households`));
  if (initial.items.length === 0) {
    await json(request.post(`${API}/households`, { data: { name: "E2E bootstrap" } }));
  }
  const householdName = `Budget real API ${crypto.randomUUID()}`;
  const household = await json<{ id: number }>(request.post(`${API}/households`, {
    data: { name: householdName },
  }));
  const headers: Record<string, string> = { "X-Household-Id": String(household.id) };
  const post = (path: string, data?: object) => json<{ id: number }>(
    request.post(`${API}${path}`, { headers, data }));
  const admin = await post("/profiles", { display_name: "Budget test admin", role: "admin" });
  headers["X-Profile-Id"] = String(admin.id);
  const other = await post("/profiles", { display_name: "Budget test member", role: "member" });
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const start = `${year}-${month}-01`;
  const end = `${year}-${month}-${new Date(year, now.getMonth() + 1, 0).getDate()}`;
  const account = (name: string, owner: number | null) => post("/accounts", {
    name, owner_profile_id: owner, type: "personal", opening_balance: { pence: 0 }, opening_date: start,
  });
  const shared = await account("Shared test account", null);
  const personal = await account("Admin test account", admin.id);
  const unplanned = await account("Member test account", other.id);
  const food = await post("/categories", { name: "Food" });
  const internal = await post("/categories", { name: "Internal Transfers" });
  const transaction = (accountId: number, pence: number, category = food.id) => post("/transactions", {
    account_id: accountId, date: start, amount: { pence }, payee: "Synthetic Budget fixture", category_id: category,
  });
  const spending = [await transaction(shared.id, -1000), await transaction(personal.id, -2000),
    await transaction(unplanned.id, -3000)];
  const receipt = await transaction(personal.id, 5000);
  const debit = await transaction(shared.id, -70000);
  const credit = await transaction(personal.id, 70000);
  await post("/transfers", { debit_transaction_id: debit.id, credit_transaction_id: credit.id });
  const markedDebit = await transaction(personal.id, -90000, internal.id);
  const markedCredit = await transaction(personal.id, 110000, internal.id);
  const budget = async (name: string, owner: number | null, pence: number, historical = false) => {
    const plan = await post("/budgets", { name, owner_profile_id: owner, period_type: "monthly",
      start_date: historical ? `${year - 1}-${month}-01` : start,
      end_date: historical ? `${year - 1}-${month}-${new Date(year - 1, now.getMonth() + 1, 0).getDate()}` : end,
    });
    await post(`/budgets/${plan.id}/lines`, { category_id: food.id, side: "expense", amount: { pence } });
    await post(`/budgets/${plan.id}/approve`);
  };
  await budget("Shared current plan", null, 10000);
  await budget("Admin current plan", admin.id, 20000);
  await budget("Member historical plan", other.id, 10000, true);
  await page.addInitScript(({ householdId, profileId }) => {
    localStorage.setItem("ff.householdId", String(householdId));
    localStorage.setItem("ff.profileId", String(profileId));
  }, { householdId: household.id, profileId: admin.id });
  await page.goto("/budget");
  await expect(page.getByText(householdName, { exact: true }).first()).toBeVisible();
  const stat = (label: string) => page.getByText(label, { exact: true }).first().locator("..").locator("..");
  await expect(page.getByRole("radio", { name: "All", exact: true })).toHaveAttribute("aria-checked", "true");
  await expect(stat("Planned spending").getByText("£300.00", { exact: true })).toBeVisible();
  await expect(stat("Spent so far").getByText("£60.00", { exact: true })).toBeVisible();
  await expect(stat("Income received").getByText("£50.00", { exact: true })).toBeVisible();
  await page.getByText("Reconcile spending", { exact: true }).click();
  const bridge = page.getByRole("table", { name: "Transactions included in spent so far" });
  await expect(bridge.locator("tbody tr")).toHaveCount(3);
  for (const transaction of spending) await expect(bridge.getByRole("cell", { name: `#${transaction.id}`, exact: true })).toBeVisible();
  for (const transaction of [receipt, debit, credit, markedDebit, markedCredit]) {
    await expect(bridge.getByRole("cell", { name: `#${transaction.id}`, exact: true })).toHaveCount(0);
  }
  const amounts = await bridge.locator("tbody tr td:last-child").allTextContents();
  expect(amounts.reduce((total, amount) => total + Math.round(Number(amount.replace(/[^\d.-]/g, "")) * 100), 0)).toBe(6000);
  await expect(bridge.locator("tfoot")).toContainText("£60.00");
  await page.screenshot({ path: "e2e/artifacts/live142-budget-real-api.png", fullPage: true });
  await page.getByRole("radio", { name: "Shared", exact: true }).click();
  await expect(stat("Planned spending").getByText("£100.00", { exact: true })).toBeVisible();
  await expect(stat("Spent so far").getByText("£10.00", { exact: true })).toBeVisible();
  await expect(stat("Income received").getByText("£0.00", { exact: true })).toBeVisible();
  await page.getByRole("radio", { name: "Budget test admin", exact: true }).click();
  await expect(stat("Planned spending").getByText("£200.00", { exact: true })).toBeVisible();
  await expect(stat("Spent so far").getByText("£20.00", { exact: true })).toBeVisible();
  await expect(stat("Income received").getByText("£50.00", { exact: true })).toBeVisible();
  await page.getByRole("radio", { name: "Budget test member", exact: true }).click();
  const monthName = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(now);
  await expect(page.getByText(`No budget for this owner in ${monthName}`, { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Year", exact: true })).toHaveValue(String(year));
  await expect(page.getByRole("group", { name: "Month", exact: true })
    .getByRole("button", { name: monthName.slice(0, 3), exact: true })).toHaveAttribute("aria-pressed", "true");
  for (const label of ["Planned spending", "Spent so far", "Income received"]) {
    await expect(page.getByText(label, { exact: true })).toHaveCount(0);
  }
  await page.getByRole("radio", { name: "All", exact: true }).click();
  await expect(stat("Spent so far").getByText("£60.00", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Dashboard", exact: true }).first().click();
  await expect(page.getByText(`Here's where ${householdName} stands today.`, { exact: true })).toBeVisible();
});
