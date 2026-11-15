import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { api, ApiError } from '../../api/client';
import { createTestRouter } from '../../app/router';
import { MileagePage } from './MileagePage';
import { ScopeBoundary } from '../../app/ScopeBoundary';
import { session } from '../../test/fixtures';

it('lets read-only users inspect mileage with familiar field and tag labels', async () => {
  vi.spyOn(api, 'session').mockResolvedValue({ ...session, capabilities: ['bookkeeping:read'] });
  vi.spyOn(api, 'tags').mockResolvedValue({ items: [{ id: 'tag', name: 'Site work', active: true, baseline: 'tag-token', created_at: '' }] });
  vi.spyOn(api, 'mileageTrip').mockResolvedValue({ id: 'trip', trip_date: '2026-10-08', miles: '12.4', vehicle: 'Van',
    start_location: 'Office', destination: 'Site', business_purpose: 'Inspection', notes: 'Reviewed', tag_ids: ['tag'], created_at: '', baseline: 'opaque' });
  render(<RouterProvider router={createTestRouter(['/mileage/trip'])} />);
  expect(await screen.findByText('Inspection')).toBeVisible();
  expect(await screen.findByText('Site work')).toBeVisible();
  expect(screen.getByText('Business miles')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
});

it('retains an uncertain create draft and prevents another submission until the log is inspected', async () => {
  vi.spyOn(api, 'session').mockResolvedValue(session);
  vi.spyOn(api, 'tags').mockResolvedValue({ items: [] });
  const create = vi.spyOn(api, 'createMileage').mockRejectedValue(new ApiError('unavailable'));
  render(<RouterProvider router={createTestRouter(['/mileage/new'])} />);
  const purpose = await screen.findByLabelText('Business purpose');
  expect(screen.queryByRole('checkbox', { name: 'I reviewed the recorded locations and mileage' })).not.toBeInTheDocument();
  await userEvent.type(purpose, 'Inspection draft');
  await userEvent.type(screen.getByLabelText('Business miles'), '12.4');
  await userEvent.type(screen.getByLabelText('Starting location'), 'Office');
  await userEvent.type(screen.getByLabelText('Destination'), 'Site');
  await userEvent.click(screen.getByRole('button', { name: 'Save mileage trip' }));
  expect(await screen.findByText(/Save status is uncertain/)).toBeVisible();
  expect(purpose).toHaveValue('Inspection draft');
  expect(screen.getByRole('button', { name: 'Save mileage trip' })).toBeDisabled();
  expect(create).toHaveBeenCalledTimes(1);
});

it('searches hidden mileage IDs, exports IDs and restores the filtered log after guarded Back', async () => {
  vi.spyOn(api, 'session').mockResolvedValue(session);
  vi.spyOn(api, 'tags').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'reports').mockRejectedValue(new ApiError('unavailable'));
  const row = { id: 'stable-trip-id', trip_date: '2026-10-08', miles: '12.4', vehicle: 'Van', start_location: 'Office',
    destination: 'Site', business_purpose: 'Inspection', notes: '', tag_ids: [], created_at: '', baseline: 'loaded' };
  vi.spyOn(api, 'mileage').mockResolvedValue({ items: [row, { ...row, id: 'other-trip', destination: 'Other site' }] });
  vi.spyOn(api, 'mileageTrip').mockResolvedValue(row);
  const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:synthetic');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  render(<RouterProvider router={createTestRouter(['/mileage'])} />);
  await screen.findByRole('region', { name: 'Mileage log' });
  await userEvent.type(screen.getByLabelText('Search mileage'), 'stable-trip-id');
  expect(screen.getByRole('status', { name: '' })).toHaveTextContent('Trips: 1');
  expect(screen.queryByRole('columnheader', { name: 'ID' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Download filtered mileage CSV' }));
  const blob = createUrl.mock.calls[0][0] as Blob;
  const csv = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result as string); reader.readAsText(blob); });
  expect(csv).toContain('"ID","Date","Miles"'); expect(csv).toContain('stable-trip-id'); expect(csv).not.toContain('other-trip');
  await userEvent.click(screen.getByRole('button', { name: /^Edit / }));
  await userEvent.type(await screen.findByLabelText('Business purpose'), ' draft');
  await userEvent.click(screen.getByRole('link', { name: /^Back$/ }));
  expect(await screen.findByRole('alertdialog', { name: 'Unsaved changes' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(screen.getByLabelText('Business purpose')).toHaveValue('Inspection draft');
  await userEvent.click(screen.getByRole('link', { name: /^Back$/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Discard draft and leave' }));
  await waitFor(() => expect(screen.getByLabelText('Search mileage')).toHaveValue('stable-trip-id'));
});

it.each(['alpha', 'beta'])('restores mileage context only for its original business: %s', async entityId => {
  const scoped = { ...session, entity: { ...session.entity, id: entityId } };
  vi.spyOn(api, 'tags').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'mileage').mockResolvedValue({ items: [{ id: entityId + '-trip', trip_date: '2026-10-08', miles: '1.0', vehicle: '',
    start_location: '', destination: 'Site', business_purpose: '', notes: '', tag_ids: [], created_at: '', baseline: 'loaded' }] });
  const router = createMemoryRouter([{ path: '/mileage', element: <ScopeBoundary session={scoped}><MileagePage /></ScopeBoundary> }], {
    initialEntries: [{ pathname: '/mileage', state: { mileageContext: { scope: JSON.stringify([session.cache_scope, 'alpha']), search: 'alpha-trip', start: '', end: '', tagIds: [] } } }],
  });
  render(<RouterProvider router={router} />);
  await screen.findByRole('region', { name: 'Mileage log' });
  expect(screen.getByLabelText('Search mileage')).toHaveValue(entityId === 'alpha' ? 'alpha-trip' : '');
  expect(api.mileage).toHaveBeenCalledWith(scoped, expect.any(AbortSignal));
});
