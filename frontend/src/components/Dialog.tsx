/**
 * Modal dialog + side sheet on Radix Dialog: focus trap, Escape,
 * outside-click dismissal, scroll lock and focus return come from the
 * primitive — the accessibility contract of issue #127 for free.
 */
import { useRef, type ReactNode } from "react";
import { Dialog as D } from "radix-ui";
import { X } from "lucide-react";
import { cn } from "../lib/cn";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  const opener = useRef<HTMLElement | null>(null);
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="ff-overlay fixed inset-0 z-50 bg-[var(--overlay)] backdrop-blur-[2px]" />
        <D.Content
          onOpenAutoFocus={() => {
            const active = document.activeElement;
            opener.current = active instanceof HTMLElement && active !== document.body
              ? active : null;
          }}
          onCloseAutoFocus={(event) => {
            if (opener.current?.isConnected) {
              event.preventDefault();
              opener.current.focus();
            }
          }}
          className={cn(
            "fixed top-1/2 left-1/2 z-50 flex max-h-[min(90vh,720px)] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col",
            "rounded-2xl border border-border bg-surface shadow-2xl",
            className,
          )}
        >
          <div className="flex items-start gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0 flex-1">
              <D.Title className="text-[16px] font-semibold tracking-tight">{title}</D.Title>
              {description ? (
                <D.Description className="mt-1 text-[13px] text-fg-muted">
                  {description}
                </D.Description>
              ) : (
                <D.Description className="sr-only">{title}</D.Description>
              )}
            </div>
            <D.Close
              aria-label="Close"
              className="-mr-1.5 grid min-h-11 min-w-11 place-items-center rounded-md p-1.5 text-fg-subtle hover:bg-surface-2 hover:text-fg"
            >
              <X className="size-4" />
            </D.Close>
          </div>
          <div className="scrollbar-thin overflow-y-auto px-5 py-4">{children}</div>
          {footer && (
            <div className="flex justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>
          )}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

export function Sheet({
  open,
  onOpenChange,
  title,
  side = "left",
  children,
  id,
  className,
  hideTitle = false,
  onCloseAutoFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  side?: "left" | "right" | "bottom";
  children: ReactNode;
  id?: string;
  className?: string;
  hideTitle?: boolean;
  /** Radix close-autofocus hook — override where focus lands on close. */
  onCloseAutoFocus?: (e: Event) => void;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="ff-drawer-backdrop fixed inset-0 z-50 bg-[var(--overlay)] backdrop-blur-[2px]" />
        <D.Content
          id={id}
          aria-label={title}
          onCloseAutoFocus={onCloseAutoFocus}
          className={cn(
            "fixed z-50 flex flex-col border-border bg-surface shadow-2xl",
            side === "left" && "inset-y-0 left-0 w-[min(300px,85vw)] border-r",
            side === "right" && "inset-y-0 right-0 w-[min(440px,92vw)] border-l",
            side === "bottom" && "inset-x-0 bottom-0 max-h-[85vh] rounded-t-2xl border-t",
            className,
          )}
        >
          <div className={cn("flex items-center gap-3 border-b border-border px-4 py-3", hideTitle && "sr-only")}>
            <D.Title className="flex-1 text-[15px] font-semibold">{title}</D.Title>
            <D.Description className="sr-only">{title}</D.Description>
            <D.Close
              aria-label="Close"
              className="rounded-md p-1.5 text-fg-subtle hover:bg-surface-2 hover:text-fg"
            >
              <X className="size-4" />
            </D.Close>
          </div>
          <div className="scrollbar-thin flex-1 overflow-y-auto">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
