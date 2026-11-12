/**
 * Form controls. Selects stay native (styled) on purpose: they give
 * the platform picker on phones, are fully keyboard/AT accessible and
 * need no portal — the right trade for a data-entry app.
 */
import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { ChevronDown, Search } from "lucide-react";
import { cn } from "../lib/cn";

const control =
  "w-full rounded-lg border border-border bg-surface text-fg placeholder:text-fg-subtle " +
  "transition-[border-color,box-shadow] duration-150 " +
  "hover:border-border-strong focus:border-accent focus:outline-none focus:ring-3 focus:ring-accent/20 " +
  "disabled:cursor-not-allowed disabled:opacity-50";

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { sizeVariant?: "sm" | "md" }
>(({ className, sizeVariant = "md", ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      control,
      sizeVariant === "sm" ? "h-8 px-2.5 text-[13px]" : "h-9 px-3 text-sm",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(control, "min-h-20 px-3 py-2 text-sm", className)}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & { sizeVariant?: "sm" | "md"; wrapperClassName?: string }
>(({ className, wrapperClassName, sizeVariant = "md", children, ...props }, ref) => (
  <div className={cn("relative min-w-0", wrapperClassName)}>
    <select
      ref={ref}
      className={cn(
        control,
        "native-select appearance-none truncate pr-8",
        sizeVariant === "sm" ? "h-8 pl-2.5 text-[13px]" : "h-9 pl-3 text-sm",
        className,
      )}
      {...props}
    >
      {children}
    </select>
    <ChevronDown
      aria-hidden
      className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-fg-subtle"
    />
  </div>
));
Select.displayName = "Select";

export const SearchInput = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { wrapperClassName?: string }
>(({ className, wrapperClassName, ...props }, ref) => (
  <div className={cn("relative min-w-0", wrapperClassName)}>
    <Search
      aria-hidden
      className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-subtle"
    />
    <input
      ref={ref}
      type="search"
      className={cn(control, "h-9 pr-3 pl-9 text-sm", className)}
      {...props}
    />
  </div>
));
SearchInput.displayName = "SearchInput";

export function Checkbox({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      type="checkbox"
      className={cn(
        "size-4 shrink-0 cursor-pointer rounded border-border-strong accent-[var(--accent)]",
        className,
      )}
      {...props}
    />
  );
}

/** Label + control + hint/error, wired by id for AT. */
export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: (id: string) => ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-[13px] font-medium text-fg">
        {label}
      </label>
      {children(id)}
      {error ? (
        <p role="alert" className="text-[12.5px] text-negative">{error}</p>
      ) : hint ? (
        <p className="text-[12.5px] text-fg-subtle">{hint}</p>
      ) : null}
    </div>
  );
}
