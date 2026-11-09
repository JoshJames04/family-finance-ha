import { describe, expect, it } from "vitest";
import { ingressBasename } from "../ingress";

describe("ingressBasename", () => {
  it("uses the ingress prefix when mounted under HA ingress", () => {
    window.history.pushState(
      {}, "", "/api/hassio_ingress/t0k3n/");
    expect(ingressBasename()).toBe("/api/hassio_ingress/t0k3n");
  });

  it("keeps the prefix on deep routes", () => {
    window.history.pushState(
      {}, "", "/api/hassio_ingress/t0k3n/settings");
    expect(ingressBasename()).toBe("/api/hassio_ingress/t0k3n");
  });

  it("falls back to BASE_URL for standalone", () => {
    window.history.pushState({}, "", "/settings");
    expect(ingressBasename()).toBe(import.meta.env.BASE_URL);
  });
});
