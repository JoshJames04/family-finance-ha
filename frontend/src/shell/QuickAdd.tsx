/** Quick-add transaction dialog — reachable from anywhere (top bar,
 * dashboard, ⌘K). Amount is entered positive; direction decides sign.
 * The "Repeats" control (issue #292) promotes the entry to a recurring
 * bill: a commitment is created and the first payment posted, so a
 * regular expense is one flow — not a hidden second page. */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { Dialog } from "../components/Dialog";
import { Button } from "../components/Button";
import { Field, Input, Select } from "../components/Field";
import { Segmented } from "../components/Display";
import { undoToast } from "../lib/toast";
import { toISODate } from "../lib/format";

type Repeat = "once" | "weekly" | "monthly" | "yearly";
const REPEAT_FREQ: Record<Exclude<Repeat, "once">, string> = {
  weekly: "weekly", monthly: "monthly", yearly: "annually",
};

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
  const [repeats, setRepeats] = useState<Repeat>("once");
  const [endsOn, setEndsOn] = useState("");
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
      setRepeats("once"); setEndsOn("");
    }
  }, [open]);

  const valid = accountId && payee.trim() && /^\d+(\.\d{1,2})?$/.test(amount) && Number(amount) > 0;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    const signed = `${direction === "out" ? "-" : ""}${amount}`;
    const done = () => { onSaved(); onOpenChange(false); };
    const fail = (err: Error) => setError(err.message);
    const finish = () => setBusy(false);
    if (repeats === "once") {
      api.post("/api/v1/transactions", {
        body: {
          account_id: Number(accountId),
          date,
          payee: payee.trim(),
          amount: signed,
          category_id: categoryId ? Number(categoryId) : null,
        },
      }).then(done).catch(fail).finally(finish);
      return;
    }
    // Recurring: the bill keeps generating; materialise posts the
    // first payment as a real transaction right now.
    api.post("/api/v1/commitments", {
      body: {
        name: payee.trim(),
        account_id: Number(accountId),
        category_id: categoryId ? Number(categoryId) : null,
        amount: signed,
        formula: null,
        frequency: REPEAT_FREQ[repeats] as never,
        day_of_month: null,
        next_date: date,
        end_date: endsOn || null,
        occurrence_count: null,
        notes: null,
      },
    }).then(async (c) => {
        await api.post("/api/v1/commitments/{cid}/materialise", {
          path: { cid: c.id }, query: { horizon: date },
        }).catch(() => undefined);
        undoToast(`${payee.trim()} added as a recurring ${repeats} bill`, () =>
          api.delete("/api/v1/commitments/{cid}", { path: { cid: c.id } }));
        done();
      })
      .catch(fail)
      .finally(finish);
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

        <Field label="Repeats"
          hint={repeats === "once" ? undefined : "Keeps coming up as a bill until you pause it."}>
          {(_id) => (
            <Segmented
              label="Repeats"
              value={repeats}
              onChange={setRepeats}
              options={[
                { value: "once", label: "Just this once" },
                { value: "weekly", label: "Every week" },
                { value: "monthly", label: "Every month" },
                { value: "yearly", label: "Every year" },
              ]}
            />
          )}
        </Field>
        {repeats !== "once" && (
          <Field label="Ends on" hint="Leave blank to keep it going until you stop it.">
            {(id) => (
              <Input id={id} type="date" value={endsOn}
                onChange={(e) => setEndsOn(e.target.value)} />
            )}
          </Field>
        )}
        {error && <p role="alert" className="text-[13px] text-negative">{error}</p>}
      </form>
    </Dialog>
  );
}
