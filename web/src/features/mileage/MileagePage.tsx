import { FilterPanel } from '../../components/FilterPanel';
import { ActionButton, PageHeader, SectionHeader, TableRegion } from '../../components/VisualPrimitives';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, type Schemas } from '../../api/client';
import { resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { EmptyState, ErrorNotice, Field, LoadingState, SuccessNotice } from '../../components/States';
import { UnsavedChanges } from '../../components/UnsavedChanges';
import { ProtectedEditor } from '../concurrency/ProtectedEditor';
import { useProtectedMutation } from '../concurrency/mutations';
import { useLoadedRecord } from '../concurrency/useLoadedRecord';
import { MileageFields } from './MileageFields';
import { mileageLoaded, mileagePayload, type MileageDraft } from './mileageForm';
import { mileageCsvCell } from './mileageCsv';
import { downloadBlob } from '../../components/downloadBlob';
import { localDate } from '../../components/localDate';

const tripLabel = (row: Schemas['Mileage']) => `${row.trip_date} · ${row.destination} · ${row.miles} miles`;

export function MileagePage() {
  const session = useEntity();
  const navigate = useNavigate();
  const location = useLocation();
  const origin = location.state?.mileageContext;
  const restored = origin?.scope === JSON.stringify([session.cache_scope, session.entity.id]) ? origin : undefined;
  const [search, setSearch] = useState<string>(restored?.search ?? '');
  const [start, setStart] = useState<string>(restored?.start ?? '');
  const [end, setEnd] = useState<string>(restored?.end ?? '');
  const [tagIds, setTagIds] = useState<string[]>(restored?.tagIds ?? []);
  const trips = useQuery({ queryKey: resourceKey(session, 'mileage'), queryFn: ({ signal }) => api.mileage(session, signal) });
  const tags = useQuery({ queryKey: resourceKey(session, 'tags'), queryFn: ({ signal }) => api.tags(session, signal) });
  const names = new Map(tags.data?.items.map(tag => [tag.id, tag.name]));
  const rows = (trips.data?.items ?? []).filter(row => (!start || row.trip_date >= start) && (!end || row.trip_date <= end)
    && (!tagIds.length || row.tag_ids.some(id => tagIds.includes(id)))
    && [row.id, row.destination, row.start_location, row.business_purpose, row.vehicle, row.notes, ...row.tag_ids.map(id => names.get(id) ?? id)].some(value => value.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())));
  function download() {
    const data = [['ID', 'Date', 'Miles', 'From', 'Destination', 'Business purpose', 'Vehicle', 'Tags', 'Notes'],
      ...rows.map(row => [row.id, row.trip_date, row.miles, row.start_location, row.destination, row.business_purpose,
        row.vehicle, row.tag_ids.map(id => names.get(id) ?? id).join('; '), row.notes])];
    downloadBlob(new Blob([data.map(row => row.map(mileageCsvCell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }), 'tabula-rasa-mileage.csv');
  }
  return <><PageHeader title="Mileage" description="Record business travel and review your mileage log." />
    <FilterPanel name="mileage" title="View & edit mileage log" label="Mileage filters" summary={[
      search && `Search: ${search}`, start && `From ${start}`, end && `To ${end}`, tagIds.length > 0 && `${tagIds.length} tags`,
    ].filter(Boolean).join(' · ') || 'All trips'}><p className="fieldHint">Search, dates and tags apply to the log, totals and CSV download.</p><div className="formGrid">
      <div className="formSpan"><Field id="mileage-search" label="Search mileage"><input id="mileage-search" value={search} onChange={event => setSearch(event.target.value)} /></Field></div>
      <Field id="from" label="From"><input id="from" type="date" value={start} onChange={e => setStart(e.target.value)} /></Field>
      <Field id="to" label="To"><input id="to" type="date" value={end} onChange={e => setEnd(e.target.value)} /></Field>
      {tags.isPending ? <LoadingState label="Loading tags…" /> : tags.isError ? <ErrorNotice error={tags.error} operation="read" onReview={() => void tags.refetch()} />
        : <Field id="filter-tags" label="Filter by tags"><select id="filter-tags" multiple value={tagIds}
          aria-describedby="mileage-tag-hint mileage-tag-selection" onChange={e => setTagIds(Array.from(e.target.selectedOptions, option => option.value))}>
          {tags.data.items.map(tag => <option key={tag.id} value={tag.id}>{tag.name}</option>)}
        </select><p id="mileage-tag-hint" className="fieldHint">Use Ctrl or Command with click, or Shift with arrow keys, to select multiple tags.</p>
          <p id="mileage-tag-selection" className="fieldHint">Selected: {tagIds.length ? tagIds.map(id => names.get(id) ?? id).join(', ') : 'All tags'}</p></Field>}
    </div><ActionButton onClick={() => { setSearch(''); setStart(''); setEnd(''); setTagIds([]); }}>Clear filters</ActionButton></FilterPanel>
    {trips.isPending ? <LoadingState label="Loading mileage…" /> : trips.isError ? <ErrorNotice error={trips.error} operation="read" onReview={() => void trips.refetch()} /> : <>
      <p role="status">Trips: {rows.length} · Business miles: {(rows.reduce((sum, row) => sum + Math.round(Number(row.miles) * 10), 0) / 10).toFixed(1)}</p>
      <div className="actions tableToolbar"><ActionButton disabled={!rows.length} onClick={download}>Download filtered mileage CSV</ActionButton></div>
      {!rows.length ? <EmptyState title="No mileage trips">Add a trip or adjust the filters.</EmptyState> :
        <TableRegion label="Mileage log"><table><caption>Business mileage</caption>
          <thead><tr>{['Date', 'Miles', 'From', 'Destination', 'Business purpose', 'Vehicle', 'Tags', 'Notes', 'Actions'].map(label => <th key={label} scope="col" className={label === 'Miles' ? 'amount' : label === 'Actions' ? 'tableActions' : undefined}>{label}</th>)}</tr></thead>
          <tbody>{rows.map(row => <tr key={row.id}><th scope="row">{row.trip_date}</th><td className="amount">{row.miles}</td><td className="tableText">{row.start_location}</td>
            <td className="tableText">{row.destination}</td><td className="tableText">{row.business_purpose}</td><td className="tableText">{row.vehicle}</td><td className="tableText">{row.tag_ids.map(id => names.get(id) ?? id).join(', ')}</td><td className="tableText">{row.notes}</td>
            <td className="tableActions"><div className="rowActions"><ActionButton aria-label={(session.capabilities.includes('bookkeeping:write') ? 'Edit ' : 'View ') + tripLabel(row)} onClick={() => navigate('/mileage/' + encodeURIComponent(row.id), { state: { mileageContext: { scope: JSON.stringify([session.cache_scope, session.entity.id]), search, start, end, tagIds } } })}>
              {session.capabilities.includes('bookkeeping:write') ? 'Edit' : 'View'}</ActionButton></div></td></tr>)}</tbody>
        </table></TableRegion>}
    </>}
  </>;
}

export function NewMileage() {
  const session = useEntity();
  const form = useForm<MileageDraft>({ defaultValues: { trip_date: localDate(),
    miles: '', vehicle: '', vehicle_id: '', start_location: '', destination: '', business_purpose: '', notes: '', tag_ids: [], new_tag_name: '', round_trip: false, maps_reviewed: false } });
  const [failure, setFailure] = useState<unknown>();
  const [created, setCreated] = useState<Schemas['Mileage'][]>([]);
  const [uncertain, setUncertain] = useState(false);
  const mutation = useProtectedMutation(session, async (values: MileageDraft) => {
    const response = await api.createMileage(session, { ...mileagePayload(values), round_trip: !!values.round_trip, return_miles: values.round_trip ? values.return_miles : null });
    setCreated(response.items); form.reset(values);
  });
  if (!session.capabilities.includes('bookkeeping:write')) return <ErrorNotice error={new ApiError('forbidden')} />;
  return <><PageHeader title="Add trip" /><UnsavedChanges dirty={form.formState.isDirty} />
    {created.length ? <><SuccessNotice>Mileage saved: {created.length} {created.length === 1 ? 'trip' : 'trips'}.</SuccessNotice>
      {created.map(row => <p key={row.id}><Link to={'/mileage/' + encodeURIComponent(row.id)}>{tripLabel(row)}</Link></p>)}</> :
      <form className="surface editor" onSubmit={form.handleSubmit(async values => {
        setFailure(undefined);
        try { await mutation.mutateAsync(values); } catch (error) {
          setFailure(error); setUncertain(!(error instanceof ApiError && ['validation', 'reference', 'required', 'invariant', 'forbidden', 'unauthorized'].includes(error.code)));
        }
      })}>
        {failure !== undefined && <ErrorNotice error={failure} />}
        {uncertain && <p role="alert" className="notice warning">Save status is uncertain. Review the mileage log before starting another entry.</p>}
        <fieldset disabled={mutation.isPending || uncertain}>
          <label className="checkbox"><input type="checkbox" {...form.register('round_trip', { onChange: () => {
            if (form.getValues('round_trip')) form.setValue('return_miles', form.getValues('miles'));
          } })} /> Round trip</label>
          <p className="muted">A round trip saves two independent trips with reversed locations. Review both distances before saving.</p>
          <MileageFields form={form} />
        </fieldset>
        <div className="actions formActions"><ActionButton type="submit" tone="primary" pending={mutation.isPending} disabled={uncertain}>Save mileage trip</ActionButton>
          <Link className="button action-secondary" to="/mileage">Back to records</Link></div>
        {mutation.isPending && <LoadingState label="Saving mileage…" />}
      </form>}
  </>;
}

export function MileageDetail() {
  const { recordId = '' } = useParams();
  const location = useLocation();
  return <><Link className="button action-secondary" to="/mileage" state={location.state}>Back</Link><MileageDetailQuery key={recordId} recordId={recordId} /></>;
}

function MileageDetailQuery({ recordId }: { recordId: string }) {
  const location = useLocation();
  const session = useEntity();
  const navigate = useNavigate();
  const tags = useQuery({ queryKey: resourceKey(session, 'tags'), queryFn: ({ signal }) => api.tags(session, signal) });
  const query = useLoadedRecord(session, 'mileage', recordId, api.mileageTrip);
  return <><PageHeader title="Mileage trip" />{query.initializing ? <LoadingState />
    : !query.initialized || !query.data ? <ErrorNotice error={query.error} operation="read" onReview={() => void query.refetch()} />
    : session.capabilities.includes('bookkeeping:write') ? <ProtectedEditor topBack={false} key={recordId} session={session} loaded={mileageLoaded(query.data)} title="Edit mileage trip"
      onClose={() => navigate('/mileage', { state: location.state })} fields={form => <MileageFields form={form} />}
      save={async (id, baseline, changes) => mileageLoaded(await api.updateMileage(session, id, { baseline, changes: mileagePayload(changes) }))}
      remove={(id, baseline) => api.deleteMileage(session, id, baseline)}
      loadLatest={async id => mileageLoaded(await api.mileageTrip(session, id))} />
    : <section className="surface"><SectionHeader title={tripLabel(query.data)} description="Read-only mileage record." /><dl className="detailSummary">
      {([['Trip date', query.data.trip_date], ['Vehicle', query.data.vehicle], ['Business miles', query.data.miles],
        ['Starting location', query.data.start_location], ['Destination', query.data.destination], ['Business purpose', query.data.business_purpose],
        ['Notes', query.data.notes], ['Tags', query.data.tag_ids.map(id => tags.data?.items.find(tag => tag.id === id)?.name ?? 'Unavailable tag').join(', ')]])
        .map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || 'None'}</dd></div>)}
    </dl><Link className="button action-secondary" to="/mileage" state={location.state}>Back to records</Link></section>}
  </>;
}
