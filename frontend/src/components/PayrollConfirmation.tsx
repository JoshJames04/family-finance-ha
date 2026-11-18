import { Field, Input } from './Field';

const labels = {
  payslip_gross_pence: 'Payslip gross',
  payslip_net_pence: 'Payslip net entitlement',
  taxable_pence: 'Taxable pay this period',
  niable_pence: 'NI earnings this period (optional)',
  ytd_taxable_pence: 'Taxable pay to date (optional)',
  ytd_tax_pence: 'PAYE to date (optional)',
};

export function poundsToPence(raw: string): number {
  if (!/^-?\d+(?:\.\d{1,2})?$/.test(raw.trim())) {
    throw new Error('Enter a valid amount with no more than two decimal places');
  }
  const [whole, fraction = ''] = raw.trim().replace('-', '').split('.');
  const value = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(value)) throw new Error('Amount is too large');
  return raw.trim().startsWith('-') ? -value : value;
}

export function confirmationFacts(values: Record<string, string>) {
  const required = (key: keyof typeof labels) => {
    if (!values[key]?.trim()) throw new Error(`Enter ${labels[key].toLowerCase()}`);
    return poundsToPence(values[key]);
  };
  const optional = Object.fromEntries(Object.keys(labels)
    .filter(k => !['payslip_gross_pence', 'payslip_net_pence', 'taxable_pence'].includes(k) && values[k]?.trim())
    .map(k => [k, poundsToPence(values[k]!)]));
  if (('ytd_tax_pence' in optional) !== ('ytd_taxable_pence' in optional)) {
    throw new Error('Enter both YTD taxable pay and PAYE, or leave both blank');
  }
  return { payslip_gross_pence:required('payslip_gross_pence'),
    payslip_net_pence:required('payslip_net_pence'), taxable_pence:required('taxable_pence'), ...optional,
    ...(values.statutory_pay_date ? {statutory_pay_date:values.statutory_pay_date} : {}) };
}

export function PayrollFactsFields({ values, onChange }: {
  values: Record<string, string>; onChange: (values: Record<string, string>) => void;
}) {
  return <fieldset className="grid gap-3 rounded-lg border border-border p-3">
    <legend className="px-1 text-sm font-medium">Printed payslip totals</legend>
    <p className="text-xs text-fg-subtle">Use the printed net entitlement before advances already paid. Bank payments are matched separately. Leave unavailable optional figures blank.</p>
    <Field label="Statutory PAYE date (optional)" hint="Use the date reported on the payslip when it differs from cash receipt.">{id =>
      <Input id={id} type="date" value={values.statutory_pay_date ?? ''}
        onChange={e => onChange({ ...values, statutory_pay_date:e.target.value })} />
    }</Field>
    {Object.entries(labels).map(([key, label]) => <Field key={key} label={label}>{id =>
      <Input id={id} inputMode="decimal" value={values[key] ?? ''}
        onChange={e => onChange({ ...values, [key]:e.target.value })} />
    }</Field>)}
  </fieldset>;
}
