/** #278: member-scope filtering on Loans — Mine/Shared/All narrows
 * the card list by loan ownership. */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Loans } from "../pages/Loans";

const LOANS = [
  { id: 1, party: "Mum", direction: "we_owe", principal_pence: 50000,
    start_date: "2026-01-01", expected_payment_pence: null,
    interest_bps_pa: null, active: true, notes: null,
    owner_profile_id: 1 },
  { id: 2, party: "Dad", direction: "they_owe", principal_pence: 20000,
    start_date: "2026-01-01", expected_payment_pence: null,
    interest_bps_pa: null, active: true, notes: null,
    owner_profile_id: 2 },
  { id: 3, party: "Sibling", direction: "they_owe",
    principal_pence: 10000, start_date: "2026-01-01",
    expected_payment_pence: null, interest_bps_pa: null, active: true,
    notes: null, owner_profile_id: null },
];

const PROFILES = [
  { id: 1, display_name: "Alice", role: "admin", is_active: true,
    totp_enabled: false },
  { id: 2, display_name: "Carol", role: "member", is_active: true,
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
          : url.includes("/projection")
            ? { baseline: { months: null, payoff_date: null },
                with_extra: { months: null, payoff_date: null },
                extra_monthly_pence: 0, months_saved: null }
          : url.includes("/repayments")
            ? []
          : /\/api\/v1\/loans\/\d/.test(url)
            ? { outstanding_pence: 0, repaid_pence: 0,
                accrued_interest_pence: 0,
                expected_to_date_pence: 0, drift_pence: 0 }
            : url.includes("/api/v1/loans")
              ? { items: LOANS, total: LOANS.length }
              : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Loans scope filter", () => {
  afterEach(cleanup);

  it("narrows loans by Mine / Shared / All", async () => {
    stubFetch();
    const user = userEvent.setup();
    render(<MemoryRouter><Loans /></MemoryRouter>);
    await screen.findByText("Mum");
    await screen.findByText("Dad");
    await screen.findByText("Sibling");

    // Scope group's radios — the Direction group also has an "All".
    const scope = screen.getByRole("radiogroup", { name: "Loan scope" });
    await user.click(within(scope).getByRole("radio", { name: "Mine" }));
    // current profile resolves to the first active admin (Alice)
    await screen.findByText("Mum");
    expect(screen.queryByText("Dad")).toBeNull();
    expect(screen.queryByText("Sibling")).toBeNull();

    await user.click(within(scope).getByRole("radio", { name: "Shared" }));
    await screen.findByText("Sibling");
    expect(screen.queryByText("Mum")).toBeNull();
    expect(screen.queryByText("Dad")).toBeNull();

    await user.click(within(scope).getByRole("radio", { name: "All" }));
    await screen.findByText("Mum");
    expect(screen.queryByText("Dad")).toBeTruthy();
    expect(screen.queryByText("Sibling")).toBeTruthy();
  });
});
