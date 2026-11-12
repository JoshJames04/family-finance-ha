import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { setThemePref, useTheme } from "../theme/useTheme";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); localStorage.clear(); });

it("follows explicit HA theme changes while preserving a manual app override", () => {
  vi.useFakeTimers();
  const ha = { hass: { themes: { darkMode: true } } };
  const original = document.querySelector.bind(document);
  vi.spyOn(document, "querySelector").mockImplementation((selector) =>
    selector === "home-assistant" ? ha as unknown as Element : original(selector));
  const { result } = renderHook(() => useTheme());
  expect(result.current.scheme).toBe("dark");
  ha.hass.themes.darkMode = false;
  act(() => { vi.advanceTimersByTime(2_000); });
  expect(result.current.scheme).toBe("light");
  act(() => { setThemePref("dark"); });
  expect(result.current.scheme).toBe("dark");
});
