import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { Reports } from "../pages/Reports";
import { Transactions } from "../pages/Transactions";

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

function CurrentSearch() {
  return <output data-testid="current-search">{useLocation().search}</output>;
}

it("keeps edited and cleared filters in the URL without dropping transaction links", async () => {
  const requested: URL[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(input => {
    const url = new URL(String(input), "http://localhost");
    let body: unknown = { items: [], total: 0 };
    if (url.pathname.endsWith("/households")) body = { items: [{ id: 1, name: "Home" }], total: 1 };
    else if (url.pathname.endsWith("/categories")) body = { items: [{ id: 2, name: "Food" }], total: 1 };
    else if (url.pathname.endsWith("/transactions/99")) return new Promise(() => {});
    else if (url.pathname.endsWith("/transactions")) requested.push(url);
    else if (url.pathname.endsWith("/reconcile/queue")) body = [];
    return Promise.resolve(new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }));
  });
  const mount = (search: string) => render(<MemoryRouter initialEntries={[`/transactions${search}`]}>
    <CurrentSearch /><Transactions />
  </MemoryRouter>);
  const user = userEvent.setup();
  const view = mount("?shared_only=true&uncategorised=true&date_from=2026-11-01&transaction_id=99&origin=report");
  const category = await screen.findByRole("combobox", { name: "Category" });
  await screen.findByRole("option", { name: "Food" });
  await user.selectOptions(category, "2");
  await waitFor(() => {
    const search = new URLSearchParams(screen.getByTestId("current-search").textContent!);
    expect(search.get("category_id")).toBe("2");
    expect(search.has("uncategorised")).toBe(false);
    expect(search.get("shared_only")).toBe("true");
    expect(search.get("transaction_id")).toBe("99");
  });
  await user.click(screen.getByRole("button", { name: "Clear" }));
  await waitFor(() => {
    const search = new URLSearchParams(screen.getByTestId("current-search").textContent!);
    expect([...search]).toEqual([["transaction_id", "99"], ["origin", "report"]]);
  });
  const cleared = screen.getByTestId("current-search").textContent!;
  view.unmount();
  requested.length = 0;
  mount(cleared);
  await waitFor(() => expect(requested.length).toBeGreaterThan(0));
  expect((screen.getByRole("combobox", { name: "Category" }) as HTMLSelectElement).value).toBe("");
  for (const url of requested) {
    expect(url.searchParams.has("shared_only")).toBe(false);
    expect(url.searchParams.has("category_id")).toBe(false);
    expect(url.searchParams.has("date_from")).toBe(false);
  }
});

it.each(["shared", "personal", "uncategorised"])("opens the exact %s report transaction scope", async (scope) => {
  const params = { date_from: "2026-11-01", date_to: "2026-11-30",
    direction: "expense", budget_side: "expense", internal: "exclude", exclude_void: true, exclude_opening: true,
    ...(scope === "uncategorised" ? { uncategorised: true } : { category_id: 2 }),
    ...(scope === "personal" ? { owner_profile_id: 7 } : { shared_only: true }) };
  const expected = Object.entries(params).map(([key, value]) => [key, String(value)]);
  vi.spyOn(globalThis, "fetch").mockImplementation(input => {
    const url = new URL(String(input), "http://localhost");
    let body: unknown = { items: [], total: 0 };
    if (url.pathname.endsWith("/households")) body = { items: [{ id: 1, name: "Home" }], total: 1 };
    else if (url.pathname.endsWith("/reports/monthly")) body = { sections: [{ key: "variance", label: "Variance", figures: [{
      key: "test", label: "Scoped budget variance", amount_pence: 100, drill: { endpoint: "/api/v1/transactions", params },
    }] }] };
    else if (url.pathname.includes("/analytics/") || url.pathname.endsWith("accuracy-ranking") || url.pathname.endsWith("/reconcile/queue")) body = [];
    else if (url.pathname.endsWith("/categories")) body = { items: [{ id: 2, household_id: 1, parent_id: null, name: "Food", is_system: false, archived: false }], total: 1 };
    else if (url.pathname.endsWith("/transactions")) {
      const matched = expected.every(([key, value]) => url.searchParams.get(key!) === value);
      body = { items: [{ id: 1, account_id: 1, date: "2026-11-08", settled_date: "2026-11-08", amount_pence: -100,
        payee: matched ? "Scoped report record" : "Unrelated ledger record", category_id: 2, status: "cleared", source: "manual", notes: null,
        external_ref: null, transfer_id: null, splits: [] }], total: 1, page: 1, size: 200 };
    }
    return Promise.resolve(new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }));
  });
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/reports"]}><Routes>
    <Route path="/reports" element={<Reports />} />
    <Route path="/transactions" element={<Transactions />} />
  </Routes></MemoryRouter>);
  const link = await screen.findByRole("link", { name: "Scoped budget variance" });
  const href = new URL(link.getAttribute("href")!, "http://localhost");
  for (const [key, value] of expected) expect(href.searchParams.get(key!)).toBe(value);
  await user.click(link);
  await screen.findByText("Scoped report record");
  expect(screen.queryByText("Unrelated ledger record")).toBeNull();
  const category = screen.getByRole("combobox", { name: "Category" }) as HTMLSelectElement;
  expect(category.value).toBe(scope === "uncategorised" ? "uncategorised" : "2");
  if (scope === "uncategorised") {
    await user.selectOptions(category, "2");
    await waitFor(() => expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(([input]) => {
      const url = new URL(String(input), "http://localhost");
      return url.pathname.endsWith("/transactions") && url.searchParams.get("category_id") === "2"
        && !url.searchParams.has("uncategorised");
    })).toBe(true));
  }
  await user.click(screen.getByRole("button", { name: "Clear" }));
  await waitFor(() => expect(screen.queryByText("Scoped report record")).toBeNull());
});

it.each(["/transactions", "/api/hassio_ingress/test-token/transactions"])("exports from the app mount on %s", (path) => {
  window.history.replaceState({}, "", path);
  vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => {}));
  render(<MemoryRouter><Transactions /></MemoryRouter>);
  const prefix = path.startsWith("/api/hassio_ingress/") ? "/api/hassio_ingress/test-token" : "";
  expect(screen.getByRole("link", { name: "Export" }).getAttribute("href")).toBe(`${prefix}/api/v1/transactions/export.csv`);
});
