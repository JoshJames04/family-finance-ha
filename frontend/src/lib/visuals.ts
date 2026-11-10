/**
 * Category / movement visuals: a recognisable icon and a stable colour
 * for any free-text label. Keyword-matched first (so "Gas & Electric"
 * gets a bolt), then a deterministic hash picks the hue so the same
 * category is always the same colour everywhere.
 */
import {
  Banknote, Car, CircleDashed, Cloud, CreditCard, Droplet, Gift,
  Handshake, Home, Landmark, type LucideIcon, Music, PawPrint,
  PiggyBank, Plane, Receipt, Repeat, Shield, ShoppingBasket, Smartphone,
  Tv, Utensils, Wifi, Zap, Fuel, Baby, Dumbbell, HeartPulse, GraduationCap,
  Shirt, Coffee, Wrench, ArrowLeftRight, Users, Ship, Sparkles, Wallet,
} from "lucide-react";

const RULES: Array<[RegExp, LucideIcon]> = [
  [/^personal$|^current$|checking/i, Wallet],
  [/^joint$/i, Users],
  [/^credit$|credit_card/i, CreditCard],
  [/mortgage|rent\b|housing/i, Home],
  [/broadband|internet|wifi|fibre/i, Wifi],
  [/water/i, Droplet],
  [/gas|electric|energy|power/i, Zap],
  [/council|tax\b|hmrc/i, Landmark],
  [/insurance|aviva|cover|protect/i, Shield],
  [/loan|repay|owed/i, Handshake],
  [/contribution|household|family/i, Users],
  [/food|grocer|tesco|asda|aldi|lidl|sainsbury|shop\b|supermarket/i, ShoppingBasket],
  [/vet|pet|dog|cat\b|buddy/i, PawPrint],
  [/tv|sky\b|netflix|disney|prime video|now tv|licen[cs]e/i, Tv],
  [/music|spotify|apple music/i, Music],
  [/icloud|cloud|storage|google one|dropbox/i, Cloud],
  [/phone|mobile|o2\b|ee\b|vodafone|three\b/i, Smartphone],
  [/saving|isa\b|invest/i, PiggyBank],
  [/lunch|restaurant|takeaway|dining|eat/i, Utensils],
  [/coffee|cafe|costa|starbucks/i, Coffee],
  [/car\b|vehicle|mot\b|garage/i, Car],
  [/fuel|petrol|diesel/i, Fuel],
  [/holiday|travel|flight|hotel/i, Plane],
  [/credit card|ikano|amex|barclaycard|card\b/i, CreditCard],
  [/debt|currys|finance/i, Receipt],
  [/salary|pay\b|payday|wage|income|overtime/i, Banknote],
  [/gift|christmas|xmas|birthday/i, Gift],
  [/subscription|renewal|membership/i, Repeat],
  [/gym|fitness|sport/i, Dumbbell],
  [/health|dentist|doctor|pharmacy|optician/i, HeartPulse],
  [/school|nursery|child|kids/i, Baby],
  [/course|tuition|education|university/i, GraduationCap],
  [/cloth|fashion/i, Shirt],
  [/repair|maintenance|diy/i, Wrench],
  [/transfer/i, ArrowLeftRight],
  [/piecework|delivery|boat/i, Ship],
  [/other|misc|general/i, CircleDashed],
];

/** Tailwind-safe tile palettes (bg / fg) — static strings for the JIT. */
const PALETTE = [
  "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  "bg-indigo-500/15 text-indigo-600 dark:text-indigo-400",
  "bg-teal-500/15 text-teal-700 dark:text-teal-400",
  "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  "bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-400",
  "bg-lime-500/15 text-lime-700 dark:text-lime-400",
] as const;

/** Solid chart colours aligned with PALETTE order. */
export const CHART_COLOURS = [
  "#0ea5e9", "#8b5cf6", "#f59e0b", "#10b981", "#f43f5e",
  "#6366f1", "#14b8a6", "#f97316", "#d946ef", "#84cc16",
] as const;

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export function iconFor(label: string): LucideIcon {
  for (const [re, icon] of RULES) if (re.test(label)) return icon;
  return Sparkles;
}

export function toneFor(label: string): string {
  return PALETTE[hash(label.toLowerCase()) % PALETTE.length]!;
}

export function chartColourFor(label: string): string {
  return CHART_COLOURS[hash(label.toLowerCase()) % CHART_COLOURS.length]!;
}
