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

it("explains disabled scheduling and does not promise an automatic next run", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const body = String(input).endsWith("/settings/banking") ? {
      interval_minutes: 15, source: "default", editable: true, scheduler_enabled: false,
      connections: [{ connection_id: 1, provider: "starling", status: "connected",
        account_id: 3, sync_status: "completed", sync_error: null, last_error: null,
        last_success_at: null, refresh_queued: false, retry_at: null, next_due_at: null }],
    } : { items: [] };
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  render(<BankingSyncPanel />);
  expect(await screen.findByText(/Automatic sync is disabled/)).toBeDefined();
  expect(screen.getByRole("button", { name: "Sync now for connection 1" }).hasAttribute("disabled")).toBe(false);
  expect(screen.queryByText(/^Next /)).toBeNull();
});

it("shows manual retry timing while automatic scheduling is disabled", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const body = String(input).endsWith("/settings/banking") ? {
      interval_minutes: 15, source: "default", editable: true, scheduler_enabled: false,
      connections: [{ connection_id: 1, provider: "starling", status: "degraded",
        account_id: 3, sync_status: "failed", sync_error: "Provider rate limited", last_error: null,
        last_success_at: null, refresh_queued: true, retry_at: "2026-11-14T15:30:00Z", next_due_at: null }],
    } : { items: [] };
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  render(<BankingSyncPanel />);
  expect(await screen.findByText(/Retry after/)).toBeDefined();
  expect(screen.getByText(/Automatic sync is disabled/)).toBeDefined();
  expect(screen.queryByText(/^Next /)).toBeNull();
});
