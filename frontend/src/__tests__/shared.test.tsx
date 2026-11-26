/** #277/#293/#297: the shared pot embedded in Budget — the split
 * (total + member £/%), per-member standing for the viewed period,
 * auto-match, share-rule management behind the disclosure. */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { SharedPot } from "../components/SharedPot";

const ACCOUNTS = [
  { id: 2, name: "Alice Current", type: "personal", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: 1, institution_ref: null, archived: false,
    min_balance_pence: null },
  { id: 3, name: "Carol Current", type: "personal", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: 2, institution_ref: null, archived: false,
    min_balance_pence: null },
  { id: 4, name: "Joint Bills", type: "joint", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: null, institution_ref: null, archived: false,
    min_balance_pence: null },
];

const PROFILES = [
  { id: 1, display_name: "Alice", role: "admin", is_active: true,
    totp_enabled: false },
  { id: 2, display_name: "Carol", role: "member", is_active: true,
    totp_enabled: false },
];

const RULES = [
  { id: 1, name: "Alice share", source_account_id: 2, dest_account_id: 4,
    amount_spec: { kind: "fixed", pence: 80000 }, frequency: "monthly",
    day_of_month: 15, start_date: "2026-01-01", end_date: null,
    active: true, notes: null },
  { id: 2, name: "Carol share", source_account_id: 3, dest_account_id: 4,
    amount_spec: { kind: "fixed", pence: 70000 }, frequency: "monthly",
    day_of_month: 15, start_date: "2026-01-01", end_date: null,
    active: true, notes: null },
];

const MONTH = (() => {
  const n = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return {
    start: iso(new Date(n.getFullYear(), n.getMonth(), 1)),
    end: iso(new Date(n.getFullYear(), n.getMonth() + 1, 0)),
  };
})();

const CYCLES = [
  { profile_id: 1, start: MONTH.start, end: MONTH.end, basis: "pay_cycle",
    payments: [{ movement_id: 11, account_id: 4, due_date: MONTH.end,
      expected_pence: 80000, matched_pence: 80000 }],
    next_payment: { movement_id: 21, account_id: 4, due_date: "2027-01-28",
      expected_pence: 80000, matched_pence: 20000 } },
  { profile_id: 2, start: MONTH.start, end: MONTH.end, basis: "calendar",
    payments: [{ movement_id: 13, account_id: 4, due_date: MONTH.end,
      expected_pence: 70000, matched_pence: 0 }] },
];

