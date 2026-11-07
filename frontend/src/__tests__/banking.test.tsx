/** #269–271: bank connection UX — empty-state connect CTA, token dialog
 * posts provider+token only, connection card actions, discovered-account
 * link/ignore, merge candidates in Review. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Banking } from "../pages/Banking";

const CONNECTION = {
  id: 7, provider: "starling", label: "Starling", status: "connected",
  scopes: "account:read transaction:read balance:read",
  last_validated_at: "2026-05-01T10:00:00Z", last_error: null,
};

const MAPPING = {
  id: 3, connection_id: 7, external_uid: "ext-1",
  external_label: "Joint Account", external_kind: "account",
  account_id: null, status: "proposed", balance_pence: 125000,
  balance_at: "2026-05-01T10:00:00Z",
};

function stubFetch(
  calls: { url: string; method: string; body: string }[],
  opts: { connections?: object[]; mappings?: object[] } = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      const body = method !== "GET"
        ? { ok: true }
        : url.includes("/api/v1/households")
          ? { items: [{ id: 1, name: "Home" }], total: 1 }
          : url.includes("/api/v1/banking/connections")
            ? opts.connections ?? []
            : url.includes("/api/v1/banking/mappings")
              ? opts.mappings ?? []
              : url.includes("/api/v1/accounts")
                ? { items: [{ id: 1, name: "Joint", type: "joint",
                    currency: "GBP", opening_balance_pence: 0,
                    opening_date: "2026-01-01", owner_profile_id: null,
                    institution_ref: null, archived: false,
                    min_balance_pence: null }], total: 1 }
                  : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Banking", () => {
  afterEach(cleanup);

  it("shows the connect CTA and posts only provider+token", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls);
    const user = userEvent.setup();
    render(<MemoryRouter><Banking /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /Connect Starling/ }));
    await user.type(screen.getByLabelText(/Personal access token/), "tok_test_123");
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/banking/connections"));
      expect(c).toBeDefined();
      const body = JSON.parse(c!.body);
      expect(body).toEqual({ provider: "starling", token: "tok_test_123" });
    });
  });

  it("renders a connection with status and fires sync", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { connections: [CONNECTION] });
    const user = userEvent.setup();
    render(<MemoryRouter><Banking /></MemoryRouter>);
    await screen.findByText("Connected");
    await user.click(screen.getByLabelText("Sync starling"));
    await waitFor(() => expect(calls.some((c) =>
      c.method === "POST" && c.url.endsWith("/connections/7/sync"))).toBe(true));
  });

  it("links a proposed mapping to an existing account", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { connections: [CONNECTION], mappings: [MAPPING] });
    const user = userEvent.setup();
    render(<MemoryRouter><Banking /></MemoryRouter>);
    await screen.findByText("Joint Account");
    await user.selectOptions(screen.getByLabelText("Link Joint Account to"), "1");
    await user.click(screen.getByRole("button", { name: "Link Joint Account" }));
    await waitFor(() => {
      const c = calls.find((x) =>
        x.method === "POST" && x.url.endsWith("/mappings/3/link"));
      expect(c).toBeDefined();
      expect(JSON.parse(c!.body).account_id).toBe(1);
    });
  });

  it("create-account and ignore hit their endpoints", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { connections: [CONNECTION], mappings: [MAPPING] });
    const user = userEvent.setup();
    render(<MemoryRouter><Banking /></MemoryRouter>);
    await screen.findByText("Joint Account");
    await user.click(screen.getByRole("button", { name: "Create account for Joint Account" }));
    await waitFor(() => {
      const c = calls.find((x) => x.url.endsWith("/mappings/3/link"));
      expect(JSON.parse(c!.body).create_account).toBe(true);
    });
    await user.click(screen.getByRole("button", { name: "Ignore Joint Account" }));
    await waitFor(() => expect(calls.some((c) =>
      c.method === "POST" && c.url.endsWith("/mappings/3/ignore"))).toBe(true));
  });
});
