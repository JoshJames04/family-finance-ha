/**
 * Shared formatters — the ONLY sanctioned way to render money and
 * dates (issue #122). Anything rendered to the user goes through
 * here so GBP/en-GB conventions can't drift per-page.
 */

const gbp = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
});

/** Integer pence → "£1,234.56". Negative pence → "-£1,234.56". */
export function formatPence(pence: number): string {
  return gbp.format(pence / 100);
}

/** Major units (rare — prefer formatPence) → "£1,234.56". */
export function formatGBP(pounds: number): string {
  return gbp.format(pounds);
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
