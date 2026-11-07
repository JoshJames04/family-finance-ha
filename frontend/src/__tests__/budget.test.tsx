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

function stubFetch(
  calls: { url: string; method: string; body: string }[],
  opts: { locked?: boolean } = {},
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
        ? { ...DETAIL, status: "locked" } : DETAIL;
      const body = url.includes("/api/v1/households")
        ? { items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/actuals")
          ? ACTUALS
          : url.includes("/api/v1/budgets/1")
            ? detail
            : url.includes("/api/v1/budgets")
              ? BUDGETS
              : url.includes("/api/v1/categories")
                ? { items: [
                    { id: 1, household_id: 1, parent_id: null,
                      name: "Food", is_system: false, archived: false },
                  ], total: 1 }
                : url.includes("expected-movements")
                  ? []
                  : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Budget", () => {
  afterEach(cleanup);
  it("renders original/revised/actual/variance/accuracy columns", async () => {
    stubFetch([]);
    render(<MemoryRouter><Budget /></MemoryRouter>);
    await screen.findByText("Food");
    for (const h of ["Original", "Revised", "Actual", "Variance",
      "Accuracy"]) {
      expect(screen.getByText(h)).toBeTruthy();
    }
    expect(screen.getByText("40%")).toBeTruthy();
  });
  it("revision demands a reason and PATCHes with it", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Budget /></MemoryRouter>);
    await user.click(await screen.findByLabelText("Revise Food"));
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
    await screen.findByText("locked — read-only");
    expect(screen.queryByLabelText("Revise Food")).toBeNull();
  });
});
