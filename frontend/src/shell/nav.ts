import {
  ArrowLeftRight, BarChart3, Briefcase, CalendarClock, Gift, Handshake,
  Inbox, Landmark, LayoutDashboard, LineChart, type LucideIcon, PiggyBank,
  Repeat, Settings, Target, Zap,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  description: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/** Single-segment routes on purpose: the API client resolves requests
 * relative to the page URL (ingress-safe), so nested paths would
 * shift the base. Page-level tabs use ?query state instead. */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Overview",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, description: "Your money at a glance" },
      { to: "/transactions", label: "Transactions", icon: ArrowLeftRight, description: "Search, categorise and reconcile" },
      { to: "/budget", label: "Budget", icon: Target, description: "Plan vs actual by month" },
      { to: "/forecast", label: "Forecast", icon: LineChart, description: "Projected balance and calendar" },
      { to: "/review", label: "Review", icon: Inbox, description: "Items that need a decision" },
    ],
  },
  {
    label: "Planning",
    items: [
      { to: "/upcoming", label: "Upcoming", icon: CalendarClock, description: "Bills, transfers and paydays ahead" },
      { to: "/subscriptions", label: "Subscriptions", icon: Repeat, description: "Recurring services and renewals" },
      { to: "/goals", label: "Goals", icon: PiggyBank, description: "Savings goals and progress" },
      { to: "/gifts", label: "Gifts", icon: Gift, description: "Seasonal gift planning" },
      { to: "/loans", label: "Loans", icon: Handshake, description: "Family loans and repayments" },
    ],
  },
  {
    label: "Household",
    items: [
      { to: "/accounts", label: "Accounts", icon: Landmark, description: "Balances and account health" },
      { to: "/salary", label: "Salary", icon: Briefcase, description: "Payroll, payslips and overtime" },
      { to: "/energy", label: "Energy", icon: Zap, description: "Usage, tariffs and cost" },
      { to: "/reports", label: "Reports", icon: BarChart3, description: "Monthly and annual summaries" },
    ],
  },
];

export const SETTINGS_ITEM: NavItem = {
  to: "/settings", label: "Settings", icon: Settings, description: "Profiles, categories and system",
};

/** Flat list — every routed destination. */
export const NAV_ITEMS: NavItem[] = [
  ...NAV_GROUPS.flatMap((g) => g.items),
  SETTINGS_ITEM,
];

/** Bottom tab bar on phones: the four daily destinations + More. */
export const MOBILE_TABS = ["/", "/transactions", "/budget", "/forecast"];

export function navItemFor(pathname: string): NavItem | undefined {
  const seg = `/${pathname.split("/").filter(Boolean)[0] ?? ""}`;
  return NAV_ITEMS.find((i) => i.to === seg);
}
