/** Regression coverage for empty refresh failures and profile recovery. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { DataView } from "../components/DataView";
import { Dashboard } from "../pages/Dashboard";

// Keep this test about layout hydration: widgets must not issue their
// own households probes and consume the deliberately injected failure.
vi.mock("../api/useReview", () => ({ useReview: () => ({ total: 0 }) }));
vi.mock("../widgets/registry", () => {
  const widgets = [
    { id: "accounts", title: "Accounts", span: 1,
      Component: () => <h2>Accounts</h2> },
    { id: "upcoming", title: "Upcoming", span: 1,
      Component: () => <h2>Upcoming</h2> },
  ];
  return { DEFAULT_ORDER: ["accounts", "upcoming"], WIDGETS: widgets,
    widgetById: (id: string) => widgets.find((widget) => widget.id === id) };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

it("shows the failed refresh and Retry when the previous result was empty", () => {
  const retry = vi.fn();
  render(<DataView state={{ data: [], loading: false, retrying: false,
    error: "Refresh exhausted", refetch: retry }} empty="Nothing scheduled">
    {() => <p>Records</p>}
  </DataView>);
  expect(screen.getByRole("alert").textContent).toContain("Refresh exhausted");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("hydrates the member layout after a transient households failure recovers", async () => {
  localStorage.setItem("ff.dashboard", JSON.stringify({
    order: ["accounts", "upcoming"], hidden: [],
  }));
  let householdCalls = 0;
  let dashboardCalls = 0;
  let savedLayout: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (init?.method === "PUT") savedLayout = JSON.parse(String(init.body));
    if (url.includes("/api/v1/households") && ++householdCalls === 1) {
      return Promise.resolve(new Response("booting", { status: 503 }));
    }
    const body = url.includes("/profiles/me/dashboard")
      ? (++dashboardCalls, { role: "member", layout: {
          order: ["upcoming", "accounts"], hidden: [],
        } })
      : { items: [{ id: 1, name: "Home" }], total: 1 };
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  });
  render(<MemoryRouter><Dashboard /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Accounts", level: 2 });
  await waitFor(() => expect(dashboardCalls).toBeGreaterThan(0), { timeout: 3000 });
  await waitFor(() => {
    const titles = screen.getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    expect(titles.slice(0, 2)).toEqual(["Upcoming", "Accounts"]);
  });
  fireEvent.click(screen.getByRole("button", { name: "Customise" }));
  fireEvent.click(await screen.findByRole("button", { name: "Move Accounts up" }));
  await waitFor(() => expect(savedLayout).toEqual({ order: ["accounts", "upcoming"], hidden: [] }));
});
