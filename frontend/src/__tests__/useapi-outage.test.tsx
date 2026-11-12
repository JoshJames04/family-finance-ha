import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { useApi, useHouseholds, useIdentity } from "../api/useApi";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); localStorage.clear(); });

it("recovers from an ingress outage longer than the initial retry burst", async () => {
  vi.useFakeTimers();
  let available = false;
  const fetcher = vi.fn(async () => {
    if (!available) throw new ApiError(503, "Service Unavailable");
    return { balance: 123 };
  });
  const { result } = renderHook(() => useApi(fetcher, true));
  await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
  expect(result.current.error).toContain("503");
  expect(result.current.data).toBeNull();
  available = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(35_000); });
  expect(result.current.data).toEqual({ balance: 123 });
  expect(result.current.error).toBeNull();
});

it("does not show the previous account while a new account request fails", async () => {
  vi.useFakeTimers();
  const { result, rerender } = renderHook(({ account }) => useApi(async () => {
    if (account === 1) return { account_id: 1, balance: 123 };
    throw new ApiError(404, "Not found");
  }, true, [account]), { initialProps: { account: 1 } });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(result.current.data?.account_id).toBe(1);
  rerender({ account: 2 });
  expect(result.current.data).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(result.current.data).toBeNull();
  expect(result.current.error).toContain("404");
});

it("rechecks a failed request when connectivity returns", async () => {
  vi.useFakeTimers();
  let available = false;
  const fetcher = vi.fn(async () => {
    if (!available) throw new TypeError("Network unavailable");
    return "restored";
  });
  const { result } = renderHook(() => useApi(fetcher, true));
  await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
  available = true;
  await act(async () => { window.dispatchEvent(new Event("online")); });
  expect(result.current.data).toBe("restored");
});

it("recovers household bootstrap after its initial startup window expires", async () => {
  vi.useFakeTimers();
  let available = false;
  vi.spyOn(api, "get").mockImplementation(async () => {
    if (!available) throw new ApiError(503, "Service Unavailable");
    return { items: [{ id: 1, name: "Synthetic" }] } as never;
  });
  const { result } = renderHook(() => useHouseholds());
  for (let n = 0; n < 9; n++) {
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
  }
  expect(result.current.error).toContain("503");
  expect(result.current.loading).toBe(false);
  available = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(35_000); });
  expect(result.current.data?.[0]?.id).toBe(1);
});

it("does not convert a failed identity request into an empty successful identity", async () => {
  vi.useFakeTimers();
  let available = false;
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/api/v1/households") {
      return { items: [{ id: 1, name: "Synthetic" }] } as never;
    }
    if (!available) throw new ApiError(503, "Service Unavailable");
    return { items: [{ id: 1, role: "admin", is_active: true }] } as never;
  });
  const { result } = renderHook(() => useIdentity());
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
  expect(result.current.data).toBeNull();
  expect(result.current.error).toContain("503");
  available = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(35_000); });
  expect(result.current.data?.[0]?.id).toBe(1);
});
