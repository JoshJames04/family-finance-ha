import { describe, expect, it } from "vitest";
import { groupOvertimeWeeks } from "../lib/overtimeWeeks";
import type { components } from "../api/schema";

type Entry = components["schemas"]["OvertimeOut"];
const entry = (id: number, work_date: string, extra: Partial<Entry> = {}): Entry => ({
  id, employment_id: 1, work_date, hours: "1.25", multiplier_bps: 15000,
  day_type: "weekday", status: "draft", hourly_rate_pence: 1000,
  pay_pence: 1875, notes: null, revision: "synthetic-revision", ...extra,
});

describe("overtime ISO weeks", () => {
  it("groups historical work dates across the ISO year boundary", () => {
    const groups = groupOvertimeWeeks([
      entry(1, "2025-12-29"), entry(2, "2026-01-04"), entry(3, "2026-01-05"),
    ]);
    expect(groups.map(g => [g.start, g.end, g.number, g.year])).toEqual([
      ["2026-01-05", "2026-01-11", 2, 2026],
      ["2025-12-29", "2026-01-04", 1, 2026],
    ]);
    expect(groups[1]!.entries.map(e => e.id)).toEqual([1, 2]);
  });

  it("keeps week 53 and the following year's first week distinct", () => {
    const groups = groupOvertimeWeeks([entry(1, "2021-01-03"), entry(2, "2021-01-04")]);
    expect(groups.map(g => [g.number, g.year])).toEqual([[1, 2021], [53, 2020]]);
  });

  it.each(["2026-03-29", "2026-10-25", "2024-02-29"])(
    "uses seven calendar days around DST/leap date %s", workDate => {
      const group = groupOvertimeWeeks([entry(1, workDate)])[0]!;
      const start = new Date(`${group.start}T00:00:00Z`);
      const end = new Date(`${group.end}T00:00:00Z`);
      expect(start.getUTCDay()).toBe(1);
      expect(end.getUTCDay()).toBe(0);
      expect((end.getTime() - start.getTime()) / 86_400_000).toBe(6);
    },
  );

  it("preserves multiple same-day entries and exact decimal hour/pay totals", () => {
    const rows = [entry(3, "2026-11-08", { hours: "0.10", pay_pence: 150, status: "approved" }),
      entry(2, "2026-11-02", { hours: "0.20", pay_pence: 300, status: "submitted" }),
      entry(1, "2026-11-02", { hours: "0.30", pay_pence: 450 })];
    const before = JSON.stringify(rows);
    const group = groupOvertimeWeeks(rows)[0]!;
    expect(group.entries.map(e => e.id)).toEqual([1, 2, 3]);
    expect(group.totalHours).toBe(0.6);
    expect(group.totalPayPence).toBe(900);
    expect(group.counts).toEqual({ draft: 1, submitted: 1, approved: 1, rejected: 0 });
    expect(JSON.stringify(rows)).toBe(before);
  });

  it("emits only populated weeks and handles empty history", () => {
    expect(groupOvertimeWeeks([])).toEqual([]);
    expect(groupOvertimeWeeks([entry(1, "2020-01-01"), entry(2, "2026-01-01")])).toHaveLength(2);
  });
});
