import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, ApiError } from '../../api/client';
import { ScopeBoundary } from '../../app/ScopeBoundary';
import { session } from '../../test/fixtures';
import { BusinessCurrency } from './BusinessCurrency';

afterEach(() => vi.restoreAllMocks());

it('requires explicit denomination confirmation and prevents duplicate submissions while saving', async () => {
  vi.spyOn(api, 'businessSettings').mockResolvedValue({ currency_code: 'USD', version: 7 });
  let finish!: () => void;
  const change = vi.spyOn(api, 'changeBusinessCurrency').mockImplementation(() => new Promise(resolve => {
    finish = () => resolve({ currency_code: 'CAD', version: 8 });
  }));
  render(<ScopeBoundary session={session}><BusinessCurrency /></ScopeBoundary>);
  fireEvent.change(await screen.findByLabelText('Business currency'), { target: { value: 'CAD' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save currency' }));
  expect(change).not.toHaveBeenCalled();
  expect(screen.getByRole('alertdialog')).toHaveTextContent('No exchange rate or FX conversion occurs: 100.00 USD becomes 100.00 CAD.');
  fireEvent.click(screen.getByRole('button', { name: 'Keep current currency' }));
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Save currency' }));
  fireEvent.click(screen.getByRole('button', { name: 'Change currency label' }));
  expect(change).toHaveBeenCalledExactlyOnceWith(session, { currency_code: 'CAD', version: 7, confirm_label_change: true });
  expect(screen.getByRole('button', { name: 'Change currency label' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Keep current currency' })).toBeDisabled();
  await act(async () => finish());
  expect(await screen.findByText(/Business currency change confirmed/)).toBeVisible();
  expect(screen.getByLabelText('Business currency')).toBeDisabled();
});

it.each(['stale', 'unavailable'] as const)('stops safely after %s without retry or success', async code => {
  vi.spyOn(api, 'businessSettings').mockResolvedValue({ currency_code: 'CAD', version: 2 });
  const change = vi.spyOn(api, 'changeBusinessCurrency').mockRejectedValue(new ApiError(code));
  render(<ScopeBoundary session={session}><BusinessCurrency /></ScopeBoundary>);
  fireEvent.change(await screen.findByLabelText('Business currency'), { target: { value: 'USD' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save currency' }));
  fireEvent.click(screen.getByRole('button', { name: 'Change currency label' }));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByText(/Business currency change confirmed/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Business currency')).toBeDisabled();
  expect(change).toHaveBeenCalledTimes(1);
});
