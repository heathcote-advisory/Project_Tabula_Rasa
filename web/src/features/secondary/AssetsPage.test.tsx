import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type Schemas, type Session } from '../../api/client';
import { ScopeBoundary } from '../../app/ScopeBoundary';
import { session } from '../../test/fixtures';
import { AssetDetail } from './AssetsPage';

afterEach(() => vi.restoreAllMocks());

const detail: Schemas['AssetDetail'] = {
  asset: {
    id: 'equipment', baseline: 'loaded', name: 'Synthetic equipment', asset_type: 'Computers & office equipment',
    acquisition_date: '2026-01-01', depreciation_start_date: '2026-01-01', depreciation_method: 'Straight-line',
    cost_basis: '100.00', salvage_value: '0.00', useful_life_quarters: '4',
    depreciation_expense_account_id: '', accumulated_depreciation_account_id: '',
    notes: '', payee_id: '', reference: '', retired_date: '', source_transaction_id: '', status: 'active',
  },
  can_delete: true, posting_accounts_locked: false, terms_locked: false, history: [], schedule: [],
};

it('keeps the asset editor and schedule but clears depreciation commands when switching to beta', async () => {
  vi.spyOn(api, 'asset').mockResolvedValue(detail);
  vi.spyOn(api, 'assetTypes').mockResolvedValue({ items: [detail.asset.asset_type] });
  vi.spyOn(api, 'accounts').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'categories').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'payees').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'tags').mockResolvedValue({ items: [] });

  const router = createMemoryRouter([{ path: '/assets/:recordId', element: <AssetDetail /> }],
    { initialEntries: ['/assets/equipment'] });
  const editor = (current: Session) =>
    <ScopeBoundary session={current}>
      <RouterProvider router={router} />
    </ScopeBoundary>;
  const internal = { ...session, capabilities: [...session.capabilities, 'asset:depreciate'] };
  const { rerender } = render(editor(internal));
  expect(await screen.findByRole('button', { name: 'Post due depreciation' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save period override' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Remove period override' })).toBeVisible();

  rerender(editor({ ...session, entity: { ...session.entity, id: 'beta', name: 'Beta' } }));
  expect(await screen.findByRole('button', { name: 'Save changes' })).toBeVisible();
  expect(screen.getByRole('table', { name: 'Depreciation schedule and saved history' })).toBeVisible();
  expect(screen.queryByRole('group', { name: 'Explicit actions' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /post due depreciation|period override/i })).not.toBeInTheDocument();
});
