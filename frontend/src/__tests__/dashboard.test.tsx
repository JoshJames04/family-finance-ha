/** #123: widget registry resolves every default id; layout persists
 * via PUT /profiles/me/dashboard; role-gated widgets degrade;
 * standalone (null layout) falls back to defaults. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard";
import { DEFAULT_ORDER, WIDGETS, widgetById } from "../widgets/registry";

const dashOk = {
  role: "member",
  layout: { order: ["accounts", "upcoming"], hidden: [] },
};

function stubFetch(
  calls: { url: string; method: string; body: string }[],
  dash: object | null = dashOk,
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = url.includes("/api/v1/households")
        ? { items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/profiles/me/dashboard")
          ? dash ?? { role: null, layout: null }
          : url.includes("/api/v1/accounts")
            ? { items: [{ id: 1, name: "Joint", type: "joint",
                currency: "GBP", opening_balance_pence: 0,
                opening_date: "2026-01-01", owner_profile_id: null,
                institution_ref: null, archived: false,
                min_balance_pence: 0 }], total: 1 }
            : url.includes("expected-movements")
              ? []
              : url.includes("/projection")
                ? {
                  account_id: 1, start_balance_pence: 10000,
                  next_payday: "2026-04-28",
                  committed_until_payday_pence: 5000,
                  safe_to_spend_pence: 3000, min_balance_pence: 200,
                  min_balance_date: "2026-04-30",
                  threshold_pence: 0, below_threshold: false,
                  days: [
                    { date: "2026-04-15", balance_pence: 10000 },
                    { date: "2026-04-30", balance_pence: 200 },
                  ],
                }
                : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("widget registry", () => {
  it("resolves every DEFAULT_ORDER id", () => {
    for (const id of DEFAULT_ORDER) {
      expect(widgetById(id), `registry missing ${id}`).toBeDefined();
    }
    // Registry is the single place widgets live — ids unique.
    expect(new Set(WIDGETS.map((w) => w.id)).size)
      .toBe(WIDGETS.length);
  });
});

describe("Dashboard", () => {
  afterEach(cleanup);
  it("renders layout order from the profile dashboard API", async () => {
    stubFetch([]);
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    const cards = await screen.findAllByRole("heading", { level: 2 });
    const titles = cards.map((c) => c.textContent);
    // Stored order leads; registry widgets absent from the stored
    // layout append after it (new widgets must not vanish).
    expect(titles.slice(0, 2)).toEqual(["Accounts", "Upcoming"]);
  });
  it("admin-only widget is hidden for a member", async () => {
    stubFetch([]);
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByText("Accounts");
    expect(screen.queryByText("API status")).toBeNull();
  });
  it("falls back to default layout when no profile", async () => {
    stubFetch([], null);
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByText("Accounts");
    expect(screen.getByText("Safe to spend")).toBeTruthy();
  });
  it("reorder PUTs the new order to the member endpoint", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByText("Accounts");
    await user.click(screen.getByRole("button", { name: "Customise" }));
    await user.click(
      screen.getByRole("button", { name: "Move Upcoming up" }));
    await waitFor(() => {
      const put = calls.find((c) =>
        c.url.includes("/profiles/me/dashboard") && c.method === "PUT");
      expect(put).toBeDefined();
      expect(JSON.parse(put!.body).order[0]).toBe("upcoming");
    });
  });
});
