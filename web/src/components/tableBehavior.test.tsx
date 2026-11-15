import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type Account, type Schemas } from '../api/client';
import { ScopeBoundary } from '../app/ScopeBoundary';
import { session } from '../test/fixtures';
import { Accounts } from '../features/foundation/Pages';
import { ReferencesPage } from '../features/concurrency/ReferenceEditors';
import { MixedUsePage } from '../features/secondary/MixedUsePage';
import { ReportsPage } from '../features/workflows/ReportsPage';

function mount(node: React.ReactNode) {
  render(<RouterProvider router={createMemoryRouter([{ path: '*', element: <ScopeBoundary session={session}>{node}</ScopeBoundary> }])} />);
}
afterEach(() => vi.restoreAllMocks());
const account = (id: string, name: string, classification = 'Asset', parent_id = ''): Account => ({ id, name, classification, parent_id,
  created_at: '', type: 'Other', opening_balance: '0.00', active: true, baseline: 'loaded' });
const accounts = [account('child', 'A child', 'Asset', 'root'), account('debt', 'Debt', 'Liability'), account('root', 'Z parent'), account('equity', 'Equity', 'Equity')];

it('opens Accounts with classification/tree ordering and keeps children attached during user sorting', async () => {
  vi.spyOn(api, 'accounts').mockResolvedValue({ items: accounts }); mount(<Accounts />);
  const region = await screen.findByRole('region', { name: 'Accounts' });
  const names = () => within(region).getAllByRole('rowheader').map(row => row.textContent);
  expect(names()).toEqual(['Z parent', '↳ Level 2, child of Z parent. A child', 'Debt', 'Equity']);
  await userEvent.click(within(region).getByRole('button', { name: 'Name' }));
  expect(names()).toEqual(['Debt', 'Equity', 'Z parent', '↳ Level 2, child of Z parent. A child']);
  await userEvent.click(within(region).getByRole('button', { name: 'Name' }));
  expect(within(region).getByRole('columnheader', { name: 'Name' })).toHaveAttribute('aria-sort', 'descending');
  expect(names()[0]).toBe('Z parent'); expect(names()[1]).toContain('A child');
});
it('sorts Categories by Income/Expense and exposes nested hierarchy without losing full names', async () => {
  vi.spyOn(api, 'categories').mockResolvedValue({ items: [
    { id: 'expense', name: 'A expense', kind: 'Expense', parent_id: '', baseline: 'b' },
    { id: 'child', name: 'A child', kind: 'Income', parent_id: 'income', baseline: 'b' },
    { id: 'income', name: 'Z income', kind: 'Income', parent_id: '', baseline: 'b' },
  ] }); mount(<ReferencesPage resource="categories" />);
  const region = await screen.findByRole('region', { name: 'Categories' });
  expect(within(region).getAllByRole('rowheader')[0]).toHaveTextContent('Z income');
  expect(within(region).getAllByRole('rowheader')[1]).toHaveTextContent('child of Z income');
  await userEvent.click(within(region).getByRole('button', { name: 'Kind' }));
  await userEvent.click(within(region).getByRole('button', { name: 'Kind' }));
  expect(within(region).getAllByRole('rowheader')[0]).toHaveTextContent('A expense');
});
it('sorts mixed-use amounts numerically both ways while retaining complete descriptions', async () => {
  const row: Schemas['MixedUse'] = { id: 'two', txn_date: '2026-01-01', total_expense: '2.00', allocated_amount: '1.00', business_use_percentage: '50',
    new_payee_name: '', new_tag_name: '', allocation_rationale: '', description: 'Full description with several words and all original detail', reference: '', account_id: 'cash', category_id: 'office',
    payee_id: '', tag_ids: [], baseline: 'b', linked_transaction_id: 'p', ledger_status: 'linked', recurring_schedule_id: '', recurring_scheduled_date: '', recurring_occurrence_type: '' };
  vi.spyOn(api, 'mixedUse').mockResolvedValue({ items: [{ ...row, id: 'ten', total_expense: '10.00' }, row] }); mount(<MixedUsePage />);
  const region = await screen.findByRole('region', { name: 'Mixed-use Expense Log' });
  await userEvent.click(within(region).getByRole('button', { name: 'Total' }));
  expect(within(region).getAllByRole('row')[1]).toHaveTextContent('2.00');
  await userEvent.click(within(region).getByRole('button', { name: 'Total' }));
  expect(within(region).getAllByRole('row')[1]).toHaveTextContent('10.00');
  expect(within(region).getAllByRole('rowheader')[0]).toHaveTextContent(row.description);
});
it('renders Dashboard balances and statement trees with no sorting controls and preserves accounting totals', async () => {
  vi.spyOn(api, 'reports').mockResolvedValue({ profit_loss: { income: '0.00', expenses: '0.00', net: '0.00' },
    balance_sheet: { assets: [{ id: 'child', name: 'Child category', parent_id: 'parent', type: 'Expense', balance: '2.00', direct_balance: '2.00', rollup_balance: '2.00' },
      { id: 'parent', name: 'Parent category', parent_id: '', type: 'Expense', balance: '0.00', direct_balance: '0.00', rollup_balance: '2.00' }],
      liabilities: [], equity: [], current_earnings: '0.00', total_assets: '2.00', total_liabilities: '0.00', total_equity: '2.00', difference: '0.00' },
    account_balances: accounts.map(row => ({ ...row, parent_id: row.parent_id ?? '', balance: '0.00' })) });
  mount(<ReportsPage dashboard />);
  const region = await screen.findByRole('region', { name: 'Current account balances' });
  expect(within(region).getAllByRole('rowheader')[0]).toHaveTextContent('Z parent');
  expect(within(region).getAllByRole('rowheader')[1]).toHaveTextContent('child of Z parent');
  expect(within(region).queryByRole('button')).not.toBeInTheDocument();
  const statement = screen.getByRole('region', { name: 'Assets' });
  expect(within(statement).getAllByRole('rowheader')[0]).toHaveTextContent('Parent category');
  expect(within(statement).getAllByRole('rowheader')[1]).toHaveTextContent('child of Parent category');
  expect(screen.getByText('Assets = Liabilities + Equity.')).toBeVisible();
});
