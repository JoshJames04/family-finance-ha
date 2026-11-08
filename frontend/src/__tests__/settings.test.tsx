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
});