function stubFetch(calls: { url: string; method: string; body: string }[],
  opts: { rules?: object[]; alternate?: boolean; proposals?: object[] } = {}) {
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
              ? { items: opts.alternate ? [...ACCOUNTS, {
                  id: 12, name: "House Bills space", owner_profile_id: null, archived: false,
                }] : ACCOUNTS, total: ACCOUNTS.length + Number(Boolean(opts.alternate)) }
              : url.endsWith("/proposals") ? opts.proposals ?? []
              : url.includes("/shared-split/") ? {
                  policy: opts.alternate ? { alternate_account_ids: [12] } : null, next_paydays: {},
                }
              : opts.alternate && url.includes("/transactions") && url.includes("account_id=12")
                ? { items: [{ id: 70, payee: "Direct receipt", date: MONTH.start, amount_pence: 2100,
                    transfer_id: 33 }], total: 1 }
              : url.includes("/api/v1/transfer-rules")
                ? { items: rules, total: rules.length }
                : url.includes("/contributions/cycles")
                  ? CYCLES
                  : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

const open = (sharedTotal?: number) => render(
  <MemoryRouter><SharedPot sharedTotal={sharedTotal} period={{ start: MONTH.start, end: MONTH.end }} /></MemoryRouter>);

describe("SharedPot", () => {
  afterEach(cleanup);

  it("shows each member's standing for the period", async () => {
    stubFetch([]);
    open();
    // Alice satisfied, Carol outstanding — £1,500 due, £800 in.
    await screen.findByText(/^Paid$/i);
    expect(screen.getByText(/Next payment/).textContent).toContain("£600.00 left to pay");
    expect(screen.getByText(/Configure a pay-cycle source/)).toBeTruthy();
    expect(screen.getByText(/^Due$/i)).toBeTruthy();
    expect(screen.getAllByText("£1,500.00").length).toBeGreaterThan(0);
    expect(screen.getByText(/to go/)).toBeTruthy();
  });

  it("shows only budget contribution proposals in the shared pot", async () => {
    stubFetch([], { proposals: [
      { id: "budget", budget_id: 7, profile_id: 1, old_pence: 80000,
        new_pence: 81000, reason: "Budget changed", effective_date: MONTH.end },
      { id: "top-up", budget_id: null, profile_id: 1, old_pence: 80000,
        new_pence: 82000, reason: "Standing bills account top-up", effective_date: MONTH.end },
    ] });
    open();
    await screen.findByText(/Budget changed/);
    expect(screen.queryByText(/Standing bills account top-up/)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Accept change" })).toHaveLength(1);
  });

  it("shows the pot total and each member's percentage share", async () => {
    stubFetch([]);
    open();
    await screen.findByText("Shared pot — Joint Bills");
    const bar = await screen.findByRole("img", { name: /Alice \d+%, Carol \d+%/ });
    expect(bar.getAttribute("aria-label")).toBe("Alice 53%, Carol 47%");
  });

  it("Match payments runs auto-match", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole("button", { name: /Match payments/ }));
    await waitFor(() => expect(calls.some((c) =>
      c.method === "POST" && c.url.includes("/expected-movements/match"))).toBe(true));
  });

  it("keeps the payday schedule when only the share name changes", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    const rule = RULES[0]!;
    stubFetch(calls, { rules: [{ ...rule, amount_spec: {
      ...rule.amount_spec, on_member_payday: true, pence: 99500, original_pence: 80000,
      effective_amounts: [{ date: "2199-01-01", pence: 99500 }],
    } }] });
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole("button", { name: "Share rules (1)" }));
    await user.click(screen.getByRole("button", { name: "Edit Alice share" }));
    expect((screen.getByLabelText("Share amount") as HTMLInputElement).value).toBe("800");
    const name = screen.getByLabelText("Rule name");
    await user.clear(name);
    await user.type(name, "New name");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      const change = calls.find((call) => call.method === "PATCH");
      expect(change).toBeDefined();
      expect(JSON.parse(change!.body)).toEqual({
        name: "New name", source_account_id: 2, dest_account_id: 4, frequency: "monthly",
      });
    });
  });

  it("Set a share creates a fixed contribution rule", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await screen.findByText("Shared pot — Joint Bills");
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

  it("adjust-by-percentage patches each member's fixed rule", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.click(await screen.findByRole("button", { name: /Adjust split/ }));
    await user.click(screen.getByRole("radio", { name: "By percentage" }));
    const joshPct = screen.getByLabelText("Alice share percent");
    await user.clear(joshPct);
    await user.type(joshPct, "60");
    // Two members: Carol auto-balances to 40.
    expect((screen.getByLabelText("Carol share percent") as HTMLInputElement).value)
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

  it("uses the Shared target and allocates every penny when changing the split", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open(150001);
    await user.click(await screen.findByRole("button", { name: /Adjust split/ }));
    const alice = screen.getByLabelText("Alice share amount");
    await user.clear(alice);
    await user.type(alice, "801");
    expect((screen.getByLabelText("Carol share amount") as HTMLInputElement).value).toBe("699.01");
    await user.click(screen.getByRole("radio", { name: "By percentage" }));
    const pct = screen.getByLabelText("Alice share percent");
    await user.clear(pct);
    await user.type(pct, "50");
    await user.click(screen.getByRole("button", { name: "Save split" }));
    await waitFor(() => {
      const write = calls.find((call) => call.method === "POST" && call.url.endsWith("/shared-split"));
      expect(write).toBeDefined();
      const body = JSON.parse(write!.body);
      expect(body.target_pence).toBe(150001);
      expect(body.shares.map((share: { pence: number }) => share.pence)).toEqual([75001, 75000]);
    });
  });

  it("updates the fixed share percentage against the Shared total", async () => {
    stubFetch([]);
    const user = userEvent.setup();
    open(150000);
    await user.click(await screen.findByRole("button", { name: /Adjust split/ }));
    await user.click(screen.getByRole("radio", { name: "Fixed plus remainder" }));
    const dialog = within(screen.getByRole("dialog"));
    const carol = dialog.getByLabelText("Carol share amount");
    await user.clear(carol);
    await user.type(carol, "500");
    expect(dialog.getByText("33%")).toBeTruthy();
    expect(dialog.getByText("£1,000.00")).toBeTruthy();
  });

  it("records a confirmed receipt into an eligible alternate account", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { alternate: true });
    const user = userEvent.setup();
    open(150000);
    await user.click(await screen.findByRole("button", { name: /Record Carol's payment/ }));
    const dialog = within(screen.getByRole("dialog"));
    await user.selectOptions(await dialog.findByLabelText("Receiving account"), "12");
    const option = await dialog.findByRole("option", { name: /Direct receipt/ });
    await user.selectOptions(dialog.getByLabelText(/Transfer transaction/), option);
    await user.click(dialog.getByRole("button", { name: "Record payment" }));
    await waitFor(() => expect(calls.some(call => call.method === "POST"
      && call.url.endsWith("/13/matches") && JSON.parse(call.body).transaction_id === 70)).toBe(true));
  });

  it("rules list hides behind a disclosure", async () => {
    stubFetch([]);
    const user = userEvent.setup();
    open();
    await screen.findByText("Shared pot — Joint Bills");
    expect(screen.queryByText("Alice share")).toBeNull();
    await user.click(await screen.findByRole("button", { name: /Share rules/ }));
    await screen.findByText("Alice share");
    expect(screen.getByText("Carol share")).toBeTruthy();
  });

  it("prompts to set shares when no rules exist", async () => {
    stubFetch([], { rules: [] });
    open();
    await screen.findByText("No shares set");
    expect(screen.getAllByText(/no share/i).length).toBeGreaterThan(0);
  });
});
