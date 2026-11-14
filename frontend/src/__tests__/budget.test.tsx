/** #125: five-value budget table, reason-enforced revision, locked
 * period renders read-only. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Budget } from "../pages/Budget";

const BUDGETS = { items: [
  { id: 1, owner_profile_id: null, name: "April", period_type: "monthly",
    start_date: "2026-04-01", end_date: "2026-04-30", status: "approved" },
], total: 1 };

const DETAIL = {
  ...BUDGETS.items[0],
  lines: [
    { id: 11, category_id: 1, side: "expense", original_pence: 30000,
      revised_pence: null, rollover: false, effective_pence: 30000 },
  ],
  read_only: false,
};

const ACTUALS = {
  budget_id: 1, totals: { "11": 12000 },
  lines: { "11": {
    original_pence: 30000, revised_pence: null, revision_reason: null,
    effective_pence: 30000, actual_pence: 12000, variance_pence: 18000,
    accuracy_pct: 40, original_accuracy_pct: 40 } },
};

const FLAT_CATS = { items: [
  { id: 1, household_id: 1, parent_id: null,
    name: "Food", is_system: false, archived: false },
], total: 1 };

function stubFetch(
  calls: { url: string; method: string; body: string }[],
  opts: { locked?: boolean; categories?: object; detail?: object;
          actuals?: object; budgets?: {items: Array<Omit<typeof BUDGETS.items[number], "owner_profile_id"> & {owner_profile_id: number | null}>; total: number}; combined?: object } = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      if (method !== "GET") {
        return Promise.resolve(new Response(JSON.stringify(DETAIL.lines[0]),
          { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      const detail = opts.locked
        ? { ...DETAIL, status: "locked" } : (opts.detail ?? DETAIL);
      const body = url.includes("/api/v1/households")
        ? { items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/reports/combined")
          ? opts.combined ?? { detail: { ...detail, id: 0, read_only: true }, actuals: opts.actuals ?? ACTUALS }
        : url.includes("/actuals")
          ? (opts.actuals ?? ACTUALS)
          : url.includes("/api/v1/budgets/1")
            ? detail
            : url.includes("/api/v1/budgets")
              ? (opts.budgets ?? BUDGETS)
              : url.includes("/api/v1/categories")
                ? (opts.categories ?? FLAT_CATS)
                : url.includes("expected-movements")
                  ? []
                  : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Budget", () => {
  afterEach(cleanup);
  it("renders planned/spent/over-under/used columns", async () => {
    stubFetch([]);
    render(<MemoryRouter><Budget /></MemoryRouter>);
    await screen.findByText("Food");
    for (const h of ["Planned", "Spent", "Over/under", "Used"]) {
      expect(screen.getByText(h)).toBeTruthy();
    }
    // Usage is £120 spent of the £300 plan → 40%.
    expect(screen.getByText("40%")).toBeTruthy();
  });
  it("#415: planned, left and bills-remaining are distinct KPIs", async () => {
    stubFetch([], { actuals: {
      ...ACTUALS,
      bills_remaining_pence: 5000, bills_remaining_count: 2 } });
    render(<MemoryRouter><Budget /></MemoryRouter>);
    await screen.findByText("Food");
    // £300 plan − £120 spent = £180 left; 2 unpaid scheduled bills
    // worth £50 — three different numbers, three different tiles.
    expect(screen.getByText("Bills remaining")).toBeTruthy();
    expect(screen.getByText("£50.00")).toBeTruthy();
    expect(screen.getByText("2 scheduled bills unpaid")).toBeTruthy();
    expect(screen.getByText("£180.00")).toBeTruthy();
    expect(screen.getByText("of plan left")).toBeTruthy();
    expect(screen.getAllByText("£300.00").length).toBeGreaterThan(0);
  });
  it("#415: overspend reads as overspent, not a bare negative", async () => {
    stubFetch([], { actuals: {
      ...ACTUALS,
      totals: { "11": 35000 },
      lines: { "11": { ...ACTUALS.lines["11"], actual_pence: 35000,
                       variance_pence: -5000 } } } });
    render(<MemoryRouter><Budget /></MemoryRouter>);
    await screen.findByText("Food");
    // £350 spent vs £300 plan → the magnitude + an explicit label.
    expect(screen.getByText("£50.00")).toBeTruthy();
    expect(screen.getByText("overspent")).toBeTruthy();
  });
  it("revision demands a reason and PATCHes with it", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Budget /></MemoryRouter>);
    await user.click(await screen.findByRole("radio", { name: "Shared" }));
    await user.click(await screen.findByLabelText("Adjust Food"));
    const reason = await screen.findByLabelText("Revision reason");
    const submit = screen.getByRole("button", { name: "Save revision" });
    expect(submit.hasAttribute("disabled")).toBe(true);
    await user.type(reason, "income drop");
    expect(submit.hasAttribute("disabled")).toBe(false);
    await user.click(submit);
    await waitFor(() => {
      const call = calls.find((c) =>
        c.url.includes("/lines/11") && c.method === "PATCH");
      expect(call).toBeDefined();
      const body = JSON.parse(call!.body);
      expect(body.reason).toBe("income drop");
      expect(body.amount).toBe("300");
    });
  });
  it("locked budget renders read-only (no revise affordance)", async () => {
    stubFetch([], { locked: true });
    render(<MemoryRouter><Budget /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("radio", { name: "Shared" }));
    await screen.findByText("locked — read-only");
    expect(screen.queryByLabelText("Adjust Food")).toBeNull();
  });
  it("groups lines into sections with subtotals when parents exist", async () => {
    const detail = {
      ...DETAIL,
      lines: [
        { id: 11, category_id: 1, side: "expense", original_pence: 30000,
          revised_pence: null, rollover: false, effective_pence: 30000 },
        { id: 12, category_id: 2, side: "expense", original_pence: 15000,
          revised_pence: null, rollover: false, effective_pence: 15000 },
      ],
    };
    const categories = { items: [
      { id: 10, household_id: 1, parent_id: null,
        name: "Household Bills", is_system: false, archived: false },
      { id: 1, household_id: 1, parent_id: 10,
        name: "Food", is_system: false, archived: false },
      { id: 2, household_id: 1, parent_id: 10,
        name: "Electric", is_system: false, archived: false },
    ], total: 3 };
    stubFetch([], { detail, categories });
    const user = userEvent.setup();
    render(<MemoryRouter><Budget /></MemoryRouter>);
    // Section header with subtotal, both children under it.
    const header = await screen.findByRole("button", { name: /Household Bills/ });
    expect(screen.getByText("Food")).toBeTruthy();
    expect(screen.getByText("Electric")).toBeTruthy();
    // Collapse hides the rows; expand brings them back.
    await user.click(header);
    await waitFor(() => expect(screen.queryByText("Food")).toBeNull());
    await user.click(await screen.findByRole("button", { name: /Household Bills/ }));
    await screen.findByText("Food");
  });
});


describe("Budget acceptance", () => {
  afterEach(cleanup);
  it("keeps the selected current shared period represented in the All picker", async () => {
    const now = new Date();
    const start = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
    const end = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-31`;
    const shared = { ...BUDGETS.items[0]!, start_date: start, end_date: end };
    stubFetch([], { budgets: { items: [{ ...shared, id: 2, owner_profile_id: 7 }, shared], total: 2 },
      combined: { detail: { ...DETAIL, id: 0, name: "All", read_only: true }, actuals: ACTUALS } });
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const picker = await screen.findByRole("combobox", { name: "Budget", hidden: true });
    await waitFor(() => expect((picker as HTMLSelectElement).value).toBe("1"));
  });
  it("uses the complete signed as-of ledger summary and shows its bridge", async () => {
    stubFetch([], { actuals: { ...ACTUALS, spent_pence: 9000,
      budget_covered_pence: 12000, unallocated_pence: -3000,
      uncategorised_pence: 0, as_of: "2026-04-15", spend_items: [
        { transaction_id: 44, date: "2026-04-14", category_id: null, amount_pence: -3000 },
      ] } });
    const user = userEvent.setup();
    render(<MemoryRouter><Budget /></MemoryRouter>);
    await screen.findByText("Spent so far", { selector: "span" });
    const stat = screen.getByText("Spent so far", { selector: "span" }).closest("dl") ?? screen.getByText("Spent so far", { selector: "span" }).parentElement!.parentElement!;
    expect(stat.textContent).toContain("£90.00");
    await user.click(screen.getByText("Reconcile spending"));
    expect(screen.getByText("#44")).toBeTruthy();
    expect(screen.getAllByText("Uncategorised").length).toBeGreaterThan(0);
  });
  it("defaults to All before owner scopes and fetches a combined period", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { budgets: { ...BUDGETS, items: [...BUDGETS.items,
      { ...BUDGETS.items[0]!, id: 2, owner_profile_id: 7 }], total: 2 },
      combined: { detail: { ...DETAIL, id: 0, name: "All", read_only: true },
        actuals: { ...ACTUALS, budget_id: 0, spent_pence: 5000 } } });
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const all = await screen.findByRole("radio", { name: "All" });
    expect(all.getAttribute("aria-checked")).toBe("true");
    await waitFor(() => expect(calls.some(c => c.url.includes("/reports/combined"))).toBe(true));
    expect(screen.queryByLabelText("Adjust Food")).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: "Shared" }));
    expect(await screen.findByLabelText("Adjust Food")).toBeTruthy();
    await waitFor(() => expect(calls.some(c => c.url.includes("expected-movements") && c.url.includes("budget_id=1"))).toBe(true));
  });
});
