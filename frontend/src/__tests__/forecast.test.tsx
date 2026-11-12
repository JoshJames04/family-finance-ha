/** Fixed-dataset component tests for #77: projection chart renders the
 * series with a labelled min marker; the financial calendar groups
 * movements per day and drills through to the source route; month
 * navigation and day selection are keyboard/mouse accessible. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Forecast } from "../pages/Forecast";
import { formatMonth } from "../lib/format";

const iso = (d: Date) => d.toISOString().slice(0, 10);
const today = new Date();
const plus = (n: number) => {
  const d = new Date(today);
  d.setDate(d.getDate() + n);
  return iso(d);
};

const PROJECTION = {
  account_id: 7,
  start_balance_pence: 100000,
  next_payday: plus(14),
  committed_until_payday_pence: 25000,
  safe_to_spend_pence: 75000,
  min_balance_pence: 42000,
  min_balance_date: plus(20),
  threshold_pence: 30000,
  below_threshold: false,
  days: Array.from({ length: 10 }, (_, i) => ({
    date: plus(i),
    movements_pence: i === 3 ? -58000 : 0,
    balance_pence: 100000 - (i >= 3 ? 58000 : 0),
  })),
};

const MOVEMENTS = [
  {
    id: 1, household_id: 1, account_id: 7,
    expected_date: plus(2), amount_pence: -1250,
    label: "Council tax", confidence: "high",
    source_type: "commitment", source_id: 3,
  },
  {
    id: 2, household_id: 1, account_id: 7,
    expected_date: plus(2), amount_pence: 250000,
    label: "Salary", confidence: "high",
    source_type: "payroll_run", source_id: 9,
  },
];

function stubFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    const body = url.includes("/api/v1/households")
      ? { items: [{ id: 1, name: "Home" }], total: 1 }
      : url.includes("/projection")
        ? PROJECTION
        : url.includes("/api/v1/expected-movements")
          ? MOVEMENTS
          : url.includes("/api/v1/accounts")
            ? { items: [{ id: 7, name: "Current", type: "current", archived: false }], total: 1 }
            : { items: [], total: 0 };
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
}

describe("Forecast", () => {
  afterEach(cleanup);
  it("renders chart, summary cards and calendar", async () => {
    stubFetch();
    render(<MemoryRouter><Forecast /></MemoryRouter>);
    expect(await screen.findByRole("img")).toBeDefined();
    expect(screen.getByText("Safe to spend")).toBeDefined();
    expect(screen.getByText("£750.00")).toBeDefined();
    expect(screen.getByLabelText("Previous month")).toBeDefined();
    expect(screen.getByLabelText("Next month")).toBeDefined();
    const cell = await screen.findByLabelText(`${plus(2)} — 2 events`);
    expect(cell).toBeDefined();
  });
  it("selecting a day lists events with drill-through links", async () => {
    stubFetch();
    const user = userEvent.setup();
    render(<MemoryRouter><Forecast /></MemoryRouter>);
    const cell = await screen.findByLabelText(`${plus(2)} — 2 events`);
    await user.click(cell);
    const list = await screen.findByRole("list", {
      name: `Events on ${plus(2)}`,
    });
    expect(list).toBeDefined();
    const bill = screen.getByRole("link", { name: /Council tax/ });
    expect(bill.getAttribute("href")).toBe("/bills#commitment-3");
    const payday = screen.getByRole("link", { name: /Salary/ });
    expect(payday.getAttribute("href")).toBe("/salary?run_id=9#payroll_run-9");
  });
  it("month navigation buttons change the grid", async () => {
    stubFetch();
    const user = userEvent.setup();
    render(<MemoryRouter><Forecast /></MemoryRouter>);
    await screen.findByRole("img");
    const next = new Date(today);
    next.setMonth(next.getMonth() + 1);
    const label = formatMonth(next.getFullYear(), next.getMonth());
    await user.click(screen.getByLabelText("Next month"));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: new RegExp(label) }))
        .toBeDefined(),
    );
  });
});
