/** #278: member-scope filtering on Accounts — Mine/Shared/All narrows
 * the card list by account ownership. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Accounts } from "../pages/Accounts";

const ACCOUNTS = [
  { id: 2, name: "Josh Current", type: "personal", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: 1, institution_ref: null, archived: false,
    min_balance_pence: null },
  { id: 3, name: "Chloe Current", type: "personal", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: 2, institution_ref: null, archived: false,
    min_balance_pence: null },
  { id: 4, name: "Joint Bills", type: "joint", currency: "GBP",
    opening_balance_pence: 0, opening_date: "2026-01-01",
    owner_profile_id: null, institution_ref: null, archived: false,
    min_balance_pence: null },
];

const PROFILES = [
  { id: 1, display_name: "Josh", role: "admin", is_active: true,
    totp_enabled: false },
  { id: 2, display_name: "Chloe", role: "member", is_active: true,
    totp_enabled: false },
];

function stubFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input) => {
      const url = String(input);
      const body = url.includes("/api/v1/households")
        ? { items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/api/v1/profiles")
          ? { items: PROFILES, total: PROFILES.length }
          : url.includes("/api/v1/accounts")
            ? { items: ACCOUNTS, total: ACCOUNTS.length }
            : url.includes("/api/v1/analytics/net-worth")
              ? { net_worth_pence: 0 }
              : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Accounts scope filter", () => {
  afterEach(cleanup);

  it("narrows accounts by Mine / Shared / All", async () => {
    stubFetch();
    const user = userEvent.setup();
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    await screen.findByText("Josh Current");
    await screen.findByText("Chloe Current");
    await screen.findByText("Joint Bills");

    await user.click(screen.getByRole("radio", { name: "Mine" }));
    // current profile resolves to the first active admin (Josh)
    await screen.findByText("Josh Current");
    expect(screen.queryByText("Chloe Current")).toBeNull();
    expect(screen.queryByText("Joint Bills")).toBeNull();

    await user.click(screen.getByRole("radio", { name: "Shared" }));
    await screen.findByText("Joint Bills");
    expect(screen.queryByText("Josh Current")).toBeNull();
    expect(screen.queryByText("Chloe Current")).toBeNull();

    await user.click(screen.getByRole("radio", { name: "All" }));
    await screen.findByText("Josh Current");
    expect(screen.queryByText("Chloe Current")).toBeTruthy();
    expect(screen.queryByText("Joint Bills")).toBeTruthy();
  });
});
