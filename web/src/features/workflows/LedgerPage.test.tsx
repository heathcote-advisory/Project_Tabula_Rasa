import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError, type Schemas } from '../../api/client';
import { createPrivateCache, resourceKey } from '../../api/query';
import { EntityContext } from '../../app/context';
import { session } from '../../test/fixtures';
import { LedgerPage } from './LedgerPage';

vi.mock('../concurrency/useReferenceChoices', () => ({
  useReferenceChoices: () => ({ accounts: [{ id: 'cash', name: 'Cash', active: true }],
    categories: [{ id: 'office', name: 'Office', kind: 'Expense' }], payees: [], tags: [], pending: false }),
  activeChoices: (rows: { active: boolean }[]) => rows.filter(row => row.active),
}));

const first: Schemas['Transaction'] = { id: 'one', txn_date: '2026-10-05', kind: 'Expense', amount: '12.50',
  account_id: 'cash', to_account_id: '', category_id: 'office', payee_id: '', tag_ids: [],
  description: 'First memo', reference: '', created_at: 'now', baseline: 'first-baseline', source_type: '', recurring_schedule_id: '' };
const second: Schemas['Transaction'] = { ...first, id: 'two', description: 'Second memo', baseline: 'second-baseline' };
function ledgerItem(row: Schemas['Transaction']): Schemas['LedgerItem'] {
  return { id: row.id, record_id: row.id, entry_type: 'transaction', txn_date: row.txn_date, kind: row.kind,
    amount: row.amount, account: 'Cash', to_account: '', category: 'Office', payee: '', tags: [],
    description: row.description, reference: row.reference, receipt_count: 0, postings: [], source_type: '', recurring_schedule_id: row.recurring_schedule_id };
}
const records = [ledgerItem(first), ledgerItem(second)];
const initialFilters = { kind: 'All', account_ids: [], category_ids: [], tag_ids: [] };

async function setup(beforeBulk?: () => void, enterBulk = true) {
  vi.spyOn(api, 'ledger').mockResolvedValue({ items: records });
  vi.spyOn(api, 'transactions').mockResolvedValue({ items: [first, second] });
  const cache = createPrivateCache();
  const router = createMemoryRouter([{ path: '/', element: <QueryClientProvider client={cache}>
    <EntityContext.Provider value={session}><LedgerPage /></EntityContext.Provider>
  </QueryClientProvider> }]);
  render(<RouterProvider router={router} />);
  await screen.findByRole('region', { name: 'Ledger' });
  beforeBulk?.();
  if (enterBulk) {
    await userEvent.click(screen.getByRole('button', { name: 'Bulk actions' }));
    await screen.findByRole('button', { name: 'Leave Bulk mode' });
  }
  return cache;
}
const selectFirst = () => screen.getByRole('checkbox', { name: 'Select 2026-10-05 · First memo · 12.50' });
const selectSecond = () => screen.getByRole('checkbox', { name: 'Select 2026-10-05 · Second memo · 12.50' });
function expectNoSelection() {
  expect(selectFirst()).not.toBeChecked();
  expect(selectSecond()).not.toBeChecked();
  expect(screen.getByText('0 selected logical record(s)')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy transaction' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Permanently delete selected' })).toBeDisabled();
}

beforeEach(() => vi.restoreAllMocks());

