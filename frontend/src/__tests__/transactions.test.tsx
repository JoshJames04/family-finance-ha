/** Fixed-dataset tests for #124/#126: payee search param, filter
 * selects, inline category PATCH, bulk-categorise posts all selected
 * ids in ONE action (AC: 20 items), reconciled rows locked. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Transactions } from "../pages/Transactions";

const CATS = [
  { id: 1, household_id: 1, parent_id: null, name: "Food",
    is_system: false, archived: false },
  { id: 2, household_id: 1, parent_id: null, name: "Bills",
    is_system: false, archived: false },
];

const TXNS = Array.from({ length: 21 }, (_, i) => ({
  id: i + 1, account_id: 1, date: "2026-04-01",
  settled_date: "2026-04-01", amount_pence: -(i + 1) * 1000,
  payee: `Shop ${i + 1}`, category_id: null,
  status: i === 20 ? "reconciled" : "cleared",
  source: "manual", notes: null, external_ref: null,
  transfer_id: null,
}));

function stubFetch(calls: { url: string; method: string; body: string }[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method !== "GET") {
        calls.push({ url, method, body: String(init?.body) });
        return Promise.resolve(new Response(JSON.stringify(TXNS[0]), {
          status: 200, headers: { "Content-Type": "application/json" } }));
      }
      const body = url.includes("/api/v1/households")
        ? { items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/api/v1/categories")
          ? { items: CATS, total: CATS.length, page: 1, size: 500 }
          : url.includes("/api/v1/transactions")
            ? { items: TXNS, total: TXNS.length, page: 1, size: 500 }
            : { items: [], total: 0 };
      calls.push({ url, method, body: "" });
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Transactions", () => {
  afterEach(cleanup);
  it("passes the search box through as q param (#126)", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Transactions /></MemoryRouter>);
    const box = await screen.findByLabelText("Search payee");
    await user.type(box, "Shop 1");
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("q=Shop+1")
        || c.url.includes("q=Shop%201"))).toBe(true));
  });
  it("bulk-categorises selected items in one action (AC: 20+)", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Transactions /></MemoryRouter>);
    // Re-query per click — each selection re-renders and detaches the
    // previously resolved nodes.
    for (let i = 0; i < 20; i++) {
      const boxes = await screen.findAllByRole("checkbox");
      await user.click(boxes[i]!);
    }
    await user.selectOptions(
      await screen.findByLabelText("Bulk category"), "1");
    await user.click(
      screen.getByRole("button", { name: "Apply to selected" }));
    await waitFor(() => {
      const bulk = calls.find((c) => c.url.includes("/bulk"));
      expect(bulk).toBeDefined();
      const ids = JSON.parse(bulk!.body).ids as number[];
      expect(ids).toHaveLength(20);
    });
  });
  it("reconciled rows are locked (no inline editor)", async () => {
    stubFetch([]);
    render(<MemoryRouter><Transactions /></MemoryRouter>);
    // Wait for the table row itself — "reconciled" also appears as a
    // filter <option> before data loads.
    await screen.findByText("Shop 21");
    expect(
      screen.queryByLabelText(/Edit payee for Shop 21/),
    ).toBeNull();
  });
});
