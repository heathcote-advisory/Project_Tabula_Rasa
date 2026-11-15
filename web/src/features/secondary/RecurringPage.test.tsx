import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider, MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api, ApiError, type Schemas } from '../../api/client';
import { ScopeBoundary } from '../../app/ScopeBoundary';
import { session } from '../../test/fixtures';
import { RecurringDetail, RecurringPage } from './RecurringPage';

afterEach(() => vi.restoreAllMocks());

it.each([0, 2])('reports %i queued schedules without claiming synchronous generation', async count => {
  vi.spyOn(api, 'recurring').mockResolvedValue({ items: [] });
  const reconcile = vi.spyOn(api, 'reconcileRecurring').mockResolvedValue({
    generated_count: 0, attention_count: 0, queued_count: count, execution_mode: 'worker',
  });
  render(<MemoryRouter><ScopeBoundary session={session}><RecurringPage /></ScopeBoundary></MemoryRouter>);
  await userEvent.click(screen.getByRole('button', { name: 'Generate due occurrences' }));
  expect(reconcile).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Confirm generation' }));
  expect(await screen.findByText(new RegExp(`Queued ${count} schedule`))).toHaveTextContent('refresh later to inspect occurrences and attention');
  expect(screen.queryByText(/Generated 0 occurrence/)).not.toBeInTheDocument();
  expect(reconcile).toHaveBeenCalledTimes(1);
});

const schedule: Schemas['Recurring'] = { id: 'schedule', baseline: 'loaded', name: 'Subscription', source_kind: 'transaction', status: 'active',
  start_date: '2026-01-01', recurrence_unit: 'month', recurrence_interval: '1', end_date: '', amount: '10.00', total_expense: '',
  business_use_percentage: '100', allocation_rationale: '', account_id: 'cash', category_id: 'office', payee_id: '', tag_ids: [], description: '', reference: '',
  attention_code: '', attention_message: '', next_due_date: '2026-02-01', latest_current_occurrence_date: '2026-01-01' };
function detail(can_delete: boolean) {
  vi.spyOn(api, 'recurringSchedule').mockResolvedValue({ schedule, occurrences: [], mixed_sources: [], can_delete });
  vi.spyOn(api, 'accounts').mockResolvedValue({ items: [] }); vi.spyOn(api, 'categories').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'payees').mockResolvedValue({ items: [] }); vi.spyOn(api, 'tags').mockResolvedValue({ items: [] });
  render(<RouterProvider router={createMemoryRouter([{ path: '/recurring/:recordId', element: <ScopeBoundary session={session}><RecurringDetail /></ScopeBoundary> }],
    { initialEntries: ['/recurring/schedule'] })} />);
}
it.each([true, false])('retains independent End when delete eligibility is %s', async eligible => {
  detail(eligible);
  expect(await screen.findByRole('button', { name: 'End schedule' })).toBeVisible();
  expect(screen.getByRole('link', { name: /^Back$/ })).toHaveAttribute('href', '/recurring');
  expect(Boolean(screen.queryByRole('button', { name: /^Delete$/ }))).toBe(eligible);
});
it('requires destructive confirmation and preserves End after server rejection of stale eligibility', async () => {
  const remove = vi.spyOn(api, 'deleteRecurring').mockRejectedValue(new ApiError('recurring_in_use'));
  detail(true); await userEvent.click(await screen.findByRole('button', { name: /^Delete$/ }));
  const confirmation = screen.getByRole('alertdialog');
  expect(confirmation).toHaveTextContent('original entry and its receipts are retained'); expect(remove).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Confirm delete' })).toHaveClass('action-destructive');
  await userEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('End the schedule');
  expect(remove).toHaveBeenCalledWith(session, 'schedule', 'loaded');
  expect(screen.getByRole('button', { name: 'End schedule' })).toBeEnabled();
});
