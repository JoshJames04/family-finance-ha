import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Bills } from "../pages/Bills";
import { Subscriptions } from "../pages/Subscriptions";

vi.mock("../api/useApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/useApi")>(),
  useMeProfileId: () => 7,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it.each([
  ["Bills", Bills, "Bill scope"],
  ["Subscriptions", Subscriptions, "Subscription scope"],
] as const)("filters %s to used accounts in the selected ownership scope", async (_name, Page, scopeLabel) => {
  const accounts = [
    { id: 1, name: "Personal Bills", owner_profile_id: 7, archived: false },
    { id: 2, name: "House Bills", owner_profile_id: null, archived: false },
    { id: 3, name: "Work Food", owner_profile_id: 7, archived: false },
    { id: 4, name: "Archived account", owner_profile_id: 7, archived: true },
  ];
  const rows = [
    { id: 11, name: "Personal utility", account_id: 1 },
    { id: 12, name: "Shared utility", account_id: 2 },
    { id: 13, name: "Old utility", account_id: 4 },
  ].map(row => ({
    ...row, payee: row.name, next_date: "2090-01-01", next_renewal_date: "2090-01-01",
    active: true, status: "active", amount_pence: -2500, expected_pence: 2500, frequency: "monthly",
  }));
  vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
    const url = String(input);
    const body = url.includes("/households") ? { items: [{ id: 1, name: "Household" }] }
      : url.includes("/accounts") ? { items: accounts }
      : url.includes("/commitments") ? { items: rows }
      : /subscriptions(?:\?|$)/.test(url) ? rows
      : url.includes("/annualised") ? { per_subscription: [], total_annualised_pence: 0 }
      : url.includes("/subscriptions/") ? []
      : { items: [] };
    return Response.json(body);
  });
  const user = userEvent.setup();
  render(<MemoryRouter><Page /></MemoryRouter>);
  await screen.findAllByText("Personal utility");
  const scope = await screen.findByRole("radiogroup", { name: scopeLabel });
  const select = await screen.findByRole("combobox", { name: "Payment account" });
  expect(within(select).queryByRole("option", { name: "Personal Bills" })).toBeNull();
  await user.click(within(scope).getByRole("radio", { name: "Mine" }));
  expect(within(select).getByRole("option", { name: "Personal Bills" })).toBeTruthy();
  expect(within(select).queryByRole("option", { name: /Work Food|Archived account|House Bills/ })).toBeNull();
  await user.selectOptions(select, "1");
  expect(screen.queryByText("Shared utility")).toBeNull();
  await user.click(within(scope).getByRole("radio", { name: "Shared" }));
  expect((select as HTMLSelectElement).value).toBe("");
  expect(within(select).getByRole("option", { name: "House Bills" })).toBeTruthy();
  expect(screen.queryByText("Personal utility")).toBeNull();
});
