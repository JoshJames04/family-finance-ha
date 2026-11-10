import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Settings } from "../pages/Settings";

describe("Settings year preparation", () => {
  afterEach(cleanup);

  it("requires confirmation before preparing the selected year", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      let body: object = { items: [], total: 0 };
      if (url.includes("/version")) body = { version: "0.1.0" };
      if (url.includes("/admin/backups")) body = [];
      if (url.includes("/settings/banking")) body = BANKING;
      if (url.includes("/prepare-year")) {
        body = { year: 2027, runs_created: 1, runs_skipped: 0, budgets_created: 0, warnings: [] };
      }
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" },
      }));
    });
    const user = userEvent.setup();
    render(<MemoryRouter><Settings /></MemoryRouter>);
    const prepare = await screen.findByRole("button", { name: /Prepare \d{4}/ });
    await user.click(prepare);
    expect(calls.some((call) => call.url.includes("prepare-year"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Confirm preparation" }));
    await waitFor(() => expect(calls.some((call) =>
      call.method === "POST" && call.url.includes("prepare-year"))).toBe(true));
  });

  it("downloads a snapshot file and requires confirm before restore upload", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      let body: object = { items: [], total: 0 };
      if (url.includes("/version")) body = { version: "0.1.0" };
      if (url.includes("/settings/banking")) body = BANKING;
      if (url.includes("/admin/backups")) body = [
        { name: "ff-20260101T000000.db", bytes: 1024, ok: true },
      ];
      return Promise.resolve(new Response(
        url.includes("/download") ? new Uint8Array([1, 2, 3]) : JSON.stringify(body),
        { status: 200, headers: { "Content-Type": "application/json" } }));
    });
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const user = userEvent.setup();
    render(<MemoryRouter><Settings /></MemoryRouter>);
    await user.click(await screen.findByLabelText("Download ff-20260101T000000.db"));
    await waitFor(() => expect(calls.some((call) =>
      call.url.includes("/admin/backups/ff-20260101T000000.db/download"))).toBe(true));
  });
});

const BANKING = {
  interval_minutes: 240,
  source: "default",
  editable: true,
  min_minutes: 15,
  max_minutes: 1440,
  tick_seconds: 60,
  connections: [],
};

function stubSettingsFetch(banking: object, profiles: object[]) {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: String(init?.body ?? "") });
    let body: object = { items: [], total: 0 };
    if (url.includes("/version")) body = { version: "0.1.0" };
    if (url.includes("/admin/backups")) body = [];
    if (url.includes("/profiles")) body = { items: profiles, total: profiles.length };
    if (url.includes("/settings/banking")) {
      body = method === "PATCH"
        ? { ...banking as object, interval_minutes: 15, source: "app" }
        : banking;
    }
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  });
  return calls;
}

describe("Settings banking & synchronisation", () => {
  afterEach(() => { cleanup(); localStorage.clear(); });

  it("#431: admin changes the interval and it PATCHes", async () => {
    localStorage.setItem("ff.profileId", "1");
    const calls = stubSettingsFetch(BANKING, [
      { id: 1, display_name: "Mum", role: "admin", is_active: true },
    ]);
    const user = userEvent.setup();
    render(<MemoryRouter><Settings /></MemoryRouter>);
    const select = await screen.findByLabelText(
      /Check for new bank activity every/i);
    await user.selectOptions(select, "15");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) =>
      c.method === "PATCH" && c.url.includes("/settings/banking")
        && c.body?.includes("15"))).toBe(true));
  });

  it("#431: env override renders the control read-only", async () => {
    localStorage.setItem("ff.profileId", "1");
    stubSettingsFetch(
      { ...BANKING, source: "environment", editable: false },
      [{ id: 1, display_name: "Mum", role: "admin", is_active: true }]);
    render(<MemoryRouter><Settings /></MemoryRouter>);
    const select = await screen.findByLabelText(
      /Check for new bank activity every/i);
    expect((select as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByText(/deployment configuration/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("#431: non-admin sees status but cannot change cadence", async () => {
    localStorage.setItem("ff.profileId", "2");
    stubSettingsFetch(BANKING, [
      { id: 1, display_name: "Mum", role: "admin", is_active: true },
      { id: 2, display_name: "Kid", role: "member", is_active: true },
    ]);
    render(<MemoryRouter><Settings /></MemoryRouter>);
    const select = await screen.findByLabelText(
      /Check for new bank activity every/i);
    await waitFor(() =>
      expect((select as HTMLSelectElement).disabled).toBe(true));
    expect(screen.getByText(/Only an admin/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
