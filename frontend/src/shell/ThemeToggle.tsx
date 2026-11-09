import { Monitor, Moon, Sun } from "lucide-react";
import { setThemePref, useTheme, type ThemePref } from "../theme/useTheme";
import { cn } from "../lib/cn";

const OPTIONS: Array<{ value: ThemePref; label: string; icon: typeof Sun }> = [
  { value: "system", label: "System theme", icon: Monitor },
  { value: "light", label: "Light theme", icon: Sun },
  { value: "dark", label: "Dark theme", icon: Moon },
];

/** Three-way theme switch: system (default) / light / dark. */
export function ThemeToggle({ className }: { className?: string }) {
  const { pref } = useTheme();
  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className={cn("inline-flex items-center gap-0.5 rounded-lg border border-border bg-surface-2 p-0.5", className)}
    >
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={pref === value}
          aria-label={label}
          title={label}
          onClick={() => setThemePref(value)}
          className={cn(
            "grid h-6.5 w-7.5 place-items-center rounded-md transition-colors",
            pref === value ? "bg-surface text-fg shadow-sm" : "text-fg-subtle hover:text-fg",
          )}
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
