import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { PayrollFactsFields, confirmationFacts, poundsToPence } from '../components/PayrollConfirmation';

afterEach(cleanup);

it('requires independent printed gross and net rather than assuming forecasts', () => {
  expect(() => confirmationFacts({})).toThrow(/gross/i);
  render(<PayrollFactsFields values={{}} onChange={() => {}} />);
  expect(screen.getByLabelText('Payslip gross')).toBeTruthy();
  expect(screen.getByLabelText('Payslip net entitlement')).toBeTruthy();
  expect(screen.getByLabelText('Taxable pay this period')).toBeTruthy();
});

it('parses signed money exactly and preserves unknown optional facts', () => {
  expect(poundsToPence('-0.29')).toBe(-29);
  expect(poundsToPence('100.01')).toBe(10001);
  expect(() => poundsToPence('1.001')).toThrow();
  expect(() => poundsToPence('NaN')).toThrow();
  expect(confirmationFacts({ payslip_gross_pence:'3000', payslip_net_pence:'2400',
    taxable_pence:'2800' })).toEqual({ payslip_gross_pence:300000,
    payslip_net_pence:240000, taxable_pence:280000 });
});

it('requires paired YTD facts', () => {
  expect(() => confirmationFacts({ payslip_gross_pence:'3000', payslip_net_pence:'2400',
    taxable_pence:'2800', ytd_taxable_pence:'5600' })).toThrow(/both/i);
});


it('allows gross-only confirmations without inventing taxable pay', () => {
  expect(confirmationFacts({payslip_gross_pence:'1000', payslip_net_pence:'1000'}, true))
    .toEqual({payslip_gross_pence:100000, payslip_net_pence:100000});
  render(<PayrollFactsFields values={{}} onChange={() => {}} grossOnly />);
  expect(screen.getByLabelText('Taxable pay this period (optional)')).toBeTruthy();
});

it('retains explicit gross-only taxable pay while PAYE still requires it', () => {
  expect(confirmationFacts({payslip_gross_pence:'1000', payslip_net_pence:'1000',
    taxable_pence:'0'}, true).taxable_pence).toBe(0);
  expect(() => confirmationFacts({payslip_gross_pence:'1000', payslip_net_pence:'1000'}))
    .toThrow(/taxable/);
});
