import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProfileChip } from "../shell/ProfileChip";

const PROFILES = {
  items: [
    { id: 1, display_name: "Josh", role: "admin", is_active: true },
    { id: 2, display_name: "Chloe", role: "member", is_active: true },
  ],
  total: 2, page: 1, size: 2,
};

function mockFetch() {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    let body: object = { items: [], total: 0 };
    if (url.includes("/households")) body = { items: [{ id: 1, name: "H" }] };
    if (url.includes("/profiles")) body = PROFILES;
    if (url.includes("/auth/me")) {
      body = { id: 2, display_name: "Chloe", role: "member", household_id: 1 };
    }
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  });
}

describe("ProfileChip", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("shows the supervisor-resolved identity with no switcher under ingress", async () => {
    window.history.pushState({}, "", "/api/hassio_ingress/t0k3n/");
    mockFetch();
    render(<ProfileChip />);
    // Resolved identity wins — localStorage is ignored entirely (#341).
    expect(await screen.findByLabelText("Signed in as Chloe"))
      .toBeTruthy();
    expect(screen.getByText("member")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("Switch member")).toBeNull();
  });

  it("keeps the switcher in standalone mode", async () => {
    window.history.pushState({}, "", "/");
    mockFetch();
    const user = userEvent.setup();
    render(<ProfileChip />);
    const chip = await screen.findByRole("button", { name: /switch member/i });
    await user.click(chip);
    expect(await screen.findByRole("button", { name: /Chloe/ })).toBeTruthy();
  });
});
