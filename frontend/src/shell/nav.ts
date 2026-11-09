import {
  ArrowLeftRight, BarChart3, Bell, Briefcase, Building2, CalendarClock,
  Clock, FlaskConical, Gift, Handshake, Inbox, Landmark, LayoutDashboard,
  LineChart, type LucideIcon, PiggyBank, ReceiptText, Repeat, Settings,
  Tag, Target, Users2, Wand2, Zap,
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
      { to: "/review", label: "Review", icon: Inbox, description: "Items that need a decision" },
      { to: "/upcoming", label: "Upcoming", icon: CalendarClock, description: "Bills, transfers and paydays ahead" },
      { to: "/forecast", label: "Forecast", icon: LineChart, description: "Projected balance and calendar" },
      { to: "/reports", label: "Reports", icon: BarChart3, description: "Monthly and annual summaries" },
      { to: "/notifications", label: "Notifications", icon: Bell, description: "Alerts and their rules" },
      { to: "/energy", label: "Energy", icon: Zap, description: "Usage, tariffs and cost" },
    ],
  },
  {
    label: "Planning",
    items: [
      { to: "/budget", label: "Budget", icon: Target, description: "Plan vs actual — incl. the shared pot" },
      { to: "/shared", label: "Shared", icon: Users2, description: "Who pays what into the shared pot" },
      { to: "/bills", label: "Bills", icon: ReceiptText, description: "Things you pay on a schedule" },
      { to: "/subscriptions", label: "Subscriptions", icon: Repeat, description: "Recurring services and renewals" },
      { to: "/goals", label: "Goals", icon: PiggyBank, description: "Savings goals and progress" },
      { to: "/gifts", label: "Gifts", icon: Gift, description: "Seasonal gift planning" },
      { to: "/loans", label: "Loans", icon: Handshake, description: "Family loans and repayments" },
      { to: "/scenarios", label: "Scenarios", icon: FlaskConical, description: "What-if planning" },
    ],
  },
  {
    label: "Setup",
    items: [
      { to: "/salary", label: "Salary", icon: Briefcase, description: "Pay, deductions and payslips" },
      { to: "/overtime", label: "Overtime", icon: Clock, description: "Log and approve extra hours" },
      { to: "/accounts", label: "Accounts", icon: Landmark, description: "Balances and account health" },
      { to: "/banking", label: "Banking", icon: Building2, description: "Bank connections and sync" },
      { to: "/payees", label: "Payees", icon: Tag, description: "Canonical merchant names" },
      { to: "/rules", label: "Rules", icon: Wand2, description: "Automatic categorisation" },
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
