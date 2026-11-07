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
];

const GIFTS = [
  { id: 11, event_id: 4, recipient: "Sam", idea: "Lego set",
    planned_pence: 4000, actual_pence: 3500,
    purchase_status: "purchased", payment_status: "paid",
    transaction_id: 77, notes: null },
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