it('refreshes visible values before allowing selection with newly loaded baselines', async () => {
  const remove = vi.spyOn(api, 'bulkDeleteTransactions').mockResolvedValue({ status: 'fully_applied', count: '1' });
  const changed = { ...first, description: 'Changed by another client', amount: '99.00', baseline: 'new-baseline' };
  await setup(() => {
    vi.mocked(api.transactions).mockResolvedValue({ items: [changed, second] });
    vi.mocked(api.ledger).mockResolvedValue({ items: [ledgerItem(changed), records[1]] });
  });
  expect(screen.queryByRole('checkbox', { name: 'Select 2026-10-05 · First memo · 12.50' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('checkbox', { name: 'Select 2026-10-05 · Changed by another client · 99.00' }));
  await userEvent.click(screen.getByRole('button', { name: 'Permanently delete selected' }));
  expect(screen.getByRole('alertdialog')).toHaveTextContent('aggregate absolute amount 99.00');
  await userEvent.click(screen.getByRole('checkbox', { name: 'I understand that this cannot be undone.' }));
  await userEvent.click(screen.getByRole('button', { name: 'Confirm permanent deletion' }));
  await waitFor(() => expect(remove).toHaveBeenCalledOnce());
  expect(remove.mock.calls[0][1].expected).toEqual([{ id: 'one', baseline: 'new-baseline' }]);
});

it('does not enable bulk selection when refreshing the visible ledger fails', async () => {
  await setup(undefined, false);
  vi.mocked(api.ledger).mockRejectedValue(new ApiError('unavailable'));
  await userEvent.click(screen.getByRole('button', { name: 'Bulk actions' }));
  // The real query policy retries unavailable reads after a 1s backoff.
  // Wait beyond that backoff for the final error UI on slower CI runners.
  await screen.findByRole('button', { name: 'Bulk actions' }, { timeout: 5_000 });
  expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
  expect(screen.queryByRole('button', { name: 'Leave Bulk mode' })).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: /^Select / })).not.toBeInTheDocument();
});

it('clears selected IDs permanently when a search changes and is restored', async () => {
  await setup();
  await userEvent.click(selectFirst());
  expect(selectFirst()).toBeChecked();
  await userEvent.type(screen.getByLabelText('Search transactions'), 'First');
  expect(screen.getByText('0 selected logical record(s)')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Clear search' }));
  expectNoSelection();
});

it('clears selected IDs permanently when the sort changes and is restored', async () => {
  await setup();
  await userEvent.click(selectFirst());
  await userEvent.click(selectSecond());
  expect(screen.getByText('2 selected logical record(s)')).toBeVisible();
  await userEvent.selectOptions(screen.getByLabelText('Sort entries by'), 'Amount');
  expect(screen.getByText('0 selected logical record(s)')).toBeVisible();
  await userEvent.selectOptions(screen.getByLabelText('Sort entries by'), 'Date');
  expectNoSelection();
});

it('clears the whole selection when refreshed ledger data removes a selected record', async () => {
  const update = vi.spyOn(api, 'bulkUpdateTransactions');
  const remove = vi.spyOn(api, 'bulkDeleteTransactions');
  const cache = await setup();
  await userEvent.click(selectFirst());
  await userEvent.click(selectSecond());
  await userEvent.click(screen.getByRole('checkbox', { name: 'Apply Payee to all selected' }));
  expect(screen.getByRole('button', { name: 'Review selected changes' })).toBeEnabled();
  const key = [...resourceKey(session, 'ledger'), initialFilters];
  const previousRevision = cache.getQueryState(key)?.dataUpdatedAt ?? 0;
  await act(async () => { cache.setQueryData(key, { items: [records[0]] }, { updatedAt: previousRevision + 1 }); });
  await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Select 2026-10-05 · Second memo · 12.50' })).not.toBeInTheDocument());
  expect(selectFirst()).not.toBeChecked();
  expect(screen.getByText('0 selected logical record(s)')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Review selected changes' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Permanently delete selected' })).toBeDisabled();
  expect(update).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
});

it('preserves selected IDs, explicit changes and the error after a rejected bulk write refreshes the ledger', async () => {
  const update = vi.spyOn(api, 'bulkUpdateTransactions').mockRejectedValue(new ApiError('stale'));
  await setup();
  await userEvent.click(selectFirst());
  await userEvent.click(screen.getByRole('checkbox', { name: 'Apply Payee to all selected' }));
  await userEvent.click(screen.getByRole('button', { name: 'Review selected changes' }));
  await userEvent.click(screen.getByRole('button', { name: 'Confirm selected changes' }));
  await waitFor(() => expect(api.ledger).toHaveBeenCalledTimes(3));
  expect(selectFirst()).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Apply Payee to all selected' })).toBeChecked();
  expect(await screen.findByRole('alert')).toHaveTextContent('Your draft is preserved');
  expect(update).toHaveBeenCalledTimes(1);
  expect(update.mock.calls[0][1].expected).toEqual([{ id: 'one', baseline: 'first-baseline' }]);
});

