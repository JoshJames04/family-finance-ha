import {
  formatPence,
  formatPenceParts,
  formatPenceSigned,
} from "../lib/format";
import { cn } from "../lib/cn";

/**
 * Money renderer — enforces the shared formatter and applies
 * negative styling semantically (class + sign, never colour alone).
 *
 * `tone`: "auto" colours income green and leaves outgoings neutral
 * (the bank-statement convention); "none" never colours.
 */
export function Money({
  pence,
  className = "",
  signed = false,
  tone = "none",
  mono = false,
}: {
  pence: number;
  className?: string;
  signed?: boolean;
  tone?: "none" | "auto" | "variance";
  mono?: boolean;
}) {
  const colour =
    tone === "auto"
      ? pence > 0 ? "text-positive" : ""
      : tone === "variance"
        ? pence < 0 ? "text-negative" : pence > 0 ? "text-positive" : "text-fg-subtle"
        : "";
  return (
    <span
      className={cn(
        "ff-money tnum whitespace-nowrap",
        pence < 0 && "ff-money--neg",
        mono && "font-mono text-[0.94em]",
        colour,
        className,
      )}
    >
      {signed ? formatPenceSigned(pence) : formatPence(pence)}
    </span>
  );
}

/** Hero figure: "£1,284" large with ".60" de-emphasised. */
export function HeroMoney({
  pence,
  className,
  fractionClassName,
}: {
  pence: number;
  className?: string;
  fractionClassName?: string;
}) {
  const { whole, fraction } = formatPenceParts(pence);
  return (
    <span className={cn("ff-money tnum font-semibold tracking-tight", className)}>
      {whole}
      <span className={cn("opacity-60", fractionClassName)}>{fraction}</span>
    </span>
  );
}
