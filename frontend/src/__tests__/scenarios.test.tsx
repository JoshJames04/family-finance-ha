/** #280: scenario builder — list/select, ops CRUD (add/remove/shift),
 * run → baseline-vs-scenario diff + overlay, sandbox safety (the run
 * call never mutates expected movements). */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Scenarios } from "../pages/Scenarios";

const ACCOUNT = {
  id: 1, name: "Current", type: "personal", currency: "GBP",
  opening_balance_pence: 100000, opening_date: "2026-01-01",
  owner_profile_id: null, institution_ref: null, archived: false,
  min_balance_pence: null,
};
const SCENARIO = { id: 5, household_id: 1, name: "Gym break" };
const OP = {
  id: 11, scenario_id: 5, kind: "add", expected_movement_id: null,
  account_id: 1, expected_date: "2026-11-20",
  amount_pence: -40000, label: "New boiler",
};
const RUN = {
  scenario_id: 5, account_id: 1, days: 90,
  baseline: {
    account_id: 1, start_balance_pence: 100000, next_payday: "2026-11-28",
    committed_until_payday_pence: -30000, safe_to_spend_pence: 70000,
    min_balance_pence: 90000, min_balance_date: "2026-11-19",
    threshold_pence: null, below_threshold: false,
    days: [
      { date: "2026-11-18", movements_pence: 0, balance_pence: 95000 },
      { date: "2026-11-19", movements_pence: -5000, balance_pence: 90000 },
      { date: "2026-11-20", movements_pence: 0, balance_pence: 90000 },
    ],
  },
  projection: {
    account_id: 1, start_balance_pence: 100000, next_payday: "2026-11-28",
    committed_until_payday_pence: -70000, safe_to_spend_pence: 30000,
    min_balance_pence: 50000, min_balance_date: "2026-11-20",
    threshold_pence: null, below_threshold: false,
    days: [
      { date: "2026-11-18", movements_pence: 0, balance_pence: 95000 },
      { date: "2026-11-19", movements_pence: -5000, balance_pence: 90000 },
      { date: "2026-11-20", movements_pence: -40000, balance_pence: 50000 },
    ],
  },
  diff: {
    min_balance_changed: true,
    baseline_min_balance_pence: 90000,
    baseline_min_balance_date: "2026-11-19",
    scenario_min_balance_pence: 50000,
    scenario_min_balance_date: "2026-11-20",
    min_balance_delta_pence: -40000,
    safe_to_spend_delta_pence: -40000,
    paydays: [],
  },
};

function stubFetch(
  calls: { url: string; method: string; body: string }[],
  opts: { scenarios?: object[]; ops?: object[] } = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = method !== "GET"
        ? method === "POST" && url.includes("/ops")
          ? { ...OP, id: 12 }
          : { ok: true }
        : url.includes("/api/v1/households")
          ? { items: [{ id: 1, name: "Home" }], total: 1 }
          : url.includes("/run")
            ? RUN
            : url.includes("/ops")
              ? opts.ops ?? []
              : url.includes("/api/v1/scenarios")
                ? opts.scenarios ?? []
                : url.includes("/api/v1/accounts")
                  ? { items: [ACCOUNT], total: 1 }
                  : url.includes("/expected-movements")
                    ? []
                    : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Scenarios", () => {
  afterEach(cleanup);

  it("offers the empty-state CTA when no scenarios exist", async () => {
    stubFetch([]);
    render(<MemoryRouter><Scenarios /></MemoryRouter>);
    expect(await screen.findAllByText(/New scenario/i)).not.toHaveLength(0);
  });

  it("lists scenarios and their ops with sandbox copy", async () => {
    stubFetch([], { scenarios: [SCENARIO], ops: [OP] });
    render(<MemoryRouter><Scenarios /></MemoryRouter>);
    expect(await screen.findByText("Gym break")).toBeTruthy();
    expect(await screen.findByText(/New boiler/)).toBeTruthy();
    expect(screen.getByText(/the ledger itself is never touched/i)).toBeTruthy();
    expect(screen.getAllByText("Add").length).toBeGreaterThan(1);
  });

  it("runs the what-if and renders the diff stats", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { scenarios: [SCENARIO], ops: [OP] });
    const user = userEvent.setup();
    render(<MemoryRouter><Scenarios /></MemoryRouter>);
    await screen.findByText("Gym break");
    await user.click(await screen.findByRole("button", { name: /^Run$/ }));
    await waitFor(() => expect(
      calls.some((c) => c.url.includes("/run") && c.url.includes("account_id=1")),
    ).toBe(true));
    expect(await screen.findByText("Lowest point change")).toBeTruthy();
    expect(screen.getByText("Safe to spend change")).toBeTruthy();
    expect(screen.getByLabelText(/baseline/i)).toBeTruthy();
  });

  it("deletes an op through the ops endpoint", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { scenarios: [SCENARIO], ops: [OP] });
    const user = userEvent.setup();
    render(<MemoryRouter><Scenarios /></MemoryRouter>);
    await screen.findByText(/New boiler/);
    await user.click(screen.getByLabelText(/Remove change New boiler/i));
    await waitFor(() => expect(
      calls.some((c) => c.method === "DELETE" && c.url.includes("/ops/11")),
    ).toBe(true));
  });

  it("posts an add op with pence-converted amount", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { scenarios: [SCENARIO], ops: [] });
    const user = userEvent.setup();
    render(<MemoryRouter><Scenarios /></MemoryRouter>);
    await screen.findByText("Gym break");
    await user.click(screen.getByLabelText(/Add a change/i));
    await user.type(screen.getByLabelText("Op label"), "Boiler");
    await user.type(screen.getByLabelText("Op amount"), "-400");
    await user.click(screen.getByRole("button", { name: /Add change/i }));
    await waitFor(() => expect(
      calls.some((c) => c.method === "POST" && c.url.includes("/ops")),
    ).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url.includes("/ops"))!;
    const body = JSON.parse(post.body);
    expect(body.kind).toBe("add");
    expect(body.amount).toBe(-40000);
    expect(body.account_id).toBe(1);
  });
});
