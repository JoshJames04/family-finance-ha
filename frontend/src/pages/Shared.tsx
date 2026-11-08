/** Shared page (issue #277): the household bills pot for the current
 * period — the shared-budget total broken down by section, then each
 * member's contribution standing via <SharedPot />. Members without a
 * shared budget get a guided empty state. */
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useCategories, useReady } from "../api/hooks";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Money } from "../components/Money";
import { Button } from "../components/Button";
import { Panel } from "../components/Panel";
import { SharedPot } from "../components/SharedPot";
import { IconTile, PageHeader, Stat } from "../components/Display";
import { Skeleton } from "../components/Feedback";
import { Users2 } from "lucide-react";
import { Link } from "react-router-dom";
import { formatMonthOf, toISODate } from "../lib/format";

type Budget = components["schemas"]["BudgetOut"];

/** The shared budget whose period contains today (fallback: the
 * nearest upcoming, else the most recent). */
function pickShared(budgets: Budget[]): Budget | null {
  const shared = budgets.filter((b) => b.owner_profile_id == null);
  if (!shared.length) return null;
  const today = toISODate(new Date());
  return shared.find(
    (b) => b.start_date <= today && b.end_date >= today)
    ?? shared.find((b) => b.start_date >= today)
    ?? [...shared].sort((a, b) =>
      b.start_date.localeCompare(a.start_date))[0]!;
}

export function Shared() {
  const ready = useReady();
  const budgets = useApi(() => api.get("/api/v1/budgets"), ready);
  const categories = useCategories();
  const shared = budgets.data ? pickShared(budgets.data.items) : null;
  const detail = useApi(
    () => api.get("/api/v1/budgets/{budget_id}",
      { path: { budget_id: shared!.id } }),
    ready && shared !== null, [shared?.id]);

  const parentName = (id: number) => {
    const cat = categories.byId.get(id);
    const parent = cat?.parent_id != null
      ? categories.byId.get(cat.parent_id) : null;
    return (parent ?? cat)?.name ?? "Other";
  };

  const expense = (detail.data?.lines ?? [])
    .filter((l) => l.side === "expense");
  const total = expense.reduce((s, l) => s + l.effective_pence, 0);
  const sections = new Map<string, number>();
  for (const l of expense) {
    const name = parentName(l.category_id);
    sections.set(name, (sections.get(name) ?? 0)
      + l.effective_pence);
  }
  const rows = [...sections.entries()]
    .sort((a, b) => b[1] - a[1]);

  return (
    <div>
      <PageHeader
        title="Shared"
        description="The household bills pot — what's due this period and who's paid in."
      />
      <DataView
        state={budgets}
        empty="No budgets yet"
        emptyIcon={Users2}
        emptyDescription="Create a shared budget to see the household pot here."
        emptyAction={
          <Link to="/budget">
            <Button variant="primary">Open Budget</Button>
          </Link>
        }
        loading={<Skeleton className="h-40 w-full rounded-xl" />}
      >
        {() => shared === null ? (
          <Panel className="p-8 text-center">
            <IconTile icon={Users2} className="mx-auto mb-3" />
            <p className="text-[15px] font-medium">No shared budget</p>
            <p className="mx-auto mt-1 max-w-md text-[13px] text-fg-subtle">
              Shared spending needs an unowned budget — create one on the
              Budget page and this view shows the pot total and each
              member's standing.
            </p>
            <Link to="/budget">
              <Button variant="primary" className="mt-4">
                Set up a shared budget
              </Button>
            </Link>
          </Panel>
        ) : (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
              <Stat
                className="rounded-xl border border-border bg-surface p-4 shadow-card"
                label={`Shared total · ${formatMonthOf(shared.start_date)}`}
                value={<Money pence={total} />}
                hint={`${expense.length} planned expenses`}
              />
              <Stat
                className="rounded-xl border border-border bg-surface p-4 shadow-card"
                label="Period"
                value={`${formatMonthOf(shared.start_date)}`}
                hint={`${shared.start_date} → ${shared.end_date}`}
              />
            </div>

            {rows.length > 0 && (
              <Panel className="mb-4 p-4">
                <p className="mb-3 text-[13px] font-medium uppercase tracking-wide text-fg-subtle">
                  By section
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {rows.map(([name, pence]) => (
                    <div key={name}
                      className="flex items-center justify-between text-[13.5px]">
                      <span className="truncate">{name}</span>
                      <Money pence={pence} className="font-medium" />
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            <SharedPot period={{
              start: shared.start_date, end: shared.end_date,
            }} />
          </>
        )}
      </DataView>
    </div>
  );
}

export const __test__ = { pickShared };
