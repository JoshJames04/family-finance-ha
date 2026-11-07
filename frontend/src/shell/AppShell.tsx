import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import "./shell.css";

export const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/transactions", label: "Transactions" },
  { to: "/budget", label: "Budget" },
  { to: "/accounts", label: "Accounts" },
  { to: "/upcoming", label: "Upcoming" },
  { to: "/gifts", label: "Gifts" },
] as const;

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={"end" in item && item.end}
          className={({ isActive }) =>
            `ff-nav-item${isActive ? " active" : ""}`
          }
          onClick={onNavigate}
        >
          {item.label}
        </NavLink>
      ))}
    </>
  );
}

/**
 * Responsive shell: sidebar ≥1024px, hamburger drawer 641–1023px,
 * bottom tab bar ≤640px. Breakpoints live in shell.css.
 *
 * Accessibility (issue #127): skip-link to #ff-main, drawer behaves
 * as a modal dialog (Escape closes, focus moves in on open and back
 * to the trigger on close), every interactive element shows a
 * :focus-visible ring from the theme tokens.
 */
export function AppShell() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const burgerRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!drawerOpen) return;
    const first = drawerRef.current?.querySelector<HTMLElement>("a");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      burgerRef.current?.focus();
    };
  }, [drawerOpen]);

  return (
    <div className="ff-shell">
      <a className="ff-skip" href="#ff-main">
        Skip to content
      </a>
      <header className="ff-header">
        <button
          ref={burgerRef}
          className="ff-burger"
          aria-label="Open navigation"
          aria-expanded={drawerOpen}
          aria-controls="ff-drawer"
          onClick={() => setDrawerOpen(true)}
        >
          ☰
        </button>
        <span className="ff-title">Family Finance</span>
      </header>

      <aside className="ff-sidebar">
        <nav aria-label="Main">
          <NavList />
        </nav>
      </aside>

      {drawerOpen && (
        <div
          className="ff-drawer-backdrop"
          onClick={() => setDrawerOpen(false)}
        >
          <nav
            id="ff-drawer"
            ref={drawerRef}
            className="ff-drawer"
            aria-label="Main"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <NavList onNavigate={() => setDrawerOpen(false)} />
          </nav>
        </div>
      )}

      <main className="ff-content" id="ff-main" tabIndex={-1}>
        <Outlet />
      </main>

      <nav className="ff-tabs" aria-label="Main">
        <NavList />
      </nav>
    </div>
  );
}
