/** #292: the global add flow handles both one-off transactions and
 * recurring bills — picking a repeat creates a commitment and posts
 * the first payment, instead of hiding the feature behind Bills. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QuickAdd } from "../shell/QuickAdd";

function stubFetch(calls: { url: string; method: string; body: string }[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = method !== "GET"
        ? { id: 42 }
        : url.includes("/api/v1/accounts")
          ? { items: [{ id: 1, name: "Joint", type: "joint", currency: "GBP",
              opening_balance_pence: 0, opening_date: "2026-01-01",
              owner_profile_id: null, institution_ref: null,
              archived: false, min_balance_pence: null }], total: 1 }
          : url.includes("/api/v1/categories")
            ? { items: [{ id: 2, parent_id: null, name: "Groceries",
                is_system: false, archived: false }], total: 1 }
            : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

const open = () =>
  render(<QuickAdd open onOpenChange={() => {}} onSaved={() => {}} />);

describe("QuickAdd", () => {
  afterEach(cleanup);

  it("posts a one-off transaction when repeats = once", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.type(await screen.findByLabelText("Amount (£)"), "42.50");
    await user.type(screen.getByLabelText("Paid to"), "Tesco");
    await user.click(screen.getByRole("button", { name: "Save transaction" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/api/v1/transactions"));
      expect(c).toBeDefined();
      const body = JSON.parse(c!.body);
      expect(body.payee).toBe("Tesco");
      expect(body.amount).toBe("-42.50");
      expect(body.account_id).toBe(1);
    });
    expect(calls.some((c) => c.url.includes("/commitments"))).toBe(false);
  });

  it("a repeat creates a commitment and posts the first payment", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.type(await screen.findByLabelText("Amount (£)"), "28");
    await user.type(screen.getByLabelText("Paid to"), "Broadband Co");
    await user.click(screen.getByRole("radio", { name: "Every month" }));
    expect(screen.getByLabelText("Ends on")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Save transaction" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/api/v1/commitments"));
      expect(c).toBeDefined();
      const body = JSON.parse(c!.body);
      expect(body.name).toBe("Broadband Co");
      expect(body.frequency).toBe("monthly");
      expect(body.amount).toBe("-28");
      expect(body.end_date).toBeNull();
      expect(body.occurrence_count).toBeNull();
    });
    await waitFor(() => expect(calls.some((c) =>
      c.method === "POST" && c.url.includes("/commitments/42/materialise"),
    )).toBe(true));
    // No bare transaction — the commitment owns the schedule.
    expect(calls.some((c) =>
      c.method === "POST" && c.url.endsWith("/api/v1/transactions"),
    )).toBe(false);
  });

  it("an end date maps to the commitment's end_date", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    open();
    await user.type(await screen.findByLabelText("Amount (£)"), "9.99");
    await user.type(screen.getByLabelText("Paid to"), "Streaming");
    await user.click(screen.getByRole("radio", { name: "Every year" }));
    const ends = await screen.findByLabelText("Ends on");
    await user.clear(ends);
    await user.type(ends, "2027-12-31");
    await user.click(screen.getByRole("button", { name: "Save transaction" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/api/v1/commitments"));
      const body = JSON.parse(c!.body);
      expect(body.frequency).toBe("annually");
      expect(body.end_date).toBe("2027-12-31");
    });
  });
});
