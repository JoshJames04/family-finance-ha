import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BankingSyncPanel } from "../pages/Settings";

vi.mock("../api/hooks", () => ({ useReady: () => true }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("distinguishes connected credentials from a failed account sync and retry", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const body = String(input).endsWith("/settings/banking") ? {
      interval_minutes: 15, source: "database", editable: true,
      connections: [{ connection_id: 1, provider: "starling", status: "connected",
        account_id: 3, sync_status: "failed", sync_error: "Bank rate limit — retry later",
        last_error: null, last_success_at: null, refresh_queued: true,
        retry_at: "2026-11-12T21:30:00Z", next_due_at: "2026-11-12T21:30:00Z" }],
    } : { items: [] };
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  render(<BankingSyncPanel />);
  expect(await screen.findByText("Sync failed")).toBeDefined();
  expect(screen.getByText("Bank rate limit — retry later")).toBeDefined();
  expect(screen.getByText("connected")).toBeDefined();
  expect(screen.getByText(/Retry after/)).toBeDefined();
});
