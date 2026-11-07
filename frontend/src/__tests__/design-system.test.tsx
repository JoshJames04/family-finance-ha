/** Design-system tests for #122: formatter edge cases, component
 * states, and the gate that bans ad-hoc money/date formatting. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Badge } from "../components/Badge";
import { Card } from "../components/Card";
import { Money } from "../components/Money";
import {
  formatDate,
  formatDateTime,
  formatGBP,
  formatPence,
} from "../lib/format";

afterEach(cleanup);

describe("formatPence", () => {
  it("formats pence as GBP", () => {
    expect(formatPence(123456)).toBe("£1,234.56");
    expect(formatPence(0)).toBe("£0.00");
    expect(formatPence(5)).toBe("£0.05");
  });
  it("formats negatives with leading minus", () => {
    expect(formatPence(-4200)).toBe("-£42.00");
  });
  it("handles large amounts", () => {
    expect(formatPence(99999999)).toBe("£999,999.99");
  });
});

describe("formatGBP / dates", () => {
  it("formats major units", () => {
    expect(formatGBP(12.5)).toBe("£12.50");
  });
  it("formats ISO as dd/MM/yyyy", () => {
    expect(formatDate("2026-03-15T10:30:00Z")).toMatch(
      /^\d{2}\/\d{2}\/\d{4}$/,
    );
    expect(formatDateTime("2026-03-15T10:30:00Z")).toMatch(
      /^\d{2}\/\d{2}\/\d{4}, \d{2}:\d{2}$/,
    );
  });
  it("renders an em dash for unparseable input", () => {
    expect(formatDate("not-a-date")).toBe("—");
    expect(formatDateTime("")).toBe("—");
  });
});

describe("components", () => {
  it("Badge renders all tones", () => {
    for (const tone of [
      "neutral",
      "info",
      "success",
      "warning",
      "error",
    ] as const) {
      const { unmount } = render(<Badge tone={tone}>{tone}</Badge>);
      expect(screen.getByText(tone).className).toContain(
        `ff-badge--${tone}`,
      );
      unmount();
    }
  });
  it("Card renders title, body and actions", () => {
    render(
      <Card title="T" actions={<button>A</button>}>
        body
      </Card>,
    );
    expect(screen.getByText("T")).toBeTruthy();
    expect(screen.getByText("body")).toBeTruthy();
    expect(screen.getByRole("button", { name: "A" })).toBeTruthy();
  });
  it("Money formats and flags negatives", () => {
    const { container } = render(<Money pence={-100} />);
    const el = container.querySelector(".ff-money")!;
    expect(el.textContent).toBe("-£1.00");
    expect(el.className).toContain("ff-money--neg");
  });
});

describe("formatter gate", () => {
  it("no ad-hoc Intl/locale formatting outside lib/format", () => {
    // Vite-native source scan — no node fs/types needed.
    const sources = import.meta.glob("../**/*.{ts,tsx}", {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.endsWith("lib/format.ts"))
      .filter(([, text]) =>
        /Intl\.(NumberFormat|DateTimeFormat)|toLocaleString|toLocaleDateString|\.toFixed\(/.test(
          text,
        ),
      )
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });
});
