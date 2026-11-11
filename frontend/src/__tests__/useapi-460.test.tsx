/** #460: useApi must distinguish the five widget states — a gated
 * request is `loading` (never false-empty), transient 5xx/network
 * failures retry with backoff then recover, stale data survives a
 * failed refresh, and refetch restarts a terminally-failed cycle. */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { useApi } from "../api/useApi";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 503 ? "Service Unavailable" : "Error",
    json: () => Promise.resolve(
      status >= 400
        ? { title: "Service Unavailable", status }
        : body),
  } as Response;
}

afterEach(() => vi.restoreAllMocks());

describe("useApi #460 state machine", () => {
  it("reports loading while gated (never looks empty)", () => {
    const { result } = renderHook(
      ({ ready }) => useApi(async () => ({ v: 1 }), ready, []),
      { initialProps: { ready: false } });
    // ready=false → pending: loading true, data null, no error —
    // a consumer must render the loading state, not an empty one.
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("recovers automatically from a single 503", async () => {
    const spy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(503, null))
      .mockResolvedValue(jsonResponse(200, { v: 1 }));
    const { result } = renderHook(
      () => useApi(async () => {
        const r = await fetch("/x");
        if (!r.ok) throw new ApiError(r.status, "Service Unavailable");
        return r.json();
      }, true, []));
    await waitFor(() => expect(result.current.data).toEqual({ v: 1 }),
      { timeout: 5000 });
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(spy).toHaveBeenCalledTimes(2); // retried exactly once
  });

  it("gives up after bounded retries and exposes refetch", async () => {
    const spy = vi.fn().mockRejectedValue(new TypeError("down"));
    const { result } = renderHook(() => useApi(spy, true, []));
    await waitFor(() => expect(result.current.error).toBe("down"),
      { timeout: 10_000 });
    expect(result.current.loading).toBe(false);
    expect(spy).toHaveBeenCalledTimes(4); // 1 + 3 retries, bounded
  });

  it("does not retry a 4xx validation error", async () => {
    const spy = vi.fn().mockRejectedValue(
      new ApiError(422, "Validation failed"));
    const { result } = renderHook(() => useApi(spy, true, []));
    await waitFor(() =>
      expect(result.current.error).toContain("422"));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("keeps stale data when a refresh fails terminally", async () => {
    const spy = vi.fn()
      .mockResolvedValueOnce({ v: "first" })
      .mockRejectedValue(new TypeError("gone"));
    const { result, rerender } = renderHook(
      ({ dep }) => useApi(spy, true, [dep]),
      { initialProps: { dep: 0 } });
    await waitFor(() =>
      expect(result.current.data).toEqual({ v: "first" }));
    rerender({ dep: 1 });   // refresh cycle — every attempt fails
    await waitFor(() => expect(result.current.error).toBe("gone"),
      { timeout: 10_000 });
    expect(result.current.data).toEqual({ v: "first" }); // stale kept
  });

  it("refetch restarts a terminally-failed cycle", async () => {
    const spy = vi.fn()
      .mockRejectedValueOnce(new TypeError("gone"))
      .mockRejectedValueOnce(new TypeError("gone"))
      .mockRejectedValueOnce(new TypeError("gone"))
      .mockRejectedValueOnce(new TypeError("gone"))
      .mockResolvedValue({ v: "back" });
    const { result } = renderHook(() => useApi(spy, true, []));
    await waitFor(() => expect(result.current.error).toBe("gone"),
      { timeout: 10_000 });
    result.current.refetch();
    await waitFor(() =>
      expect(result.current.data).toEqual({ v: "back" }),
      { timeout: 5000 });
    expect(result.current.error).toBeNull();
  });
});
