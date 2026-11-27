/** #277: the Shared page — shared-budget total + per-section
 * breakdown over the SharedPot member cards; guided empty state when
 * no shared (unowned) budget exists. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Shared } from "../pages/Shared";

const MONTH = (() => {
  const n = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return {
    start: iso(new Date(n.getFullYear(), n.getMonth(), 1)),
    end: iso(new Date(n.getFullYear(), n.getMonth() + 1, 0)),
  };
})();

const BUDGETS = [
  { id: 7, owner_profile_id: null, name: "Household",
    period_type: "monthly", start_date: MONTH.start,
    end_date: MONTH.end, status: "approved" },
  { id: 8, owner_profile_id: 1, name: "Alice personal",
    period_type: "monthly", start_date: MONTH.start,
    end_date: MONTH.end, status: "approved" },
];

const CATEGORIES = [
  { id: 1, household_id: 1, parent_id: null, name: "Bills",
    is_system: false, archived: false },
  { id: 2, household_id: 1, parent_id: 1, name: "Council Tax",
    is_system: false, archived: false },
  { id: 3, household_id: 1, parent_id: null, name: "Food",
    is_system: false, archived: false },
  { id: 4, household_id: 1, parent_id: 3, name: "Groceries",
    is_system: false, archived: false },
];

const DETAIL = {
  ...BUDGETS[0], read_only: false,
  lines: [
    { id: 1, category_id: 2, side: "expense", original_pence: 17400,
      revised_pence: null, rollover: false, effective_pence: 17400 },
    { id: 2, category_id: 4, side: "expense", original_pence: 50000,
      revised_pence: null, rollover: false, effective_pence: 50000 },
  ],
};

const PROFILES = [
  { id: 1, display_name: "Alice", role: "admin", is_active: true,
    totp_enabled: false },
  { id: 2, display_name: "Carol", role: "member", is_active: true,
    totp_enabled: false },
];

function stubFetch(budgets: object[] = BUDGETS, detailResponse?: () => Response) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input) => {
      const url = String(input);
      if (detailResponse && /\/api\/v1\/budgets\/\d/.test(url)) return Promise.resolve(detailResponse());
      const body = url.includes("/api/v1/households")
        ? { items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/api/v1/profiles")
          ? { items: PROFILES, total: PROFILES.length }
          : url.includes("/api/v1/categories")
            ? { items: CATEGORIES, total: CATEGORIES.length }
            : /\/api\/v1\/budgets\/\d/.test(url)
              ? DETAIL
              : url.includes("/api/v1/budgets")
                ? { items: budgets, total: budgets.length }
                : url.includes("/contributions/by-member")
                  ? []
                  : url.includes("/contributions")
                    ? []
                    : url.includes("/expected-movements")
                      ? []
                      : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Shared page", () => {
  afterEach(cleanup);

  it("shows the shared total and per-section breakdown", async () => {
    stubFetch();
    render(<MemoryRouter><Shared /></MemoryRouter>);
    // £174 Council Tax + £500 Groceries = £674 shared total.
    await screen.findByText("£674.00");
    expect(screen.getByText("Bills")).toBeTruthy();
    expect(screen.getByText("Food")).toBeTruthy();
    expect(screen.getByText("£174.00")).toBeTruthy();
    expect(screen.getByText("£500.00")).toBeTruthy();
    // Only the unowned budget is considered — Alice's £0 personal
    // budget must not leak into the total.
    expect(screen.queryByText("Alice personal")).toBeNull();
  });

  it("keeps the period and funding available when the budget detail fails", async () => {
    let failed = true;
    stubFetch(BUDGETS, () => failed
      ? Response.json({ detail: "Budget detail unavailable" }, { status: 403 })
      : Response.json({ ...DETAIL, lines: [] }));
    render(<MemoryRouter><Shared /></MemoryRouter>);

    await screen.findByText(/Budget detail unavailable/);
    expect(screen.queryByText("0 planned expenses")).toBeNull();
    expect(screen.getByText("Shared pot")).toBeTruthy();
    expect(screen.getByText("Period")).toBeTruthy();
    failed = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("0 planned expenses")).toBeTruthy();
    expect(screen.getByText(/£0\.00/)).toBeTruthy();
  });

  it("guides members without a shared budget", async () => {
    stubFetch([BUDGETS[1]!]); // owned budget only
    render(<MemoryRouter><Shared /></MemoryRouter>);
    await screen.findByText("No shared budget");
    expect(screen.getByRole("button",
      { name: "Set up a shared budget" })).toBeTruthy();
  });
});
