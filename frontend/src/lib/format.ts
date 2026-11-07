/**
 * Shared formatters — the ONLY sanctioned way to render money and
 * dates (issue #122). Anything rendered to the user goes through
 * here so GBP/en-GB conventions can't drift per-page.
 */

const gbp = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
});

const gbpWhole = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
  maximumFractionDigits: 0,
});

const gbpCompact = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Integer pence → "£1,234.56". Negative pence → "-£1,234.56". */
export function formatPence(pence: number): string {
  return gbp.format(pence / 100);
}

/** Major units (rare — prefer formatPence) → "£1,234.56". */
export function formatGBP(pounds: number): string {
  return gbp.format(pounds);
}

/** Pence → "+£12.00" / "-£12.00" — for flows where direction matters. */
export function formatPenceSigned(pence: number): string {
  return pence > 0 ? `+${gbp.format(pence / 100)}` : gbp.format(pence / 100);
}

/** Pence → rounded whole pounds "£1,285". */
export function formatPenceWhole(pence: number): string {
  return gbpWhole.format(Math.round(pence / 100));
}

/** Pence → "£1.2K" — chart axes and tight spaces only. */
export function formatPenceCompact(pence: number): string {
  return gbpCompact.format(pence / 100);
}

/** Pence split for hero figures: { whole: "£1,284", fraction: ".60" }. */
export function formatPenceParts(pence: number): {
  whole: string;
  fraction: string;
} {
  const full = gbp.format(pence / 100);
  const dot = full.lastIndexOf(".");
  return dot < 0
    ? { whole: full, fraction: "" }
    : { whole: full.slice(0, dot), fraction: full.slice(dot) };
}

/** Pounds string for an editable input: 12345 → "123.45". */
export function penceToInput(pence: number): string {
  const sign = pence < 0 ? "-" : "";
  const abs = Math.abs(pence);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/** 0.6234 → "62%"; 62.34 with {ratio:false} → "62%". */
export function formatPercent(value: number, opts: { ratio?: boolean } = {}): string {
  const n = opts.ratio === false ? value : value * 100;
  return `${Math.round(n)}%`;
}

/**
 * Parse an ISO date or datetime. Date-only strings ("2026-11-10") are
 * read as LOCAL midnight so they never shift a day in negative-offset
 * timezones.
 */
export function parseISODate(iso: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m
    ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    : new Date(iso);
}

/** Date → "2026-11-07" (local calendar date). */
export function toISODate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const dateFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const dateTimeFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/** ISO date/datetime string → "dd/MM/yyyy". Unparseable → "—". */
export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : dateFmt.format(d);
}

/** ISO datetime string → "dd/MM/yyyy HH:mm". Unparseable → "—". */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : dateTimeFmt.format(d);
}

const monthFmt = new Intl.DateTimeFormat("en-GB", {
  month: "long",
  year: "numeric",
});
const monthShortFmt = new Intl.DateTimeFormat("en-GB", { month: "short" });
const monthLongFmt = new Intl.DateTimeFormat("en-GB", { month: "long" });
const dayMonthFmt = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
});
const dayMonthYearFmt = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
});
const weekdayDayMonthFmt = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
});
const weekdayFmt = new Intl.DateTimeFormat("en-GB", { weekday: "long" });
const longDateFmt = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

function safe(iso: string, fmt: Intl.DateTimeFormat): string {
  const d = parseISODate(iso);
  return Number.isNaN(d.getTime()) ? "—" : fmt.format(d);
}

/** (year, month 0-based) → "March 2025" — calendar headings. */
export function formatMonth(year: number, month: number): string {
  return monthFmt.format(new Date(Date.UTC(year, month, 1)));
}

/** ISO → "December 2027". */
export function formatMonthOf(iso: string): string {
  return safe(iso, monthFmt);
}

/** ISO → "Dec". */
export function formatMonthShort(iso: string): string {
  return safe(iso, monthShortFmt);
}

/** Month index 0-11 → "January". */
export function formatMonthName(month: number): string {
  return monthLongFmt.format(new Date(2000, month, 1));
}

/** ISO → "7 Nov". */
export function formatDayMonth(iso: string): string {
  return safe(iso, dayMonthFmt);
}

/** ISO → "7 Nov 2026". */
export function formatDayMonthYear(iso: string): string {
  return safe(iso, dayMonthYearFmt);
}

/** ISO → "7 Nov" this year, "7 Nov 2025" otherwise. */
export function formatSmartDate(iso: string, now: Date = new Date()): string {
  const d = parseISODate(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.getFullYear() === now.getFullYear()
    ? dayMonthFmt.format(d)
    : dayMonthYearFmt.format(d);
}

/** ISO → "Mon 10 Nov". */
export function formatWeekdayDayMonth(iso: string): string {
  return safe(iso, weekdayDayMonthFmt);
}

/** ISO → weekday name ("Monday"). */
export function formatWeekday(iso: string): string {
  const d = parseISODate(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return weekdayFmt.format(d);
}

/** Date → "Saturday 7 November". */
export function formatLongDate(d: Date): string {
  return longDateFmt.format(d);
}

/** Whole days from today (local) to the ISO date; negative = past. */
export function daysUntil(iso: string, now: Date = new Date()): number {
  const target = parseISODate(iso);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(target.getFullYear(), target.getMonth(), target.getDate());
  return Math.round((day.getTime() - today.getTime()) / 86_400_000);
}

/** ISO → "today" / "tomorrow" / "in 3 days" / "yesterday" / "5 days ago". */
export function formatRelativeDays(iso: string, now: Date = new Date()): string {
  const n = daysUntil(iso, now);
  if (Number.isNaN(n)) return "—";
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}

const numFmt1 = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 });
const numFmt2 = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 });

/** Watt-hours → "12.3 kWh". */
export function formatKwh(wh: number): string {
  return `${numFmt1.format(wh / 1000)} kWh`;
}

/** Millipence/kWh → "24.50" — the page supplies the "p/kWh" unit. */
export function formatUnitRate(millipence: number): string {
  return numFmt2.format(millipence / 1000);
}

/** Bytes → "512 B" / "42 KB" / "3.1 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${numFmt1.format(bytes / 1024)} KB`;
  return `${numFmt1.format(bytes / 1024 / 1024)} MB`;
}
