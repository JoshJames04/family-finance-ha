import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Budget } from "../pages/Budget";

const budgets = [null, 7].map((owner, index) => ({
  id: index + 1, owner_profile_id: owner, name: "November", period_type: "monthly",
  start_date: "2026-11-01", end_date: "2026-11-30", status: "approved",
}));
const lines = [
  { id: 11, category_id: 1, side: "income", original_pence: 10000, effective_pence: 10000 },
  { id: 12, category_id: 2, side: "expense", original_pence: 2000, effective_pence: 2000 },
];
const income = [
  { transaction_id: 77, date: "2026-10-30", account_id: 1, payee: "Received salary", category_id: 1, amount_pence: 10001 },
  { transaction_id: 78, date: "2026-11-02", account_id: 1, payee: "Split receipt", category_id: 2, amount_pence: 2001 },
  { transaction_id: 78, date: "2026-11-02", account_id: 1, payee: "Split receipt", category_id: null, amount_pence: 2999 },
];
const spend = [
  { transaction_id: 81, date: "2026-11-02", category_id: null, amount_pence: 100 },
  { transaction_id: 82, date: "2026-11-03", category_id: 2, amount_pence: 200 },
  { transaction_id: 83, date: "2026-11-04", category_id: null, amount_pence: 300 },
];
const actuals = { budget_id: 0, totals: {}, lines: {}, spent_pence: 600, as_of: "2026-11-14",
  spend_items: spend,
  external_income_pence: 15001, income_received_pence: 15001, income_items: income,
  opening_funds_pence: 0, contributions_pence: 8900,
  contribution_items: [{ transaction_id: 79, date: "2026-11-02", account_id: 2, payee: "Contribution", category_id: null, amount_pence: 8900, contributor_profile_id: 7 }],
};

function fixture(nextStatus = 200) {
  vi.spyOn(globalThis, "fetch").mockImplementation(input => {
    const url = new URL(String(input), "http://localhost");
    const december = url.searchParams.get("start") === "2026-12-01";
    const period = { start: december ? "2026-12-01" : "2026-11-01", end: december ? "2026-12-31" : "2026-11-30", basis: "calendar" };
    const personal = url.pathname.includes("/budgets/2");
    const detail = { ...budgets[personal ? 1 : 0], lines, read_only: false,
      period: personal ? { start: "2026-10-30", end: "2026-11-26", basis: "pay_cycle" } : period };
    let body: unknown = { items: [], total: 0 };
    if (url.pathname.endsWith("/households")) body = { items: [{ id: 1, name: "Synthetic" }], total: 1 };
    else if (url.pathname.endsWith("/profiles")) body = { items: [{ id: 7, display_name: "Member A", role: "admin", is_active: true }], total: 1 };
    else if (url.pathname.endsWith("/categories")) body = { items: [{ id: 1, name: "Salary" }, { id: 2, name: "Expense only" }, { id: 3, name: "Unplanned spend" }, { id: 4, name: "Zero plan" }], total: 4 };
    else if (url.pathname.endsWith("/reports/combined")) {
      if (december && nextStatus !== 200) return Promise.resolve(new Response(JSON.stringify({ detail: "December budgets unavailable" }), { status: nextStatus, headers: { "Content-Type": "application/json" } }));
      body = { detail: { ...detail, id: 0, read_only: true, start_date: period.start, end_date: period.end }, actuals,
        sections: [
          { owner_profile_id: null, name: "Shared", detail: { ...detail, lines: [], read_only: true }, actuals: { ...actuals, period, spent_pence: 100, spend_items: [spend[0]], external_income_pence: 0, income_received_pence: 0, income_items: [] } },
          { owner_profile_id: 7, name: december ? "December member" : "Member A", detail: { ...detail, lines, read_only: true }, actuals: { ...actuals, period, spent_pence: 200, spend_items: [spend[1]], totals: { "11": 10001, "12": 200 } } },
          { owner_profile_id: 8, name: "Member B", detail: null, actuals: { ...actuals, period, spent_pence: 300, spend_items: [spend[2]], external_income_pence: 0, income_received_pence: 0, income_items: [] } },
        ],
      };
    } else if (url.pathname.endsWith("/budgets")) body = { items: [...budgets, { ...budgets[0], id: 3, start_date: "2026-12-01", end_date: "2026-12-31" }], total: 3 };
    else if (url.pathname.endsWith("/actuals")) body = actuals;
    else if (/\/budgets\/\d+$/.test(url.pathname)) body = detail;
    else if (url.pathname.endsWith("/expected-movements")) body = [];
    return Promise.resolve(new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }));
  });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

