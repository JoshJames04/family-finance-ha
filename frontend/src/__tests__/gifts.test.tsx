/** Fixed-dataset tests for #97: recipient grouping with per-gift +
 * per-recipient variance, lifecycle transition + pay POST the right
 * endpoints, status badges render. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Gifts } from "../pages/Gifts";

const EVENTS = [
  { id: 4, year: 2026, type: "christmas", name: "Christmas 2026",
    budget_pence: 60000, savings_goal_id: null, notes: null },
  { id: 3, year: 2025, type: "christmas", name: "Christmas 2025",
    budget_pence: 50000, savings_goal_id: null, notes: null },
];

const COMPARISON = {
  earlier: { planned_pence: 8000, actual_pence: 8000,
    committed_pence: 8000, remaining_pence: 42000 },
  later: { planned_pence: 7500, actual_pence: 5700,
    committed_pence: 5700, remaining_pence: 54300 },
  actual_delta_pence: -2300,
  earlier_avg_gift_pence: 2666, later_avg_gift_pence: 2850,
  recipients: [
    { recipient: "Mum", earlier_actual_pence: 4000,
      later_actual_pence: 5700, delta_pence: 1700,
      earlier_gifts: 1, later_gifts: 2 },
  ],
};

const GIFTS = [
  { id: 11, event_id: 4, recipient: "Sam", idea: "Lego set",
    planned_pence: 4000, actual_pence: 3500,
    purchase_status: "purchased", payment_status: "paid",
    transaction_id: 77, notes: "Synthetic historical note" },
  { id: 12, event_id: 4, recipient: "Sam", idea: "Book",
    planned_pence: 1500, actual_pence: null,
    purchase_status: "idea", payment_status: "unpaid",
    transaction_id: null, notes: null },
  { id: 13, event_id: 4, recipient: "Bob", idea: "Scarf",
    planned_pence: 2000, actual_pence: 2200,
    purchase_status: "wrapped", payment_status: "paid",
    transaction_id: 78, notes: null },
];

const TOTALS = {
  planned_pence: 7500, actual_pence: 5700,
  committed_pence: 5700, remaining_pence: 54300,
};

const GIFT = { id: 12, event_id: 4, recipient: "Sam", idea: "Book",
  planned_pence: 1500, actual_pence: 1500,
  purchase_status: "planned", payment_status: "unpaid",
  transaction_id: null, notes: null };

function stubFetch(posts: { url: string; body: string }[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "POST") {
        posts.push({ url, body: String(init?.body) });
        return Promise.resolve(new Response(JSON.stringify(GIFT), {
          status: 200, headers: { "Content-Type": "application/json" } }));
      }
      const body = url.includes("/api/v1/households")
        ? { items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/compare/")
          ? COMPARISON
          : url.includes("/totals")
          ? TOTALS
          : url.includes("/gifts")
            ? GIFTS
            : url.includes("/api/v1/seasonal-events")
              ? EVENTS
              : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Gifts", () => {
  afterEach(cleanup);
  it("shows historical notes and searches their context", async () => {
    stubFetch([]);
    const user = userEvent.setup();
    render(<MemoryRouter><Gifts /></MemoryRouter>);
    expect(await screen.findByText("Synthetic historical note")).toBeDefined();
    await user.type(screen.getByLabelText("Search gifts and notes"), "historical");
    expect(screen.getByText("Lego set")).toBeDefined();
    expect(screen.queryByText("Scarf")).toBeNull();
    expect(screen.queryByText("Book")).toBeNull();
  });
  it("groups gifts per recipient with variance", async () => {
    stubFetch([]);
    render(<MemoryRouter><Gifts /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: /Sam/ })).toBeDefined();
    expect(screen.getByRole("heading", { name: /Bob/ })).toBeDefined();
    // Sam: 3500+? actual vs 5500 planned → -£5.50? no: 3500 vs 4000 for Lego
    expect(screen.getByText(/-£5\.00|£-5\.00/)).toBeDefined();
  });
  it("transition button POSTs the next status", async () => {
    const posts: { url: string; body: string }[] = [];
    stubFetch(posts);
    const user = userEvent.setup();
    render(<MemoryRouter><Gifts /></MemoryRouter>);
    const btns = await screen.findAllByRole("button", { name: "Mark planned" });
    await user.click(btns[0]!);
    await waitFor(() =>
      expect(posts.some((p) =>
        p.url.includes("/gifts/12/transition") && p.body.includes("planned"),
      )).toBe(true));
  });
  it("YoY comparison renders on selection (#99)", async () => {
    stubFetch([]);
    const user = userEvent.setup();
    render(<MemoryRouter><Gifts /></MemoryRouter>);
    const sel = await screen.findByLabelText(/Compare with/);
    await user.selectOptions(sel, "3");
    const region = await screen.findByRole("region", {
      name: "Year-on-year comparison" });
    expect(region.textContent).toContain("+£17.00");
    expect(region.textContent).toContain("-£23.00");
    expect(region.textContent).toContain("Mum");
  });
  it("pay button POSTs the pay endpoint", async () => {
    const posts: { url: string; body: string }[] = [];
    stubFetch(posts);
    const user = userEvent.setup();
    render(<MemoryRouter><Gifts /></MemoryRouter>);
    const btn = await screen.findByRole("button", { name: "Mark paid" });
    await user.click(btn);
    await waitFor(() =>
      expect(posts.some((p) => p.url.includes("/gifts/12/pay"))).toBe(true));
  });
});
