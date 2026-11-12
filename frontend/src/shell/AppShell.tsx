/**
 * Responsive shell.
 *  ≥1024px  fixed sidebar (grouped nav, search, theme) + slim top bar
 *  <1024px  top bar with menu button → slide-in navigation sheet
 *  <640px   adds a bottom tab bar for the daily destinations
 *
 * Accessibility (issue #127): skip-link to #ff-main; the navigation
 * sheet is a Radix modal dialog (Escape, focus trap, focus return);
 * every control shows the token focus ring.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { Bell, Menu, MoreHorizontal, Plus, Search, Wallet } from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "../components/Button";
import { Sheet } from "../components/Dialog";
import { useApi, useHouseholds } from "../api/useApi";
import { api } from "../api/client";
import { useReview } from "../api/useReview";
import { useTheme } from "../theme/useTheme";
import { CommandPalette } from "./CommandPalette";
import { ProfileChip } from "./ProfileChip";
import { QuickAdd } from "./QuickAdd";
import { ShellContext } from "./ShellContext";
import { ThemeToggle } from "./ThemeToggle";
import {
  MOBILE_TABS,
  NAV_GROUPS,
  NAV_ITEMS,
  SETTINGS_ITEM,
  navItemFor,
  type NavItem,
} from "./nav";

export { NAV_ITEMS };

function NavRow({
  item,
  badge,
  onNavigate,
}: {
  item: NavItem;
  badge?: number;
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          "ff-nav-item group flex h-8.5 items-center gap-2.5 rounded-lg px-2.5 text-[13.5px] transition-colors",
          isActive
            ? "active bg-surface-3 font-medium text-fg"
            : "text-fg-muted hover:bg-surface-2 hover:text-fg",
        )
      }
    >
      {({ isActive }) => (
        <>
          <Icon className={cn("size-4 shrink-0", isActive ? "text-accent" : "text-fg-subtle group-hover:text-fg-muted")} />
          <span className="flex-1 truncate">{item.label}</span>
          {badge ? (
            <span className="tnum grid h-5 min-w-5 place-items-center rounded-full bg-accent px-1.5 text-[11px] font-semibold text-accent-fg">
              {badge}
            </span>
          ) : null}
        </>
      )}
    </NavLink>
  );
}

function NavBody({
  reviewCount,
  onNavigate,
}: {
  reviewCount: number;
  onNavigate?: () => void;
}) {
  return (
    <div className="flex flex-col gap-5">
      {NAV_GROUPS.map((g) => (
        <div key={g.label} className="flex flex-col gap-0.5">
          <p className="px-2.5 pb-1 text-[11px] font-medium tracking-wider text-fg-subtle uppercase">
            {g.label}
          </p>
          {g.items.map((item) => (
            <NavRow
              key={item.to}
              item={item}
              badge={item.to === "/review" ? reviewCount : undefined}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function Brand({ household }: { household?: string }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <span className="bg-hero grid size-8 shrink-0 place-items-center rounded-[10px] text-white shadow-sm">
        <Wallet className="size-4" />
      </span>
      <div className="min-w-0 leading-tight">
        <p className="truncate text-[14px] font-semibold tracking-tight text-fg">Family Finance</p>
        {household && <p className="truncate text-[12px] text-fg-subtle">{household}</p>}
      </div>
    </div>
  );
}

function SearchButton({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-8.5 w-full items-center gap-2 rounded-lg border border-border bg-surface px-2.5 text-[13px] text-fg-subtle transition-colors hover:border-border-strong hover:text-fg-muted",
        className,
      )}
    >
      <Search className="size-4" />
      <span className="flex-1 text-left">Search…</span>
      <kbd className="rounded border border-border bg-surface-2 px-1.5 font-sans text-[10.5px]">Ctrl K</kbd>
    </button>
  );
}

export function AppShell() {
  useTheme();
  const location = useLocation();
  useEffect(() => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    const focus = () => {
      const element = document.getElementById(id);
      if (!element) return false;
      element.scrollIntoView?.({ block: "center" });
      element.tabIndex = -1;
      element.focus({ preventScroll: true });
      return true;
    };
    if (focus()) return;
    const observer = new MutationObserver(() => { if (focus()) observer.disconnect(); });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [location.pathname, location.search, location.hash]);
  const households = useHouseholds();
  const household = households.data?.[0]?.name;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const burgerRef = useRef<HTMLButtonElement>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [quickAddOpen, setQuickAddOpen] = useState(false);
  const [dataVersion, setDataVersion] = useState(0);
  const review = useReview(dataVersion);
  const version = useApi(() => api.get("/api/v1/version"), households.data !== null);
  const current = navItemFor(location.pathname);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    document.title = current ? `${current.label} · Family Finance` : "Family Finance";
  }, [current]);

  const ctx = useMemo(
    () => ({
      openQuickAdd: () => setQuickAddOpen(true),
      openCommand: () => setCommandOpen(true),
      dataVersion,
    }),
    [dataVersion],
  );
  const onSaved = useCallback(() => setDataVersion((v) => v + 1), []);
  const versionText = version.data ? String(version.data["version"] ?? "") : "";

  return (
    <ShellContext.Provider value={ctx}>
      <div className="ff-shell min-h-dvh bg-bg text-fg lg:pl-[248px]">
        <a
          className="ff-skip sr-only z-[60] rounded-lg bg-fg px-3 py-2 text-bg focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
          href="#ff-main"
        >
          Skip to content
        </a>

        {/* Desktop sidebar */}
        <aside className="ff-sidebar fixed inset-y-0 left-0 z-30 hidden w-[248px] flex-col border-r border-border bg-surface lg:flex">
          <div className="flex h-15 items-center px-4">
            <Brand household={household} />
          </div>
          <div className="px-3 pb-3">
            <SearchButton onClick={() => setCommandOpen(true)} />
          </div>
          <nav aria-label="Main" className="scrollbar-thin flex flex-1 flex-col overflow-y-auto px-3 pt-1 pb-4">
            <NavBody reviewCount={review.total} />
            <div className="mt-auto pt-4">
              <NavRow item={SETTINGS_ITEM} />
            </div>
          </nav>
          <div className="flex flex-col gap-2 border-t border-border p-3">
            <ProfileChip />
            <div className="flex items-center justify-between px-1">
              <span className="tnum text-[11.5px] text-fg-subtle">{versionText}</span>
              <ThemeToggle />
            </div>
          </div>
        </aside>

        {/* Top bar */}
        <header className="ff-header sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-border bg-bg/80 px-3 backdrop-blur-md sm:px-5">
          <Button
            ref={burgerRef}
            variant="ghost"
            size="icon"
            className="ff-burger lg:hidden"
            aria-label="Open navigation"
            aria-expanded={drawerOpen}
            aria-controls="ff-drawer"
            onClick={() => setDrawerOpen(true)}
          >
            <Menu />
          </Button>
          <div className="min-w-0 flex-1">
            <div className="lg:hidden"><Brand /></div>
            <nav aria-label="Breadcrumb" className="hidden items-center gap-2 text-[13.5px] lg:flex">
              <span className="text-fg-subtle">{household ?? "Household"}</span>
              <span className="text-fg-subtle">/</span>
              <span className="font-medium text-fg">{current?.label ?? "Dashboard"}</span>
            </nav>
          </div>
          <Button variant="ghost" size="icon" aria-label="Search" className="lg:hidden" onClick={() => setCommandOpen(true)}>
            <Search />
          </Button>
          <NavLink
            to="/review"
            aria-label={`Review — ${review.total} item${review.total === 1 ? "" : "s"} waiting`}
            className="relative grid size-9 place-items-center rounded-lg text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <Bell className="size-4" />
            {review.total > 0 && (
              <span className="absolute top-1.5 right-1.5 size-2 rounded-full bg-negative ring-2 ring-bg" />
            )}
          </NavLink>
          <Button variant="primary" size="md" onClick={() => setQuickAddOpen(true)} className="hidden sm:inline-flex">
            <Plus /> Add
          </Button>
          <Button variant="primary" size="icon" aria-label="Add transaction" onClick={() => setQuickAddOpen(true)} className="sm:hidden">
            <Plus />
          </Button>
        </header>

        {/* Navigation sheet (<1024px) */}
        <Sheet
          open={drawerOpen}
          onOpenChange={setDrawerOpen}
          title="Navigation"
          id="ff-drawer"
          side="left"
          hideTitle
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            burgerRef.current?.focus();
          }}
        >
          <div className="flex flex-col gap-4 p-3">
            <div className="flex items-center justify-between px-1 pt-1">
              <Brand household={household} />
            </div>
            <nav aria-label="Main">
              <NavBody reviewCount={review.total} onNavigate={() => setDrawerOpen(false)} />
              <div className="mt-5 border-t border-border pt-3">
                <NavRow item={SETTINGS_ITEM} onNavigate={() => setDrawerOpen(false)} />
              </div>
            </nav>
            <div className="flex items-center justify-between px-1">
              <span className="text-[12px] text-fg-subtle">Theme</span>
              <ThemeToggle />
            </div>
            <div className="border-t border-border pt-2">
              <ProfileChip />
            </div>
          </div>
        </Sheet>

        <main id="ff-main" tabIndex={-1} className="ff-content mx-auto w-full max-w-[1320px] px-3 pt-5 pb-28 outline-none sm:px-6 sm:pt-7 lg:pb-12">
          <Outlet />
        </main>

        {/* Bottom tabs (<640px) */}
        <nav
          aria-label="Quick navigation"
          className="ff-tabs fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md sm:hidden"
        >
          {MOBILE_TABS.map((to) => {
            const item = NAV_ITEMS.find((i) => i.to === to)!;
            const Icon = item.icon;
            return (
              <NavLink
                key={to}
                to={to}
                end={item.end}
                className={({ isActive }) =>
                  cn("flex h-15 flex-col items-center justify-center gap-1 text-[10.5px] font-medium",
                    isActive ? "text-accent" : "text-fg-subtle")
                }
              >
                <Icon className="size-5" />
                {item.label}
              </NavLink>
            );
          })}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            className="flex h-15 flex-col items-center justify-center gap-1 text-[10.5px] font-medium text-fg-subtle"
          >
            <MoreHorizontal className="size-5" />
            More
          </button>
        </nav>

        <Sheet open={moreOpen} onOpenChange={setMoreOpen} title="More" side="bottom">
          <div className="grid grid-cols-3 gap-2 p-3">
            {NAV_ITEMS.filter((i) => !MOBILE_TABS.includes(i.to)).map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={() => setMoreOpen(false)}
                  className="relative flex flex-col items-center gap-2 rounded-xl border border-border bg-surface-2 px-2 py-3.5 text-[12.5px] font-medium text-fg"
                >
                  <Icon className="size-5 text-accent" />
                  {item.label}
                  {item.to === "/review" && review.total > 0 && (
                    <span className="absolute top-2 right-2 grid h-4.5 min-w-4.5 place-items-center rounded-full bg-accent px-1 text-[10px] text-accent-fg">
                      {review.total}
                    </span>
                  )}
                </NavLink>
              );
            })}
          </div>
        </Sheet>

        <CommandPalette open={commandOpen} onOpenChange={setCommandOpen} onQuickAdd={() => setQuickAddOpen(true)} />
        <QuickAdd open={quickAddOpen} onOpenChange={setQuickAddOpen} onSaved={onSaved} />
        <Toaster
          position="bottom-right"
          toastOptions={{
            className: "!bg-surface !text-fg !border-border !rounded-xl !text-[13px]",
          }}
          offset={{ bottom: 88 }}
          mobileOffset={{ bottom: 88 }}
        />
      </div>
    </ShellContext.Provider>
  );
}
