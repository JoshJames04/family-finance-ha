/** ⌘K command palette: jump to any page, run global actions. */
import { Command } from "cmdk";
import { useNavigate } from "react-router-dom";
import { Monitor, Moon, Plus, Sun } from "lucide-react";
import { NAV_ITEMS } from "./nav";
import { setThemePref } from "../theme/useTheme";

export function CommandPalette({
  open,
  onOpenChange,
  onQuickAdd,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onQuickAdd: () => void;
}) {
  const navigate = useNavigate();
  const run = (fn: () => void) => {
    onOpenChange(false);
    fn();
  };
  const item =
    "flex h-10 cursor-pointer items-center gap-3 rounded-lg px-3 text-[13.5px] text-fg-muted " +
    "data-[selected=true]:bg-surface-2 data-[selected=true]:text-fg [&_svg]:size-4";
  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Command menu"
      overlayClassName="fixed inset-0 z-50 bg-[var(--overlay)] backdrop-blur-[2px]"
      contentClassName="fixed top-[14vh] left-1/2 z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"
    >
      <Command.Input
        placeholder="Search pages and actions…"
        className="h-13 w-full border-b border-border bg-transparent px-4 text-[15px] text-fg outline-none placeholder:text-fg-subtle"
      />
      <Command.List className="scrollbar-thin max-h-[min(60vh,420px)] overflow-y-auto p-2">
        <Command.Empty className="py-8 text-center text-[13px] text-fg-subtle">No results.</Command.Empty>
        <Command.Group heading="Actions" className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[11.5px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-fg-subtle">
          <Command.Item className={item} onSelect={() => run(onQuickAdd)}>
            <Plus /> Add transaction
          </Command.Item>
          <Command.Item className={item} onSelect={() => run(() => setThemePref("light"))}>
            <Sun /> Use light theme
          </Command.Item>
          <Command.Item className={item} onSelect={() => run(() => setThemePref("dark"))}>
            <Moon /> Use dark theme
          </Command.Item>
          <Command.Item className={item} onSelect={() => run(() => setThemePref("system"))}>
            <Monitor /> Follow system theme
          </Command.Item>
        </Command.Group>
        <Command.Group heading="Go to" className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[11.5px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-fg-subtle">
          {NAV_ITEMS.map(({ to, label, icon: Icon, description }) => (
            <Command.Item
              key={to}
              value={`${label} ${description}`}
              className={item}
              onSelect={() => run(() => navigate(to))}
            >
              <Icon />
              <span className="text-fg">{label}</span>
              <span className="truncate text-[12.5px] text-fg-subtle">{description}</span>
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
