/** Undo affordance (issue #290): destructive-but-reversible actions
 * report success with an inline Undo — one click beats hunting for a
 * restore control. `undo` is only offered where the action can truly
 * be reversed (flag toggles, or recreate-from-captured-state); bulk
 * writes like payee merges keep plain toasts instead. */
import { toast } from "sonner";

export function undoToast(message: string, undo: () => Promise<unknown>) {
  toast.success(message, {
    action: {
      label: "Undo",
      onClick: () => {
        undo().catch((e: Error) =>
          toast.error(`Undo failed: ${e.message}`));
      },
    },
  });
}
