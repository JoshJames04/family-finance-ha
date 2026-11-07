import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import "./shell.css";

export const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/transactions", label: "Transactions" },
  { to: "/budget", label: "Budget" },
  { to: "/accounts", label: "Accounts" },
  { to: "/upcoming", label: "Upcoming" },
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
 */
export function AppShell() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <div className="ff-shell">
      <header className="ff-header">
        <button
          className="ff-burger"
          aria-label="Open navigation"
          aria-expanded={drawerOpen}
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
            className="ff-drawer"
            aria-label="Main"
            onClick={(e) => e.stopPropagation()}
          >
            <NavList onNavigate={() => setDrawerOpen(false)} />
          </nav>
        </div>
      )}

      <main className="ff-content">
        <Outlet />
      </main>

      <nav className="ff-tabs" aria-label="Main">
        <NavList />
      </nav>
    </div>
  );
}
