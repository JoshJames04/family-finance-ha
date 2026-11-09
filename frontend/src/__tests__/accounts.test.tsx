/** #278: member-scope filtering on Accounts — Mine/Shared/All narrows
 * the card list by account ownership. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

function stubFetch(
  calls?: { url: string; method: string; body: string }[],
  rows: typeof ACCOUNTS = ACCOUNTS,
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? init.body : "";
      calls?.push({ url, method, body });
      if (method === "PATCH" && /\/api\/v1\/accounts\/\d+/.test(url)) {
        const id = Number(url.match(/accounts\/(\d+)/)![1]);
        const acct = rows.find((a) => a.id === id);
        if (acct) Object.assign(acct, JSON.parse(body));
        return new Response(JSON.stringify(acct), {
          status: 200, headers: { "Content-Type": "application/json" } });
      }
      const payload = url.includes("/api/v1/households")
        ? { items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/api/v1/profiles")
          ? { items: PROFILES, total: PROFILES.length }
          : url.includes("/api/v1/accounts")
            ? { items: rows, total: rows.length }
            : url.includes("/api/v1/analytics/net-worth")
              ? { net_worth_pence: 0 }
              : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(payload), {
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

  it("edits name and owner via PATCH", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    const rows = ACCOUNTS.map((a) => ({ ...a }));
    stubFetch(calls, rows);
    const user = userEvent.setup();
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    await screen.findByText("Joint Bills");
    await user.click(screen.getByRole("button",
      { name: "Edit Joint Bills" }));
    const nameInput = await screen.findByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "House Bills");
    await user.selectOptions(screen.getByLabelText(/Owner/), "1");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      const c = calls.find((x) => x.method === "PATCH"
        && x.url.endsWith("/accounts/4"));
      expect(c).toBeDefined();
      const body = JSON.parse(c!.body);
      expect(body).toEqual({ name: "House Bills", owner_profile_id: 1 });
    });
  });
});
