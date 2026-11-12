/** #275/#276: categorisation rules UI and canonical payee management —
 * rule toggles/create/run-now, payee list + rename/merge. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Payees } from "../pages/Payees";
import { Rules } from "../pages/Rules";

const PAYEES = [
  { payee: "TESCO STORES", transaction_count: 12 },
  { payee: "Tesco", transaction_count: 3 },
  { payee: "Sainsburys", transaction_count: 7 },
];

const RULES = [
  { id: 1, household_id: 1, pattern: "TESCO", match_type: "contains",
    category_id: 9, priority: 100, auto_apply: false,
    times_confirmed: 4, enabled: true, source: "learned" },
  { id: 2, household_id: 1, pattern: "^SPOTIFY", match_type: "regex",
    category_id: 4, priority: 50, auto_apply: true,
    times_confirmed: 0, enabled: false, source: "manual" },
];

const CATEGORIES = [
  { id: 4, name: "Entertainment", parent_id: null, kind: "expense",
    icon: null, color: null, archived: false },
  { id: 9, name: "Groceries", parent_id: null, kind: "expense",
    icon: null, color: null, archived: false },
];

function stubFetch(
  calls: { url: string; method: string; body: string }[],
  opts: { payees?: object[]; rules?: object[] } = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = method !== "GET"
        ? { renamed: 2, skipped: 1 }
        : url.includes("/api/v1/households")
          ? { items: [{ id: 1, name: "Home" }], total: 1 }
          : url.includes("/api/v1/profiles")
            ? { items: [], total: 0 }
            : url.includes("/api/v1/payees")
              ? opts.payees ?? PAYEES
              : url.includes("/api/v1/categorisation/rules")
                ? opts.rules ?? RULES
                : url.includes("/api/v1/categories")
                  ? { items: CATEGORIES, total: CATEGORIES.length }
                  : url.includes("/api/v1/categorisation/apply")
                    ? { auto_applied: 1, suggested: 2, unmatched: 0 }
                    : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Payees", () => {
  afterEach(cleanup);

  it("lists payees with counts and posts a rename", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Payees /></MemoryRouter>);
    await screen.findByText("TESCO STORES");
    await user.click(screen.getByLabelText("Rename TESCO STORES"));
    const input = screen.getByLabelText("New payee name");
    await user.clear(input);
    await user.type(input, "Tesco");
    await user.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/payees/rename"));
      expect(c).toBeDefined();
      expect(JSON.parse(c!.body)).toEqual({
        source: "TESCO STORES", target: "Tesco" });
    });
  });
});

describe("Rules", () => {
  afterEach(cleanup);

  it("lists rules and toggles enabled/auto", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Rules /></MemoryRouter>);
    await screen.findByText(/TESCO/);
    await user.click(screen.getByLabelText("Auto-apply “TESCO”"));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "PATCH" && x.url.endsWith("/rules/1"));
      expect(JSON.parse(c!.body)).toEqual({ auto_apply: true });
    });
    await user.click(screen.getByLabelText("Enable “^SPOTIFY”"));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "PATCH" && x.url.endsWith("/rules/2"));
      expect(JSON.parse(c!.body)).toEqual({ enabled: true });
    });
  });

  it("creates a rule via the dialog", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Rules /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /New rule/ }));
    await user.type(screen.getByLabelText(/Payee pattern/), "AMZN");
    await user.selectOptions(screen.getByLabelText("Rule category"), "9");
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/categorisation/rules"));
      const body = JSON.parse(c!.body);
      expect(body.pattern).toBe("AMZN");
      expect(body.category_id).toBe(9);
    });
  });

  it("Run now posts to apply", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Rules /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /Run now/ }));
    await waitFor(() => expect(calls.some((c) =>
      c.method === "POST" && c.url.includes("/categorisation/apply"))).toBe(true));
  });
});

it("creates a category rule limited to signed amount", async () => {
  const calls: { url: string; method: string; body: string }[] = [];
  stubFetch(calls);
  const user = userEvent.setup();
  render(<MemoryRouter><Rules /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: /New rule/ }));
  await user.type(screen.getByLabelText(/Payee pattern/), "Apple");
  await user.selectOptions(screen.getByLabelText("Rule category"), "9");
  await user.type(screen.getByLabelText("Exact signed amount"), "-0.99");
  await user.click(screen.getByRole("button", { name: "Create rule" }));
  await waitFor(() => {
    const call = calls.find((x) => x.method === "POST" && x.url.endsWith("/categorisation/rules"));
    expect(JSON.parse(call!.body).amount_pence).toBe(-99);
  });
  cleanup();
});
