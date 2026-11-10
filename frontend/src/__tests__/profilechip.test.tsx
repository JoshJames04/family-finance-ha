import { cleanup, render, renderHook, screen, waitFor }
  from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { useMeProfileId } from "../api/useApi";
import { ProfileChip } from "../shell/ProfileChip";

const PROFILES = {
  items: [
    { id: 1, display_name: "Alice", role: "admin", is_active: true },
    { id: 2, display_name: "Carol", role: "member", is_active: true },
  ],
  total: 2, page: 1, size: 2,
};

const sentHeaders: Record<string, string>[] = [];

function mockFetch() {
  sentHeaders.length = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    sentHeaders.push((init?.headers ?? {}) as Record<string, string>);
    const url = String(input);
    let body: object = { items: [], total: 0 };
    if (url.includes("/households")) body = { items: [{ id: 1, name: "H" }] };
    if (url.includes("/profiles")) body = PROFILES;
    if (url.includes("/auth/me")) {
      body = { id: 2, display_name: "Carol", role: "member", household_id: 1 };
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
    expect(await screen.findByLabelText("Signed in as Carol"))
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
    expect(await screen.findByRole("button", { name: /Carol/ })).toBeTruthy();
  });
});

describe("ingress identity isolation (#341)", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("never sends X-Profile-Id under ingress, even with a stale id", async () => {
    window.history.pushState({}, "", "/api/hassio_ingress/t0k3n/");
    localStorage.setItem("ff.profileId", "1");
    mockFetch();
    await api.get("/api/v1/households");
    expect(sentHeaders[0]!["X-Profile-Id"]).toBeUndefined();
    // And the stored id survives untouched — nothing under ingress
    // may read it as "me", but standalone sessions keep their choice.
    expect(localStorage.getItem("ff.profileId")).toBe("1");
  });

  it("sends X-Profile-Id in standalone mode", async () => {
    window.history.pushState({}, "", "/");
    localStorage.setItem("ff.profileId", "1");
    mockFetch();
    await api.get("/api/v1/households");
    expect(sentHeaders[0]!["X-Profile-Id"]).toBe("1");
  });

  it("useMeProfileId resolves the supervisor identity under ingress", async () => {
    window.history.pushState({}, "", "/api/hassio_ingress/t0k3n/");
    // Stale id pointing at a member that no longer exists — under
    // ingress nothing may "repair" or read it.
    localStorage.setItem("ff.profileId", "99");
    mockFetch();
    const { result } = renderHook(() => useMeProfileId());
    await waitFor(() => expect(result.current).toBe(2));
    expect(localStorage.getItem("ff.profileId")).toBe("99");
  });

  it("useMeProfileId reads the stored choice in standalone mode", async () => {
    window.history.pushState({}, "", "/");
    localStorage.setItem("ff.profileId", "2");
    mockFetch();
    const { result } = renderHook(() => useMeProfileId());
    await waitFor(() => expect(result.current).toBe(2));
  });
});
