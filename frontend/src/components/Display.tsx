/** Display primitives: stat tiles, progress, icon tiles, segmented
 * control, page header and table parts. */
import type {
  HTMLAttributes,
  ReactNode,
  TdHTMLAttributes,
  ThHTMLAttributes,
} from "react";
import type { LucideIcon } from "lucide-react";
import { TrendingDown, TrendingUp } from "lucide-react";
import { cn } from "../lib/cn";
import { iconFor, toneFor } from "../lib/visuals";

/** Coloured rounded icon tile — category / movement identity. */
export function IconTile({
  icon,
  label,
  size = "md",
  className,
}: {
  icon?: LucideIcon;
  /** Free-text label used to derive icon + colour when not given. */
  label?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const Icon = icon ?? iconFor(label ?? "");
  return (
    <span
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center",
        size === "sm" && "size-7 rounded-lg [&_svg]:size-3.5",
        size === "md" && "size-9 rounded-xl [&_svg]:size-4",
        size === "lg" && "size-11 rounded-2xl [&_svg]:size-5",
        toneFor(label ?? ""),
        className,
      )}
    >
      <Icon />
    </span>
  );
}

/** KPI tile: label, figure, optional delta / hint / icon. */
export function Stat({
  label,
  value,
  hint,
  delta,
  deltaGoodWhen = "up",
  icon: Icon,
  iconClassName,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  /** Fractional change, e.g. 0.042 for +4.2%. */
  delta?: number | null;
  deltaGoodWhen?: "up" | "down";
  icon?: LucideIcon;
  iconClassName?: string;
  className?: string;
}) {
  const good = delta == null ? null : (delta >= 0) === (deltaGoodWhen === "up");
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <div className="flex items-center gap-2 text-[13px] text-fg-muted">
        {Icon && (
          <span className={cn("grid size-6 place-items-center rounded-md [&_svg]:size-3.5", iconClassName)}>
            <Icon />
          </span>
        )}
        <span className="truncate">{label}</span>
      </div>
      <div className="tnum truncate text-[22px] font-semibold tracking-tight text-fg">{value}</div>
      {(delta != null || hint) && (
        <div className="flex items-center gap-1.5 text-[12.5px] text-fg-subtle">
          {delta != null && (
            <span className={cn("inline-flex items-center gap-0.5 font-medium", good ? "text-positive" : "text-negative")}>
              {delta >= 0 ? <TrendingUp className="size-3.5" /> : <TrendingDown className="size-3.5" />}
              {Math.abs(Math.round(delta * 1000) / 10)}%
            </span>
          )}
          {hint && <span className="truncate">{hint}</span>}
        </div>
      )}
    </div>
  );
}

/** Horizontal bar. Tone auto-escalates: ≥90% amber, >100% red. */
export function Progress({
  value,
  max,
  tone = "auto",
  className,
  label,
}: {
  value: number;
  max: number;
  tone?: "auto" | "accent" | "neutral" | "positive";
  className?: string;
  label?: string;
}) {
  const ratio = max > 0 ? value / max : value > 0 ? 1.01 : 0;
  const pct = Math.max(0, Math.min(100, ratio * 100));
  const bar =
    tone === "auto"
      ? ratio > 1 ? "bg-negative" : ratio >= 0.9 ? "bg-warning" : "bg-accent"
      : tone === "accent" ? "bg-accent"
        : tone === "positive" ? "bg-positive" : "bg-fg-subtle";
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(ratio * 100)}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-3", className)}
    >
      <div className={cn("h-full rounded-full transition-[width] duration-500", bar)} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Ring gauge for a single ratio (budget used). */
export function Ring({
  ratio,
  size = 120,
  stroke = 10,
  children,
}: {
  ratio: number;
  size?: number;
  stroke?: number;
  children?: ReactNode;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, ratio));
  const colour = ratio > 1 ? "var(--negative)" : ratio >= 0.9 ? "var(--warning)" : "var(--positive)";
  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeOpacity={clamped > 0 ? 1 : 0}
          stroke={colour}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${clamped * c} ${c}`}
          className="transition-[stroke-dasharray] duration-700"
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">{children}</div>
    </div>
  );
}

/** Segmented control (single choice) — radio-group semantics. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  options: ReadonlyArray<{ value: T; label: ReactNode }>;
  label: string;
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn("inline-flex items-center gap-0.5 rounded-lg border border-border bg-surface-2 p-0.5", className)}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-7 rounded-md px-2.5 text-[12.5px] font-medium whitespace-nowrap transition-colors",
            value === o.value
              ? "bg-surface text-fg shadow-sm"
              : "text-fg-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Page title row: heading, subtitle and actions. */
export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end gap-x-4 gap-y-3">
      <div className="min-w-0 flex-1">
        {eyebrow && <p className="mb-0.5 text-[13px] font-medium text-fg-subtle">{eyebrow}</p>}
        <h1 className="text-[22px] font-semibold tracking-tight text-fg sm:text-[24px]">{title}</h1>
        {description && <p className="mt-0.5 text-[13.5px] text-fg-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ---------- table ---------- */

export function Table({ className, ...p }: HTMLAttributes<HTMLTableElement>) {
  return <table className={cn("ff-table w-full border-collapse text-[13.5px]", className)} {...p} />;
}
export function THead({ className, ...p }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead
      className={cn("sticky top-0 z-[1] bg-surface text-left text-[12px] font-medium text-fg-subtle", className)}
      {...p}
    />
  );
}
export function TH({ className, numeric, ...p }: ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return (
    <th
      className={cn("h-9 border-b border-border px-3 font-medium whitespace-nowrap first:pl-4 last:pr-4", numeric && "text-right", className)}
      {...p}
    />
  );
}
export function TR({ className, ...p }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("border-b border-border last:border-0 hover:bg-surface-2/60", className)} {...p} />;
}
export function TD({ className, numeric, ...p }: TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return (
    <td
      className={cn("h-11 px-3 align-middle first:pl-4 last:pr-4", numeric && "tnum text-right whitespace-nowrap", className)}
      {...p}
    />
  );
}
