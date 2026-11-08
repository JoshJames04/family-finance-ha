/**
 * Who you're acting as. Standalone mode resolves member identity from
 * the persisted X-Profile-Id header; switching reloads the app so every
 * scoped query refetches under the new identity. Under ingress the row
 * is display-only — HA supplies identity and the header is ignored.
 */
import { useState } from "react";
import { Check, ChevronDown, ShieldCheck } from "lucide-react";
import { getProfileId, setProfileId } from "../api/client";
import { useIdentity } from "../api/useApi";
import { Sheet } from "../components/Dialog";
import { Badge } from "../components/Badge";
import { cn } from "../lib/cn";
import { toneFor } from "../lib/visuals";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

function Avatar({ name, size = "sm" }: { name: string; size?: "sm" | "md" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center font-semibold",
        size === "sm" ? "size-7 rounded-lg text-[11px]" : "size-9 rounded-xl text-[12.5px]",
        toneFor(name),
      )}
    >
      {initials(name) || "?"}
    </span>
  );
}

export function ProfileChip() {
  const identity = useIdentity();
  const [open, setOpen] = useState(false);
  const currentId = getProfileId();
  const profiles = (identity.data ?? []).filter((p) => p.is_active);
  const me = profiles.find((p) => p.id === currentId) ?? profiles[0];
  if (profiles.length === 0) return null;

  const switchTo = (id: number) => {
    if (id === currentId) {
      setOpen(false);
      return;
    }
    setProfileId(id);
    // Full reload: nothing scoped to the previous identity may linger.
    window.location.reload();
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex w-full items-center gap-2.5 rounded-lg px-1.5 py-1.5 text-left transition-colors hover:bg-surface-2"
        aria-label={`Signed in as ${me?.display_name ?? "unknown"} — switch member`}
      >
        <Avatar name={me?.display_name ?? "?"} />
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block truncate text-[13px] font-medium text-fg">
            {me?.display_name ?? "Unknown"}
          </span>
          <span className="block text-[11.5px] capitalize text-fg-subtle">
            {me?.role ?? "member"}
          </span>
        </span>
        <ChevronDown className="size-3.5 shrink-0 text-fg-subtle" />
      </button>

      <Sheet open={open} onOpenChange={setOpen} title="Switch member" side="bottom">
        <div className="flex flex-col gap-1 p-3">
          <p className="px-1 pb-2 text-[12.5px] text-fg-subtle">
            You're browsing as a household member. Switching reloads the
            app so permissions and private items match that member.
          </p>
          {profiles.map((p) => {
            const isMe = p.id === me?.id;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => switchTo(p.id)}
                className={cn(
                  "flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors",
                  isMe
                    ? "border-accent/40 bg-accent-soft"
                    : "border-border hover:bg-surface-2",
                )}
              >
                <Avatar name={p.display_name} size="md" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px] font-medium text-fg">
                    {p.display_name}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5">
                    {p.role === "admin" ? (
                      <Badge tone="accent" dot={false}>
                        <ShieldCheck className="size-3" /> Admin
                      </Badge>
                    ) : (
                      <Badge tone="neutral">Member</Badge>
                    )}
                  </span>
                </span>
                {isMe && <Check className="size-4 shrink-0 text-accent" />}
              </button>
            );
          })}
        </div>
      </Sheet>
    </>
  );
}
