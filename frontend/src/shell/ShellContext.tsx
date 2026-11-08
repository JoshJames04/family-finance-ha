import { createContext, useContext } from "react";

export interface ShellActions {
  openQuickAdd: () => void;
  openCommand: () => void;
  /** Bumped after a global mutation (e.g. quick-add) so pages refetch. */
  dataVersion: number;
}

export const ShellContext = createContext<ShellActions>({
  openQuickAdd: () => {},
  openCommand: () => {},
  dataVersion: 0,
});

export const useShell = () => useContext(ShellContext);
