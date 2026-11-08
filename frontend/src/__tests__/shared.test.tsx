/** #277/#278: the shared pot — per-member standing from transfer-rule
 * movements joined to contribution status, the pot's requirements,
 * auto-match trigger and share-rule creation. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Shared } from "../pages/Shared";

const ACCOUNTS = [
  { id: 2, name: "Josh Current", type: "personal", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: 1, institution_ref: null, archived: false,
    min_balance_pence: null },
  { id: 3, name: "Chloe Current", type: "personal", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: 2, institution_ref: null, archived: false,
    min_balance_pence: null },
  { id: 4, name: "Joint Bills", type: "joint", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: null, institution_ref: null, archived: false,
    min_balance_pence: null },
];

const PROFILES = [
  { id: 1, display_name: "Josh", role: "admin", is_active: true,
    totp_enabled: false },
  { id: 2, display_name: "Chloe", role: "member", is_active: true,
    totp_enabled: false },
];

const RULES = [
  { id: 1, name: "Josh share", source_account_id: 2, dest_account_id: 4,
    amount_spec: { kind: "fixed", pence: 80000 }, frequency: "monthly",
    day_of_month: 15, start_date: "2026-01-01", end_date: null,
    active: true, notes: null },
  { id: 2, name: "Chloe share", source_account_id: 3, dest_account_id: 4,
    amount_spec: { kind: "fixed", pence: 70000 }, frequency: "monthly",
    day_of_month: 15, start_date: "2026-01-01", end_date: null,
    active: true, notes: null },
];

const MONTH = (() => {
  const n = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return {
    due: iso(new Date(n.getFullYear(), n.getMonth() + 1, 0)), // end of month — always pending
    in: iso(new Date(n.getFullYear(), n.getMonth(), 5)),
  };
})();

const MOVEMENTS = [
  { id: 10, source_type: "transfer_rule", source_id: 1, account_id: 2,
    expected_date: MONTH.due, amount_pence: -80000, label: "Josh share (out)" },
  { id: 11, source_type: "transfer_rule", source_id: 1, account_id: 4,
    expected_date: MONTH.due, amount_pence: 80000, label: "Josh share (in)" },
  { id: 12, source_type: "transfer_rule", source_id: 2, account_id: 3,
    expected_date: MONTH.due, amount_pence: -70000, label: "Chloe share (out)" },
  { id: 13, source_type: "transfer_rule", source_id: 2, account_id: 4,
    expected_date: MONTH.due, amount_pence: 70000, label: "Chloe share (in)" },
];

const CONTRIBUTIONS = [
  { movement_id: 10, expected_pence: 80000, matched_pence: 80000,
    outstanding_pence: 0, state: "satisfied" },
  { movement_id: 11, expected_pence: 80000, matched_pence: 80000,
    outstanding_pence: 0, state: "satisfied" },
  { movement_id: 12, expected_pence: 70000, matched_pence: 0,
    outstanding_pence: 70000, state: "pending" },
  { movement_id: 13, expected_pence: 70000, matched_pence: 0,
    outstanding_pence: 70000, state: "pending" },
];

const BY_MEMBER = [
  { profile_id: 1, display_name: "Josh", contributed_pence: 80000 },
  { profile_id: 2, display_name: "Chloe", contributed_pence: 0 },
];

const REQUIREMENTS = [
  { account_id: 4, due_date: MONTH.due, scheduled_pence: 80000,
    satisfied_pence: 80000, outstanding_pence: 0 },
  { account_id: 4, due_date: MONTH.due, scheduled_pence: 70000,
    satisfied_pence: 0, outstanding_pence: 70000 },
];

function stubFetch(calls: { url: string; method: string; body: string }[],
  opts: { rules?: object[] } = {}) {
  const rules = opts.rules ?? RULES;
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = method !== "GET"
        ? { ok: true }
        : url.includes("/api/v1/households")
          ? { items: [{ id: 1, name: "Home" }], total: 1 }
          : url.includes("/api/v1/profiles")
            ? { items: PROFILES, total: PROFILES.length }
            : url.includes("/api/v1/accounts")
              ? { items: ACCOUNTS, total: ACCOUNTS.length }
              : url.includes("/api/v1/transfer-rules")
                ? { items: rules, total: rules.length }
                : url.includes("/contributions/by-member")
                  ? BY_MEMBER
                  : url.includes("/contributions")
                    ? CONTRIBUTIONS
                    : url.includes("/requirements")
                      ? REQUIREMENTS
                      : url.includes("/outstanding")
                        ? []
                        : url.includes("/expected-movements")
                          ? MOVEMENTS
                          : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Shared", () => {
  afterEach(cleanup);

  it("shows each member's standing and the pot totals", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    render(<MemoryRouter><Shared /></MemoryRouter>);
    // Josh satisfied, Chloe outstanding — £1,500 due, £800 in.
    await screen.findByText(/satisfied/i);
    expect(screen.getByText(/^outstanding$/i)).toBeTruthy();
    expect(screen.getAllByText("£1,500.00").length).toBeGreaterThan(0);
    expect(screen.getByText(/to go/)).toBeTruthy();
    expect(screen.getByText("Josh share")).toBeTruthy();
    expect(screen.getByText("Chloe share")).toBeTruthy();
    // pot requirement aggregates both in-legs: £800 outstanding of £1,500
    expect(screen.getByText("Joint Bills")).toBeTruthy();
  });

  it("Match payments runs auto-match", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Shared /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /Match payments/ }));
    await waitFor(() => expect(calls.some((c) =>
      c.method === "POST" && c.url.includes("/expected-movements/match"))).toBe(true));
  });

  it("Set a share creates a fixed contribution rule", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Shared /></MemoryRouter>);
    await screen.findByText("Josh share");
    await user.click(screen.getByRole("button", { name: /Set a share/ }));
    await user.selectOptions(screen.getByLabelText(/From their account/), "2");
    await user.selectOptions(screen.getByLabelText(/Into the shared account/), "4");
    await user.type(screen.getByLabelText(/Amount/), "50");
    await user.click(screen.getByRole("button", { name: "Set share" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/transfer-rules"));
      expect(c).toBeDefined();
      const body = JSON.parse(c!.body);
      expect(body.source_account_id).toBe(2);
      expect(body.dest_account_id).toBe(4);
      expect(body.amount_spec).toEqual({ kind: "fixed", pence: 5000 });
    });
  });

  it("prompts to set shares when no rules exist", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { rules: [] });
    render(<MemoryRouter><Shared /></MemoryRouter>);
    await screen.findByText("Set up the shared pot");
    expect(screen.getAllByText(/no share/i).length).toBeGreaterThan(0);
  });

  it("shows the pot total and each member's percentage share", async () => {
    stubFetch([]);
    render(<MemoryRouter><Shared /></MemoryRouter>);
    // The split panel — £1,500 total, Josh 53%, Chloe 47%.
    await screen.findByText("The split — Joint Bills");
    const bar = screen.getByRole("img", { name: /Josh \d+%, Chloe \d+%/ });
    expect(bar.getAttribute("aria-label")).toBe("Josh 53%, Chloe 47%");
  });

  it("adjust-by-percentage patches each member's fixed rule", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Shared /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /Adjust split/ }));
    await user.click(screen.getByRole("radio", { name: "By percentage" }));
    const joshPct = screen.getByLabelText("Josh share percent");
    await user.clear(joshPct);
    await user.type(joshPct, "60");
    // Two members: Chloe auto-balances to 40.
    expect((screen.getByLabelText("Chloe share percent") as HTMLInputElement).value)
      .toBe("40");
    await user.click(screen.getByRole("button", { name: "Save split" }));
    await waitFor(() => {
      const p1 = calls.find((x) =>
        x.method === "PATCH" && x.url.endsWith("/transfer-rules/1"));
      const p2 = calls.find((x) =>
        x.method === "PATCH" && x.url.endsWith("/transfer-rules/2"));
      expect(p1).toBeDefined();
      expect(p2).toBeDefined();
      // 60/40 of the £1,500 total.
      expect(JSON.parse(p1!.body).amount_spec).toEqual({ kind: "fixed", pence: 90000 });
      expect(JSON.parse(p2!.body).amount_spec).toEqual({ kind: "fixed", pence: 60000 });
    });
  });
});
