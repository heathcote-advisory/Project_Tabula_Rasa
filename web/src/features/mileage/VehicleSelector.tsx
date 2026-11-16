import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { UseFormReturn } from 'react-hook-form';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../../api/client';
import { useEntity } from '../../app/context';
import { resourceKey } from '../../api/query';
import { ActionButton } from '../../components/VisualPrimitives';
import { ErrorNotice, Field, LoadingState } from '../../components/States';
import { SelectField } from '../concurrency/Fields';
import { activeChoices } from '../concurrency/useReferenceChoices';
import { useProtectedMutation } from '../concurrency/mutations';
import type { MileageDraft } from './mileageForm';

export function VehicleSelector({ form }: { form: UseFormReturn<MileageDraft> }) {
  const session = useEntity();
  const vehicles = useQuery({ queryKey: resourceKey(session, 'vehicles'), queryFn: ({ signal }) => api.vehicles(session, signal) });
  const [name, setName] = useState('');
  const [stopped, setStopped] = useState(false);
  const create = useProtectedMutation(session, async (name: string) => {
    try {
      const vehicle = await api.createVehicle(session, { name, active: true });
      form.setValue('vehicle_id', vehicle.id, { shouldDirty: true }); setName('');
    } catch (failure) {
      if (!(failure instanceof ApiError && ['validation', 'required', 'invariant', 'forbidden', 'unauthorized'].includes(failure.code))) setStopped(true);
      throw failure;
    }
  });
  const choices = activeChoices(vehicles.data?.items ?? [], form.formState.defaultValues?.vehicle_id ? [form.formState.defaultValues.vehicle_id] : []);
  const originals = new Map(vehicles.data?.items.map(row => [row.id, row.name]));
  const counts = new Map<string, number>();
  const comparison = (id: string) => (originals.get(id) ?? '').trim().toLocaleLowerCase();
  for (const choice of choices) counts.set(comparison(choice.id), (counts.get(comparison(choice.id)) ?? 0) + 1);
  const displayChoices = choices.map(choice => ({ ...choice,
    name: (counts.get(comparison(choice.id)) ?? 0) > 1
      ? `${JSON.stringify(originals.get(choice.id))} · ${choice.id}${choice.active ? '' : ' (inactive)'}` : choice.name,
  }));
  return <div className="formSpan">
    {vehicles.isPending ? <LoadingState label="Loading vehicles…" /> : vehicles.isError
      ? <ErrorNotice error={vehicles.error} operation="read" onReview={() => void vehicles.refetch()} />
      : <SelectField controlled form={form} name="vehicle_id" label="Vehicle" hint="Choosing No vehicle clears this trip’s selected vehicle and label." options={[{ id: '', name: 'No vehicle' },
        ...displayChoices]} />}
    {!form.formState.defaultValues?.vehicle_id && form.formState.defaultValues?.vehicle && <p className="notice neutral">
      Original historical label: <code>{JSON.stringify(form.formState.defaultValues.vehicle)}</code>. Choose a managed vehicle to resolve it; the original text is retained.</p>}
    <details className="disclosure"><summary>Add a new vehicle</summary>
      <Field id="inline-vehicle-name" label="New vehicle name"><input id="inline-vehicle-name" value={name} disabled={stopped || create.isPending} onChange={event => setName(event.target.value)} /></Field>
      <ActionButton disabled={stopped || !name.trim()} pending={create.isPending} onClick={() => void create.mutateAsync(name).catch(() => undefined)}>Add vehicle</ActionButton>
      {create.error && <ErrorNotice error={create.error} />}
      {stopped && <p>Creation could not be confirmed. Inspect Vehicle management before adding again.</p>}
    </details>
    <div className="actions"><Link className="button action-secondary" to="/mileage/vehicles">Manage vehicles</Link></div>
  </div>;
}
