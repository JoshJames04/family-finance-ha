import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
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

it.each([
  "sync already running; automatic sync is disabled, so retry after the active sync finishes",
  "automatic sync is disabled; refresh was not queued. Retry after 2026-11-14T15:30:00",
])("shows retryable manual failure without a queued success: %s", async (message) => {
  const error = vi.spyOn(toast, "error").mockReturnValue("retry-error");
  const success = vi.spyOn(toast, "success").mockReturnValue("retry-success");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST" && String(input).endsWith("/connections/1/sync")) {
      return new Response(JSON.stringify({ type: "about:blank", title: message, status: 409 }),
        { status: 409, headers: { "Content-Type": "application/problem+json" } });
    }
    const body = String(input).endsWith("/settings/banking") ? {
      interval_minutes: 15, source: "default", editable: true, scheduler_enabled: false,
      connections: [{ connection_id: 1, provider: "starling", status: "connected",
        account_id: 3, sync_status: "completed", sync_error: null, last_error: null,
        last_success_at: null, refresh_queued: false, retry_at: null, next_due_at: null }],
    } : { items: [] };
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  render(<BankingSyncPanel />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Sync now for connection 1" }));
  await waitFor(() => expect(error).toHaveBeenCalledWith(`409 ${message}`));
  expect(success).not.toHaveBeenCalled();
  expect(screen.queryByText("queued", { exact: true })).toBeNull();
});

it.each([true, false])("labels historical queue flags using worker availability: %s", async (enabled) => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const body = String(input).endsWith("/settings/banking") ? {
      interval_minutes: 15, source: "default", editable: true, scheduler_enabled: enabled,
      connections: [{ connection_id: 1, provider: "starling", status: "connected",
        account_id: 3, sync_status: "completed", sync_error: null, last_error: null,
        last_success_at: null, refresh_queued: true, retry_at: null,
        next_due_at: "2026-11-14T15:30:00Z" }],
    } : { items: [] };
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  render(<BankingSyncPanel />);
  if (enabled) {
    expect(await screen.findByText("queued", { exact: true })).toBeDefined();
    expect(screen.getByText(/^Runs /)).toBeDefined();
    expect(screen.queryByText("Manual refresh needed")).toBeNull();
  } else {
    expect(await screen.findByText("Manual refresh needed")).toBeDefined();
    expect(screen.queryByText("queued", { exact: true })).toBeNull();
    expect(screen.queryByText(/^Runs /)).toBeNull();
    expect(screen.queryByText(/^Next /)).toBeNull();
  }
});

it.each(["completed", "failed"])("reports a processed manual request without claiming a queue: %s", async (status) => {
  const info = vi.spyOn(toast, "info").mockReturnValue("processed-info");
  const success = vi.spyOn(toast, "success").mockReturnValue("processed-success");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST" && String(input).endsWith("/connections/1/sync")) {
      return new Response(JSON.stringify({ runs: [{ status, inserted: 0 }] }),
        { headers: { "Content-Type": "application/json" } });
    }
    const body = String(input).endsWith("/settings/banking") ? {
      interval_minutes: 15, source: "default", editable: true, scheduler_enabled: false,
      connections: [{ connection_id: 1, provider: "starling", status: "connected",
        account_id: 3, sync_status: status, sync_error: null, last_error: null,
        last_success_at: null, refresh_queued: false, retry_at: null, next_due_at: null }],
    } : { items: [] };
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  render(<BankingSyncPanel />);
  await userEvent.setup().click(await screen.findByRole("button", { name: "Sync now for connection 1" }));
  await waitFor(() => expect(info).toHaveBeenCalledWith(
    "Sync request processed. Check the account status below."));
  expect(success).not.toHaveBeenCalled();
  expect(screen.queryByText("queued", { exact: true })).toBeNull();
});