async function openNovember() {
  const user = userEvent.setup();
  render(<MemoryRouter><Budget /></MemoryRouter>);
  await user.selectOptions(await screen.findByRole("combobox", { name: "Budget", hidden: true }), "1");
  return user;
}

it("links actual split receipts and reconciles income categories outside income plan", async () => {
  fixture();
  const user = await openNovember();
  await user.click(await screen.findByRole("radio", { name: "Member A" }));
  const reconciliation = await screen.findByRole("table", { name: "Income by category" });
  expect(within(reconciliation).getByText("Salary").closest("tr")!.textContent).toContain("£100.01");
  expect(within(reconciliation).getByText("Expense only").closest("tr")!.textContent).toContain("£20.01");
  expect(within(reconciliation).getByText("Uncategorised").closest("tr")!.textContent).toContain("£29.99");
  expect(within(reconciliation).getByText("In income plan").closest("tr")!.textContent).toContain("£100.01");
  expect(within(reconciliation).getByText("Outside income plan").closest("tr")!.textContent).toContain("£50.00");
  expect(within(reconciliation).getByText("Total received income").closest("tr")!.textContent).toContain("£150.01");
  const receipts = screen.getByRole("table", { name: "Received income" });
  expect(within(receipts).getByRole("link", { name: "#77 · Received salary" }).getAttribute("href")).toBe("/transactions?transaction_id=77");
  expect(within(receipts).getAllByRole("link", { name: "#78 · Split receipt" })).toHaveLength(2);
  expect(within(receipts).getByText("30 Oct 2026")).toBeTruthy();
  expect(screen.getByText("Income received").parentElement!.parentElement!.textContent).toContain("£150.01");
});

it("gives contributions drill-through without income-category subtotals", async () => {
  fixture(); const user = await openNovember();
  await user.click(await screen.findByRole("radio", { name: "Shared" }));
  const contributions = await screen.findByRole("table", { name: "Contributions" });
  expect(within(contributions).getByRole("link", { name: "#79 · Contribution" }).getAttribute("href")).toBe("/transactions?transaction_id=79");
  expect(within(contributions.closest("details")!).queryByText("Outside income plan")).toBeNull();
});

it("shows owner-labelled calendar sections, zero plans and missing plans read-only", async () => {
  fixture(); await openNovember();
  const shared = await screen.findByRole("region", { name: "Shared budget" });
  const member = screen.getByRole("region", { name: "Member A budget" });
  const missing = screen.getByRole("region", { name: "Member B budget" });
  expect(screen.getAllByRole("region", { name: "Shared budget" })).toHaveLength(1);
  expect(shared.textContent).toContain("£0.00");
  expect(shared.textContent).not.toContain("No plan for this period");
  expect(member.textContent).toContain("1 Nov 2026 – 30 Nov 2026");
  expect(member.textContent).toContain("£2.00");
  expect(missing.textContent).toContain("No plan for this period");
  expect(missing.textContent).toContain("£3.00");
  expect(within(member).queryByRole("button", { name: /Adjust/ })).toBeNull();
});

