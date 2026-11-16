import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useForm } from 'react-hook-form';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../api/client';
import { ScopeBoundary } from '../../app/ScopeBoundary';
import { session } from '../../test/fixtures';
import { VehicleSelector } from './VehicleSelector';
import type { MileageDraft } from './mileageForm';

afterEach(() => vi.restoreAllMocks());
const van = { id: 'van', name: 'Van', active: true, created_at: '', baseline: 'van-baseline' };
const old = { ...van, id: 'old', name: 'Old car', active: false };

function Form({ selected = '' }: { selected?: string }) {
  const form = useForm<MileageDraft>({ defaultValues: { vehicle_id: selected } });
  return <VehicleSelector form={form} />;
}
function show(selected = '') {
  return render(<MemoryRouter><ScopeBoundary session={session}><Form selected={selected} /></ScopeBoundary></MemoryRouter>);
}

it('hides inactive vehicles for new trips and retains the current inactive selection on edit', async () => {
  vi.spyOn(api, 'vehicles').mockResolvedValue({ items: [van, old] });
  const view = show();
  expect(await screen.findByLabelText('Vehicle')).toHaveValue('');
  expect(screen.getByRole('option', { name: 'Van' })).toBeVisible();
  expect(screen.queryByRole('option', { name: /Old car/ })).not.toBeInTheDocument();
  view.unmount();
  show('old');
  expect(await screen.findByLabelText('Vehicle')).toHaveValue('old');
  expect(screen.getByRole('option', { name: /Old car/ })).toBeVisible();
});

it('creates and selects a managed vehicle without leaving the mileage draft', async () => {
  const vehicle = { ...van, id: 'new', name: 'New van' };
  vi.spyOn(api, 'vehicles').mockResolvedValueOnce({ items: [van] }).mockResolvedValue({ items: [van, vehicle] });
  const create = vi.spyOn(api, 'createVehicle').mockResolvedValue(vehicle);
  show();
  await screen.findByLabelText('Vehicle');
  fireEvent.click(screen.getByText('Add a new vehicle'));
  fireEvent.change(screen.getByLabelText('New vehicle name'), { target: { value: 'New van' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add vehicle' }));
  await screen.findByRole('option', { name: 'New van' });
  expect(screen.getByLabelText('Vehicle')).toHaveValue('new');
  expect(create).toHaveBeenCalledExactlyOnceWith(session, { name: 'New van', active: true });
});

it('distinguishes similar historical labels by exact spelling and stable reference', async () => {
  vi.spyOn(api, 'vehicles').mockResolvedValue({ items: [van, { ...van, id: 'padded', name: ' Van ' }] });
  show();
  expect(await screen.findByRole('option', { name: '"Van" · van' })).toBeVisible();
  expect(screen.getByRole('option', { name: '" Van " · padded' })).toBeVisible();
});