it('shows the confirmed bulk result after clearing the selection and refreshing the ledger', async () => {
  const update = vi.spyOn(api, 'bulkUpdateTransactions').mockResolvedValue({ status: 'fully_applied', count: '1' });
  await setup();
  await userEvent.click(selectFirst());
  await userEvent.click(screen.getByRole('checkbox', { name: 'Apply Payee to all selected' }));
  await userEvent.click(screen.getByRole('button', { name: 'Review selected changes' }));
  await userEvent.click(screen.getByRole('button', { name: 'Confirm selected changes' }));
  await waitFor(() => expect(api.ledger).toHaveBeenCalledTimes(3));
  expect(await screen.findByText('1 transaction(s) updated.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Bulk actions' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Review selected changes' })).not.toBeInTheDocument();
  expect(update).toHaveBeenCalledOnce();
});

it('requires leaving failed Bulk mode and loading a fresh selection before another command', async () => {
  const update = vi.spyOn(api, 'bulkUpdateTransactions').mockRejectedValueOnce(new ApiError('unavailable'))
    .mockResolvedValue({ status: 'fully_applied', count: '1' });
  await setup();
  await userEvent.click(selectFirst());
  await userEvent.click(screen.getByRole('checkbox', { name: 'Apply Payee to all selected' }));
  await userEvent.click(screen.getByRole('button', { name: 'Review selected changes' }));
  await userEvent.click(screen.getByRole('button', { name: 'Confirm selected changes' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: 'Review selected changes' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Leave Bulk mode' }));
  vi.mocked(api.transactions).mockResolvedValue({ items: [{ ...first, baseline: 'refreshed-baseline' }, second] });
  await userEvent.click(screen.getByRole('button', { name: 'Bulk actions' }));
  await screen.findByRole('button', { name: 'Leave Bulk mode' });
  expectNoSelection();
  await userEvent.click(selectFirst());
  await userEvent.click(screen.getByRole('checkbox', { name: 'Apply Payee to all selected' }));
  await userEvent.click(screen.getByRole('button', { name: 'Review selected changes' }));
  await userEvent.click(screen.getByRole('button', { name: 'Confirm selected changes' }));
  await screen.findByText('1 transaction(s) updated.');
  expect(update).toHaveBeenCalledTimes(2);
  expect(update.mock.calls[1][1].expected).toEqual([{ id: 'one', baseline: 'refreshed-baseline' }]);
});

it('retains the entire failed selection when refresh removes one selected record', async () => {
  const update = vi.spyOn(api, 'bulkUpdateTransactions').mockRejectedValue(new ApiError('stale'));
  await setup();
  await userEvent.click(selectFirst());
  await userEvent.click(selectSecond());
  await userEvent.click(screen.getByRole('checkbox', { name: 'Apply Payee to all selected' }));
  vi.mocked(api.ledger).mockResolvedValue({ items: [records[0]] });
  await userEvent.click(screen.getByRole('button', { name: 'Review selected changes' }));
  await userEvent.click(screen.getByRole('button', { name: 'Confirm selected changes' }));
  await waitFor(() => expect(api.ledger).toHaveBeenCalledTimes(3));
  expect(await screen.findByRole('alert')).toHaveTextContent('Your draft is preserved');
  expect(screen.getByText('2 selected logical record(s)')).toBeVisible();
  expect(screen.getByRole('checkbox', { name: 'Apply Payee to all selected' })).toBeChecked();
  expect(update).toHaveBeenCalledOnce();
  expect(update.mock.calls[0][1].transaction_ids).toEqual(['one', 'two']);
});

it('searches stable transaction IDs with no visible ID column and keeps the edit identity', async () => {
  await setup(undefined, false);
  await userEvent.type(screen.getByLabelText('Search transactions'), 'one');
  expect(screen.getByRole('checkbox', { name: 'Select 2026-10-05 · First memo · 12.50' })).toBeVisible();
  expect(screen.queryByRole('columnheader', { name: 'ID' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Edit 2026-10-05 · First memo · 12.50' })).toBeVisible();
  expect(screen.queryByRole('button', { name: /Edit .*Second memo/ })).not.toBeInTheDocument();
});
