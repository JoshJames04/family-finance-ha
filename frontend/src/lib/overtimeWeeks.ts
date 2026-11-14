import type { components } from "../api/schema";

type Entry = components["schemas"]["OvertimeOut"];
type Status = Entry["status"];

export interface OvertimeWeek {
  start: string;
  end: string;
  number: number;
  year: number;
  entries: Entry[];
  totalHours: number;
  totalPayPence: number;
  counts: Record<Status, number>;
}

/** Calendar arithmetic stays in UTC: neither DST nor browser locale moves a work date. */
export function groupOvertimeWeeks(entries: Entry[]): OvertimeWeek[] {
  const groups = new Map<string, OvertimeWeek>();
  for (const entry of entries) {
    const monday = new Date(`${entry.work_date}T00:00:00Z`);
    monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() + 6) % 7);
    const start = monday.toISOString().slice(0, 10);
    let group = groups.get(start);
    if (!group) {
      const sunday = new Date(monday);
      sunday.setUTCDate(sunday.getUTCDate() + 6);
      const thursday = new Date(monday);
      thursday.setUTCDate(thursday.getUTCDate() + 3);
      const year = thursday.getUTCFullYear();
      const first = new Date(`${String(year).padStart(4, "0")}-01-01T00:00:00Z`);
      const number = Math.ceil(((thursday.getTime() - first.getTime()) / 86_400_000 + 1) / 7);
      group = { start, end: sunday.toISOString().slice(0, 10), number, year,
        entries: [], totalHours: 0, totalPayPence: 0,
        counts: { draft: 0, submitted: 0, approved: 0, rejected: 0 } };
      groups.set(start, group);
    }
    group.entries.push(entry);
    group.totalPayPence += entry.pay_pence;
    group.counts[entry.status]++;
  }
  for (const group of groups.values()) {
    group.entries.sort((a, b) => a.work_date.localeCompare(b.work_date) || a.id - b.id);
    group.totalHours = group.entries.reduce((sum, e) => sum + Math.round(Number(e.hours) * 100), 0) / 100;
  }
  return [...groups.values()].sort((a, b) => b.start.localeCompare(a.start));
}
