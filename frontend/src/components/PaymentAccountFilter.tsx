import { useEffect, useState } from "react";
import type { components } from "../api/schema";
import { Segmented } from "./Display";
import { Select } from "./Field";

type Account = components["schemas"]["AccountOut"];
type Scope = "all" | "mine" | "shared";

export function usePaymentAccountFilter(accounts: Account[], used: ReadonlySet<number>, me: number | null) {
  const [scope, setScope] = useState<Scope>("all");
  const [selected, setSelected] = useState("");
  const eligible = (account: Account) => scope === "mine"
    ? me !== null && account.owner_profile_id === me
    : account.owner_profile_id === null;
  const options = accounts
    .filter(account => !account.archived && used.has(account.id) && eligible(account))
    .sort((a, b) => a.name.localeCompare(b.name));
  const id = options.find(option => String(option.id) === selected)?.id;
  useEffect(() => {
    if (selected && id === undefined) setSelected("");
  }, [id, selected]);
  const byId = new Map(accounts.map(account => [account.id, account]));
  return {
    scope,
    setScope: (next: Scope) => { setScope(next); setSelected(""); },
    options,
    selected: id === undefined ? "" : String(id),
    setSelected,
    includes: (accountId: number | null) => {
      const account = accountId === null ? undefined : byId.get(accountId);
      if (id !== undefined) return accountId === id;
      return scope === "all" || (account !== undefined && !account.archived && eligible(account));
    },
  };
}

export function PaymentAccountFilter({ label, filter }: {
  label: string;
  filter: ReturnType<typeof usePaymentAccountFilter>;
}) {
  return (
    <div className="grid min-w-0 justify-items-start gap-2">
      <Segmented label={label} className="[&_button]:min-h-9" value={filter.scope} onChange={filter.setScope}
        options={[
          { value: "all", label: "All" },
          { value: "mine", label: "Mine" },
          { value: "shared", label: "Shared" },
        ]} />
      <Select aria-label="Payment account" className="w-full max-w-full"
        disabled={filter.options.length === 0} value={filter.selected}
        onChange={event => filter.setSelected(event.target.value)}>
        <option value="">All payment accounts</option>
        {filter.options.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
      </Select>
    </div>
  );
}
