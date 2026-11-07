import { formatPence } from "../lib/format";

/**
 * Money renderer — enforces the shared formatter and applies
 * negative styling semantically (class + sign, never colour alone).
 */
export function Money({
  pence,
  className = "",
}: {
  pence: number;
  className?: string;
}) {
  const cls =
    `ff-money ${pence < 0 ? "ff-money--neg" : ""} ${className}`.trim();
  return <span className={cls}>{formatPence(pence)}</span>;
}
