/** Smoke tests for #121: shell renders, nav present, typed client
 * drives real requests, theme hook applies the scheme attribute. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { formatPence } from "../lib/format";

const PAGE = { items: [], total: 0, page: 1, size: 1 };

function stubFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    const body = url.includes("/api/v1/version")
      ? { version: "0.1.0-test" }
      : url.includes("/api/v1/households")
        ? { ...PAGE, items: [{ id: 1, name: "Home" }], total: 1 }
        : url.includes("/api/v1/expected-movements")
          ? []
          : PAGE;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
}

beforeEach(() => {
  localStorage.clear();
  stubFetch();
});
afterEach(cleanup);

describe("AppShell", () => {
  it("renders the header and all nav destinations", async () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getAllByText("Family Finance").length).toBeGreaterThan(0);
    for (const label of [
      "Dashboard",
      "Transactions",
      "Budget",
      "Accounts",
      "Upcoming",
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    await waitFor(() =>
      expect(screen.getByText("0.1.0-test")).toBeDefined(),
    );
  });

  it("applies the detected colour scheme to <html>", () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );
    expect(["light", "dark"]).toContain(
      document.documentElement.dataset.theme,
    );
  });

  it("requests use the standalone app mount", async () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
          ([u]) => String(u).startsWith("/api/v1/households"),
        ),
      ).toBe(true),
    );
  });
});

describe("formatPence", () => {
  it("formats integer pence as GBP", () => {
    expect(formatPence(123456)).toBe("£1,234.56");
    expect(formatPence(-500)).toBe("-£5.00");
  });
});
