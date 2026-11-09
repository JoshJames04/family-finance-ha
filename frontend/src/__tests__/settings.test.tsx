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
