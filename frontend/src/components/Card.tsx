import type { ReactNode } from "react";

/** Surface card — consistent radius/shadow/padding for panels. */
export function Card({
  title,
  children,
  actions,
}: {
  title?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="ff-card">
      {(title || actions) && (
        <header className="ff-card__header">
          {title && <h3 className="ff-card__title">{title}</h3>}
          {actions && <div className="ff-card__actions">{actions}</div>}
        </header>
      )}
      <div className="ff-card__body">{children}</div>
    </section>
  );
}
