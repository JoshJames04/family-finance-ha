import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import * as hooks from "../api/hooks";
import { Forecast } from "../pages/Forecast";
import { Upcoming } from "../pages/Upcoming";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it.each([Upcoming, Forecast])("distinguishes a failed schedule from a successful empty schedule on %s", async Page => {
  let failed = true;
  vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
    const url = String(input);
    if (url.includes("/expected-movements")) {
      return Response.json(failed ? { detail: "Schedule unavailable" } : [], { status: failed ? 403 : 200 });
    }
    if (url.includes("/households")) return Response.json({ items: [{ id: 1, name: "Test" }], total: 1 });
    if (url.includes("/profiles")) return Response.json({ items: [{ id: 1, display_name: "Test", is_active: true, role: "admin" }], total: 1 });
    return Response.json({ items: [], total: 0 });
  });
  render(<MemoryRouter><Page /></MemoryRouter>);
  await screen.findByText(/Schedule unavailable/);
  expect(screen.queryAllByText(/\u00a30\.00/)).toHaveLength(0);
  expect(screen.queryByText("0 movements")).toBeNull();
  expect(screen.queryByRole("button", { name: /0 events/ })).toBeNull();
  failed = false;
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect((await screen.findAllByText(/\u00a30\.00/)).length).toBeGreaterThan(0);
  expect(screen.queryByText(/Schedule unavailable/)).toBeNull();
});

it("retains a previously loaded empty schedule alongside a refresh warning", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ items: [], total: 0 }));
  vi.spyOn(hooks, "useUpcoming").mockReturnValue({
    data: [], loading: false, retrying: false, error: "Refresh failed", refetch: vi.fn(),
  });
  render(<MemoryRouter><Upcoming /></MemoryRouter>);
  expect(await screen.findByText("Refresh failed")).toBeTruthy();
  expect(screen.getByText("0 movements")).toBeTruthy();
  expect(screen.getAllByText(/\u00a30\.00/)).toHaveLength(3);
});
