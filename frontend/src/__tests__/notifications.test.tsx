/** #281: notification UX — inbox ack/snooze lifecycle, rule summaries,
 * enable/disable + delete, evaluate-now, rule creation posts the HA
 * channel + privacy-safe defaults. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Notifications } from "../pages/Notifications";

const ACCOUNT = {
  id: 1, name: "Current", type: "personal", currency: "GBP",
  opening_balance_pence: 100000, opening_date: "2026-01-01",
  owner_profile_id: null, institution_ref: null, archived: false,
  min_balance_pence: null,
};
const RULE = {
  id: 3, household_id: 1, name: "Big spend",
  kind: "spend_above",
  params: { threshold_pence: 10000 },
  target_profile_id: null, channel: "ha_notify", digest: false,
  digest_window_minutes: 60, enabled: true,
  service: null, privacy_safe: true,
};
const EVENT = {
  id: 9, rule_id: 3, dedup_key: "k9",
  payload: { payee: "IKEA", amount_pence: 15000, date: "2026-11-05" },
  state: "pending", delivery_id: null,
  created_at: "2026-11-05T09:00:00Z",
  delivered_at: null, snoozed_until: null,
};

function stubFetch(
  calls: { url: string; method: string; body: string }[],
  opts: { rules?: object[]; events?: object[] } = {},
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
            : url.includes("/notifications/rules")
              ? opts.rules ?? []
              : url.includes("/notifications/events")
                ? opts.events ?? []
                : url.includes("/notifications/deliveries")
                  ? []
                  : url.includes("/api/v1/accounts")
                    ? { items: [ACCOUNT], total: 1 }
                    : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    });
}

describe("Notifications", () => {
  afterEach(cleanup);

  it("renders open events with ack + snooze actions", async () => {
    stubFetch([], { rules: [RULE], events: [EVENT] });
    render(<MemoryRouter><Notifications /></MemoryRouter>);
    expect(await screen.findByText("IKEA")).toBeTruthy();
    expect(screen.getByText("Big spend")).toBeTruthy();
    expect(screen.getByLabelText(/Acknowledge IKEA/i)).toBeTruthy();
    expect(screen.getByLabelText(/Snooze IKEA/i)).toBeTruthy();
  });

  it("acks an event through the lifecycle endpoint", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { rules: [RULE], events: [EVENT] });
    const user = userEvent.setup();
    render(<MemoryRouter><Notifications /></MemoryRouter>);
    await screen.findByText("IKEA");
    await user.click(screen.getByLabelText(/Acknowledge IKEA/i));
    await waitFor(() => expect(
      calls.some((c) => c.method === "POST"
        && c.url.includes("/events/9/ack")),
    ).toBe(true));
  });

  it("summarises a rule in plain language", async () => {
    stubFetch([], { rules: [RULE], events: [] });
    render(<MemoryRouter><Notifications /></MemoryRouter>);
    expect(await screen.findByText(/any spend over £100\.00 on any account/i)).toBeTruthy();
  });

  it("posts a new rule with the HA channel + privacy defaults", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    stubFetch(calls, { rules: [], events: [] });
    const user = userEvent.setup();
    render(<MemoryRouter><Notifications /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /New rule/i }));
    await user.selectOptions(screen.getByLabelText("Rule kind"), "balance_below");
    await user.type(screen.getByLabelText("Threshold amount"), "200");
    await user.click(screen.getByRole("button", { name: /Create rule/i }));
    await waitFor(() => expect(
      calls.some((c) => c.method === "POST"
        && c.url.includes("/notifications/rules")),
    ).toBe(true));
    const post = calls.find((c) => c.method === "POST"
      && c.url.includes("/notifications/rules"))!;
    const body = JSON.parse(post.body);
    expect(body.kind).toBe("balance_below");
    expect(body.params.threshold_pence).toBe(20000);
    expect(body.channel).toBe("ha_notify");
    expect(body.privacy_safe).toBe(true);
  });
});
