import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../lib/cn";

/**
 * Surface panel — the standard container. Header (title, description,
 * actions) is separated by a hairline; `flush` drops body padding for
 * tables and lists that run edge-to-edge.
 */
export function Panel({
  title,
  description,
  actions,
  footer,
  flush = false,
  className,
  bodyClassName,
  children,
  headingLevel = 2,
  ...rest
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  flush?: boolean;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
  headingLevel?: 2 | 3;
} & Omit<HTMLAttributes<HTMLElement>, "title">) {
  const H = headingLevel === 2 ? "h2" : "h3";
  return (
    <section
      className={cn(
        "flex flex-col rounded-[var(--radius-card)] border border-border bg-surface shadow-card min-w-0",
        className,
      )}
      {...rest}
    >
      {(title || actions) && (
        <header className="flex min-h-12 items-center gap-3 border-b border-border px-4 py-2.5">
          <div className="min-w-0 flex-1">
            {title && (
              <H className="truncate text-[14px] font-semibold tracking-tight text-fg">
                {title}
              </H>
            )}
            {description && (
              <p className="truncate text-[12.5px] text-fg-subtle">{description}</p>
            )}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cn("min-w-0 flex-1", !flush && "p-4", bodyClassName)}>{children}</div>
      {footer && (
        <footer className="border-t border-border px-4 py-2.5 text-[13px] text-fg-muted">
          {footer}
        </footer>
      )}
    </section>
  );
}

/** Back-compat name used by the catalogue and older call sites. */
export const Card = ({
  title,
  actions,
  children,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) => (
  <Panel title={title} actions={actions} headingLevel={3}>
    {children}
  </Panel>
);