it.each([200, 503])("clears prior sections while navigating calendar periods (status %s)", async status => {
  fixture(status); const user = await openNovember();
  await screen.findByRole("region", { name: "Member A budget" });
  await user.click(screen.getByRole("button", { name: "Next budget" }));
  if (status === 200) {
    await screen.findByRole("region", { name: "December member budget" });
  } else {
    await screen.findByText(/December budgets unavailable/, {}, { timeout: 8000 });
  }
  expect(screen.queryByRole("region", { name: "Member A budget" })).toBeNull();
}, 10000);

function ownerCoverageFixture(sharedPlanned: boolean, legacy = false) {
  fixture();
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const response = await original(input, init);
    if (!String(input).includes("/reports/combined")) return response;
    const body = await response.json();
    const receipt = (transaction_id: number, category_id: number, amount_pence: number) => ({
      ...income[0], transaction_id, category_id, amount_pence,
    });
    const memberIncome = [receipt(177, 1, 101), receipt(180, 1, 409), receipt(180, 1, 7), receipt(180, 2, 503)];
    const sharedIncome = [receipt(179, 1, 307)];
    const missingPlanIncome = [receipt(178, 1, 203)];
    body.actuals = { ...body.actuals, income_items: [...memberIncome, ...sharedIncome, ...missingPlanIncome], external_income_pence: 1530, income_received_pence: 1530 };
    body.sections[0].detail.lines = sharedPlanned ? [lines[0]] : [];
    body.sections[0].actuals.income_items = sharedIncome;
    body.sections[0].actuals.external_income_pence = body.sections[0].actuals.income_received_pence = 307;
    body.sections[1].actuals.income_items = memberIncome;
    body.sections[1].actuals.external_income_pence = body.sections[1].actuals.income_received_pence = 1020;
    body.sections[2].actuals.income_items = missingPlanIncome;
    body.sections[2].actuals.external_income_pence = body.sections[2].actuals.income_received_pence = 203;
    if (legacy) delete body.sections;
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
}

it.each([false, true])("All income coverage follows the receipt owner's plan (Shared planned: %s)", async sharedPlanned => {
  ownerCoverageFixture(sharedPlanned);
  await openNovember();
  const table = await screen.findByRole("table", { name: "Income by category" });
  const salary = within(table).getByText("Salary").closest("tr")!;
  expect(salary.textContent).toContain("Mixed coverage");
  expect(salary.textContent).toContain("£10.27");
  expect(within(table).getByText("In income plan").closest("tr")!.textContent).toContain(sharedPlanned ? "£8.24" : "£5.17");
  expect(within(table).getByText("Outside income plan").closest("tr")!.textContent).toContain(sharedPlanned ? "£7.06" : "£10.13");
  expect(within(table).getByText("Total received income").closest("tr")!.textContent).toContain("£15.30");
  expect(within(table).getByText("Expense only").closest("tr")!.textContent).toContain("Unplanned category");
});

it("retains income-category coverage for legacy combined replies without sections", async () => {
  ownerCoverageFixture(false, true);
  await openNovember();
  const table = await screen.findByRole("table", { name: "Income by category" });
  expect(within(table).getByText("In income plan").closest("tr")!.textContent).toContain("£10.27");
  expect(within(table).getByText("Outside income plan").closest("tr")!.textContent).toContain("£5.03");
  expect(within(table).queryByText("Mixed coverage")).toBeNull();
});

function sectionFixture(index: number, update: (section: { detail: { lines: unknown[] } | null; actuals: Record<string, unknown> }) => void) {
  fixture();
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const response = await original(input, init);
    if (!String(input).includes("/reports/combined")) return response;
    const body = await response.json();
    update(body.sections[index]);
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
}

