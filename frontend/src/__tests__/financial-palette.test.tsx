import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import { CommandPalette } from "../shell/CommandPalette";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

function Destination() { return <output aria-label="Destination">{useLocation().search}</output>; }

it("searches financial records and carries the query and exact transaction into navigation", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
  const queries: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    let body: unknown = { items: [] };
    if (url.includes("/households")) body = { items: [{ id: 1, name: "Synthetic" }] };
    if (url.includes("/transactions")) {
      queries.push(url);
      body = { items: [{ id: 7, payee: "Test Shop", date: "2026-01-02", amount_pence: -1234 }], total: 1 };
    }
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const user = userEvent.setup();
  render(<MemoryRouter><CommandPalette open onOpenChange={() => {}} onQuickAdd={() => {}} /><Destination /></MemoryRouter>);
  await user.type(screen.getByRole("combobox"), "birthday");
  const match = await screen.findByRole("option", { name: /Test Shop/ });
  await user.click(match);
  await waitFor(() => expect(screen.getByLabelText("Destination").textContent).toContain("transaction_id=7"));
  expect(screen.getByLabelText("Destination").textContent).toContain("q=birthday");
  expect(queries.some((url) => url.includes("q=birthday"))).toBe(true);
});
