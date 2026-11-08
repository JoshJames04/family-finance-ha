/** Quick-add transaction dialog — reachable from anywhere (top bar,
 * dashboard, ⌘K). Amount is entered positive; direction decides sign. */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { Dialog } from "../components/Dialog";
import { Button } from "../components/Button";
import { Field, Input, Select } from "../components/Field";
import { Segmented } from "../components/Display";
import { toISODate } from "../lib/format";

export function QuickAdd({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const accounts = useApi(() => api.get("/api/v1/accounts"), open);
  const categories = useApi(
    () => api.get("/api/v1/categories"), open);
  const [direction, setDirection] = useState<"out" | "in">("out");
  const [accountId, setAccountId] = useState("");
  const [date, setDate] = useState(toISODate(new Date()));
  const [payee, setPayee] = useState("");
  const [amount, setAmount] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accountId && accounts.data?.items[0]) {
      setAccountId(String(accounts.data.items[0].id));
    }
  }, [accounts.data, accountId]);

  useEffect(() => {
    if (open) {
      setPayee(""); setAmount(""); setCategoryId(""); setError(null);
      setDate(toISODate(new Date()));
    }
  }, [open]);

  const valid = accountId && payee.trim() && /^\d+(\.\d{1,2})?$/.test(amount) && Number(amount) > 0;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    api.post("/api/v1/transactions", {
      body: {
        account_id: Number(accountId),
        date,
        payee: payee.trim(),
        amount: direction === "out" ? `-${amount}` : amount,
        category_id: categoryId ? Number(categoryId) : null,
      },
    })
      .then(() => { onSaved(); onOpenChange(false); })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add transaction"
      description="Record a payment or income manually."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" type="submit" form="ff-quick-add" disabled={!valid || busy}>
            {busy ? "Saving…" : "Save transaction"}
          </Button>
        </>
      }
    >
      <form id="ff-quick-add" onSubmit={submit} className="grid gap-4">
        <Segmented
          label="Direction"
          value={direction}
          onChange={setDirection}
          options={[{ value: "out", label: "Money out" }, { value: "in", label: "Money in" }]}
          className="w-fit"
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Amount (£)">
            {(id) => (
              <Input id={id} inputMode="decimal" placeholder="0.00" autoFocus
                value={amount} onChange={(e) => setAmount(e.target.value.trim())} />
            )}
          </Field>
          <Field label="Date">
            {(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}
          </Field>
        </div>
        <Field label={direction === "out" ? "Paid to" : "Received from"}>
          {(id) => <Input id={id} placeholder="e.g. Tesco" value={payee} onChange={(e) => setPayee(e.target.value)} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Account">
            {(id) => (
              <Select id={id} value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                {(accounts.data?.items ?? []).map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Category">
            {(id) => (
              <Select id={id} value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
                <option value="">Uncategorised</option>
                {(categories.data?.items ?? []).filter((c) => !c.archived).map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        {error && <p role="alert" className="text-[13px] text-negative">{error}</p>}
      </form>
    </Dialog>
  );
}