it("itemises missing-plan spending and receipts by side including uncategorised activity", async () => {
  sectionFixture(2, section => {
    section.actuals = { ...section.actuals, spent_pence: 200, external_income_pence: 400,
      income_received_pence: 400,
      spend_items: [{ ...spend[0], category_id: 3, amount_pence: 120 }, { ...spend[1], category_id: null, amount_pence: 80 }],
      income_items: [{ ...income[0], amount_pence: 305 }, { ...income[1], category_id: null, amount_pence: 95 }],
    };
  });
  await openNovember();
  const section = await screen.findByRole("region", { name: "Member B budget" });
  const table = within(section).getByRole("table", { name: "Member B budget lines" });
  const rows = within(table).getAllByRole("row").slice(1, -2);
  expect(rows).toHaveLength(4);
  expect(rows.every(row => row.children[2]!.textContent === "—")).toBe(true);
  expect(within(table).getAllByText("Uncategorised")).toHaveLength(2);
  expect(within(table).getByText("Unplanned spend").closest("tr")!.textContent).toContain("£1.20");
  expect(within(table).getByText("Salary").closest("tr")!.textContent).toContain("£3.05");
  expect(within(table).getByText("Total spent so far").closest("tr")!.textContent).toContain("£2.00");
  expect(within(table).getByText("Total external income").closest("tr")!.textContent).toContain("£4.00");
});

it("section actuals reconcile signed split spending and refunds separately from same-category income", async () => {
  sectionFixture(1, section => {
    section.detail!.lines = [...lines, { ...lines[1], id: 13, category_id: 4, original_pence: 0, effective_pence: 0 }];
    section.actuals = { ...section.actuals, spent_pence: 376, external_income_pence: 200,
      income_received_pence: 200, totals: { "11": 9999, "12": 9999 },
      spend_items: [{ ...spend[0], transaction_id: 601, category_id: 2, amount_pence: 300 },
        { ...spend[0], transaction_id: 601, category_id: 3, amount_pence: 101 },
        { ...spend[1], category_id: 2, amount_pence: -75 },
        { ...spend[2], category_id: null, amount_pence: 50 }],
      income_items: [{ ...income[0], category_id: 2, amount_pence: 80 },
        { ...income[1], category_id: 1, amount_pence: 100 },
        { ...income[2], category_id: null, amount_pence: 20 }],
    };
  });
  await openNovember();
  const section = await screen.findByRole("region", { name: "Member A budget" });
  const table = within(section).getByRole("table", { name: "Member A budget lines" });
  const sameCategory = within(table).getAllByText("Expense only").map(cell => cell.closest("tr")!);
  expect(sameCategory).toHaveLength(2);
  expect(sameCategory.find(row => row.children[1]!.textContent === "Expense")!.children[3]!.textContent).toBe("£2.25");
  expect(sameCategory.find(row => row.children[1]!.textContent === "Income")!.children[3]!.textContent).toBe("£0.80");
  expect(within(table).getByText("Zero plan").closest("tr")!.children[2]!.textContent).toBe("£0.00");
  expect(within(table).getByText("Unplanned spend").closest("tr")!.children[2]!.textContent).toBe("—");
  expect(within(table).getByText("Total spent so far").closest("tr")!.textContent).toContain("£3.76");
  expect(within(table).getByText("Total external income").closest("tr")!.textContent).toContain("£2.00");
});

it("keeps a net refund negative and shared contributions outside economic category income", async () => {
  sectionFixture(0, section => {
    section.actuals = { ...section.actuals, spent_pence: -50, external_income_pence: 100,
      income_received_pence: 100, contributions_pence: 500,
      spend_items: [{ ...spend[0], amount_pence: -50 }],
      income_items: [{ ...income[0], amount_pence: 100 }],
    };
  });
  await openNovember();
  const section = await screen.findByRole("region", { name: "Shared budget" });
  const table = within(section).getByRole("table", { name: "Shared budget lines" });
  expect(within(table).getByText("Total spent so far").closest("tr")!.textContent).toContain("-£0.50");
  expect(within(table).getByText("Total external income").closest("tr")!.textContent).toContain("£1.00");
  expect(table.textContent).not.toContain("£5.00");
  expect(section.textContent).toContain("Net contributions£5.00");
});
