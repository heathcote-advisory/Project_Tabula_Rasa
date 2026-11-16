import { useQuery } from '@tanstack/react-query';
import type { UseFormReturn } from 'react-hook-form';
import { api } from '../../api/client';
import { resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { ErrorNotice, LoadingState } from '../../components/States';
import { SelectField, TextField } from '../concurrency/Fields';
import { activeChoices } from '../concurrency/useReferenceChoices';
import type { MileageDraft } from './mileageForm';
import { MapsMileageFields } from './MapsMileageFields';
import { VehicleSelector } from './VehicleSelector';
import { FormSection } from '../../components/VisualPrimitives';

export function MileageFields({ form }: { form: UseFormReturn<MileageDraft> }) {
  const session = useEntity();
  const tags = useQuery({ queryKey: resourceKey(session, 'tags'), queryFn: ({ signal }) => api.tags(session, signal) });
  return <><FormSection title="Trip details">
    <TextField form={form} name="trip_date" label="Trip date" type="date" />
    <VehicleSelector form={form} />
    </FormSection>
    <MapsMileageFields key={JSON.stringify(form.formState.defaultValues)} form={form}>
    <FormSection title="Tags">
    {tags.isPending ? <LoadingState label="Loading tags…" /> : tags.isError ? <ErrorNotice error={tags.error} onReview={() => void tags.refetch()} />
      : <SelectField form={form} name="tag_ids" label="Tags" options={activeChoices(tags.data.items, form.formState.defaultValues?.tag_ids ?? [])} multiple />}
    <TextField form={form} name="new_tag_name" label="New tag name (optional)" />
    </FormSection>
    </MapsMileageFields>
    <FormSection title="Purpose and notes">
    <TextField form={form} name="business_purpose" label="Business purpose" multiline />
    <TextField form={form} name="notes" label="Notes" multiline />
    </FormSection></>;
}
