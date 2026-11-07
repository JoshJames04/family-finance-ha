/** Keyboard + landmark tests for #127: skip-link, drawer dialog
 * semantics (Escape, focus management), nav completeness. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AppShell, NAV_ITEMS } from "../shell/AppShell";

const PAGE = { items: [], total: 0, page: 1, size: 1 };

function stubFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(PAGE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
}

vi.stubGlobal("matchMedia", (query: string) => ({
  matches: false,
  media: query,
  addEventListener: () => {},
  removeEventListener: () => {},
}));

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AppShell />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AppShell accessibility", () => {
  it("exposes a skip link as the first focusable element", async () => {
    stubFetch();
    renderShell();
    const user = userEvent.setup();
    await user.tab();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(document.activeElement).toBe(skip);
    expect(skip.getAttribute("href")).toBe("#ff-main");
    expect(document.getElementById("ff-main")).not.toBeNull();
  });

  it("renders nav landmarks covering every section", () => {
    stubFetch();
    renderShell();
    const navs = screen.getAllByRole("navigation", { name: "Main" });
    expect(navs.length).toBeGreaterThan(0);
    for (const item of NAV_ITEMS) {
      expect(navs.some((n) => n.textContent?.includes(item.label)))
        .toBe(true);
    }
  });

  it("drawer behaves as a modal dialog: Escape closes and focus returns", async () => {
    stubFetch();
    renderShell();
    const user = userEvent.setup();
    const burger = screen.getByRole("button", {
      name: "Open navigation",
    });

    await user.click(burger);
    const drawer = screen.getByRole("dialog");
    expect(burger.getAttribute("aria-expanded")).toBe("true");
    expect(burger.getAttribute("aria-controls")).toBe("ff-drawer");
    // focus moved into the dialog on open
    await waitFor(() =>
      expect(drawer.contains(document.activeElement)).toBe(true),
    );

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(burger.getAttribute("aria-expanded")).toBe("false");
    await waitFor(() => expect(document.activeElement).toBe(burger));
  });

  it("backdrop click dismisses the drawer", async () => {
    stubFetch();
    renderShell();
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Open navigation" }),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
    const backdrop = document.querySelector(".ff-drawer-backdrop");
    await user.click(backdrop as Element);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
