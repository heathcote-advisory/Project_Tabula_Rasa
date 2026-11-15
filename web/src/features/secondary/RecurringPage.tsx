import { ActionButton, FormSection, PageHeader, SectionHeader, TableRegion } from '../../components/VisualPrimitives';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import type { Path, UseFormReturn } from 'react-hook-form';
import { api, type Schemas } from '../../api/client';
import { useEntity } from '../../app/context';
import { ConfirmationDialog } from '../../components/ConfirmationDialog';
import { ErrorNotice, Field, LoadingState, SuccessNotice } from '../../components/States';
import { localDate } from '../../components/localDate';
import { SelectField, TextField } from '../concurrency/Fields';
import { ProtectedEditor } from '../concurrency/ProtectedEditor';
import { RecordList } from '../concurrency/RecordList';
import { SavedFields } from '../concurrency/SavedFields';
import { useLoadedRecord } from '../concurrency/useLoadedRecord';
import { useProtectedMutation } from '../concurrency/mutations';
import { CreateEditor } from '../workflows/CreateEditor';
import { ExpenseReferences } from './ExpenseReferences';

type Changes = Schemas['RecurringChanges'];
type Draft = Changes & { start_date: string; source_kind: string };
const initial = (): Draft => ({ name: '', start_date: localDate(), source_kind: 'transaction', recurrence_unit: 'month', recurrence_interval: '1',
  end_date: '', amount: '', total_expense: '', business_use_percentage: '100', allocation_rationale: '', account_id: '', category_id: '', payee_id: '', tag_ids: [], description: '', reference: '' });
const loaded = (row: Schemas['Recurring']) => ({ metadata: { id: row.id, baseline: row.baseline }, values: {
  name: row.name, recurrence_unit: row.recurrence_unit, recurrence_interval: row.recurrence_interval, end_date: row.end_date,
  amount: row.amount, total_expense: row.total_expense, business_use_percentage: row.business_use_percentage, allocation_rationale: row.allocation_rationale,
  account_id: row.account_id, category_id: row.category_id, payee_id: row.payee_id, tag_ids: row.tag_ids, description: row.description, reference: row.reference,
} satisfies Changes });

function RecurringFields<T extends Changes>({ form, creating = false, mixed = false }: { form: UseFormReturn<T>; creating?: boolean; mixed?: boolean }) {
  const path = (name: string) => name as Path<T>;
  return <>
    <FormSection title="Schedule" description="Templates affect future occurrences; existing expenses retain their saved values and provenance.">
      <TextField form={form} name={path('name')} label="Schedule name" />
      {creating && <><TextField form={form} name={path('start_date')} label="Start date" type="date" />
        <SelectField form={form} name={path('source_kind')} label="Expense source" options={[{ id: 'transaction', name: 'Ordinary expense' }, { id: 'mixed_use', name: 'Mixed-use expense' }]} /></>}
      <RecurrenceFields form={form} />
    </FormSection>
    <FormSection title="Expense template">
      {(creating ? form.watch(path('source_kind')) === 'mixed_use' : mixed) ? <>
        <TextField form={form} name={path('total_expense')} label="Total expense" inputMode="decimal" />
        <TextField form={form} name={path('business_use_percentage')} label="Business use %" inputMode="decimal" />
        <TextField form={form} name={path('allocation_rationale')} label="Allocation rationale" multiline />
      </> : <TextField form={form} name={path('amount')} label="Amount" inputMode="decimal" />}
    </FormSection>
    <ExpenseReferences form={form} creating={creating} />
  </>;
}

export function RecurrenceFields<T extends { recurrence_unit: string; recurrence_interval: string; end_date: string }>({ form }: { form: UseFormReturn<T> }) {
  const path = (name: string) => name as Path<T>;
  return <><SelectField form={form} name={path('recurrence_unit')} label="Repeat unit" options={['day', 'week', 'month', 'year'].map(id => ({ id, name: id }))} />
    <TextField form={form} name={path('recurrence_interval')} label="Repeat every" inputMode="numeric" />
    <TextField form={form} name={path('end_date')} label="End date (optional)" type="date" /></>;
}

function GenerateOccurrences() {
  const session = useEntity(); const [through, setThrough] = useState(localDate); const [confirm, setConfirm] = useState(false);
  const [result, setResult] = useState<Schemas['ReconcileResult']>(); const [failure, setFailure] = useState<unknown>();
  const mutation = useProtectedMutation(session, () => api.reconcileRecurring(session, { through_date: through }));
  async function generate() {
    setResult(undefined); setFailure(undefined);
    try { setResult(await mutation.mutateAsync()); } catch (error) { setFailure(error); }
    finally { setConfirm(false); }
  }
  return <section className="surface commandSection">
    <SectionHeader title="Generate due occurrences" description="Request due occurrences through the selected date. Page visits do not generate expenses." />
    <div className="formGrid"><Field id="recurring-through" label="Generate through"><input id="recurring-through" type="date" value={through} disabled={mutation.isPending}
      onChange={event => setThrough(event.target.value)} /></Field></div>
    <div className="actions"><ActionButton pending={mutation.isPending} disabled={!through} onClick={event => { event.currentTarget.focus(); setConfirm(true); }}>Generate due occurrences</ActionButton></div>
    {result && <SuccessNotice>{result.execution_mode === 'worker' ? `Queued ${result.queued_count ?? 0} schedule(s). Generation runs in the background; refresh later to inspect occurrences and attention.` : `Generated ${result.generated_count} occurrence(s). ${result.attention_count} schedule(s) require attention.`}</SuccessNotice>}
    {failure !== undefined && <ErrorNotice error={failure} />}
    {confirm && <ConfirmationDialog title="Generate due occurrences" keepLabel="Keep records" confirmLabel="Confirm generation" pending={mutation.isPending}
      onKeep={() => setConfirm(false)} onConfirm={() => void generate()}>Generate due expenses through {through} using the current schedule templates?</ConfirmationDialog>}
  </section>;
}

export function RecurringPage() {
  const session = useEntity();
  return <><PageHeader title="Recurring expenses" description="Templates affect future occurrences. Existing expenses retain their provenance."
    actions={session.capabilities.includes('bookkeeping:write') && <Link className="button" to="/recurring/new">Add recurring expense</Link>} />
    <RecordList resource="recurring" title="Recurring schedules" list={api.recurring} label={row => row.name} canEdit={() => false} editor={() => null}
      detailsTarget={row => '/recurring/' + encodeURIComponent(row.id)} columns={[
        { title: 'Cadence', render: row => row.recurrence_interval + ' ' + row.recurrence_unit }, { title: 'Next due', render: row => row.next_due_date || 'None' },
        { title: 'Latest occurrence', render: row => row.latest_current_occurrence_date || 'None' }, { title: 'Status', render: row => <span className="recordBadge">{row.status}</span> },
        { title: 'Attention', className: 'tableText', render: row => row.attention_message || 'None' }, { title: 'ID', className: 'ledgerIdentity', render: row => row.id }]} />
    {session.capabilities.includes('bookkeeping:write') && <GenerateOccurrences />}
  </>;
}

export function NewRecurring() {
  const session = useEntity(); const navigate = useNavigate();
  return <><PageHeader title="Add recurring expense" description="Save creates the schedule and its initial occurrence together." />
    {session.capabilities.includes('bookkeeping:write') ? <CreateEditor title="Save recurring expense" initial={initial}
      fields={form => <RecurringFields form={form} creating />} create={(values, id) => api.createRecurring(session, { ...values, schedule_id: id })}
      onClose={() => navigate('/recurring')} /> : <p>Read-only access.</p>}</>;
}

export function RecurringDetail() {
  const { recordId = '' } = useParams();
  return <><Link className="button action-secondary" to="/recurring">Back</Link><RecurringDetailQuery key={recordId} recordId={recordId} /></>;
}

function RecurringDetailQuery({ recordId }: { recordId: string }) {
  const session = useEntity(); const navigate = useNavigate();
  const query = useLoadedRecord(session, 'recurring', recordId, api.recurringSchedule);
  if (query.initializing) return <><PageHeader title="Recurring schedule" /><LoadingState /></>;
  if (!query.initialized || !query.data) return <><PageHeader title="Recurring schedule" /><ErrorNotice error={query.error} operation="read" onReview={() => void query.refetch()} /></>;
  const data = query.data;
  return <><PageHeader title="Recurring schedule" description="Manage future generation while retaining each occurrence's saved provenance." />
    <section className="surface">
      <SectionHeader title={data.schedule.name} />
      <dl className="detailSummary">
        <div><dt>Status</dt><dd>{data.schedule.status}</dd></div>
        <div><dt>Schedule ID</dt><dd>{data.schedule.id}</dd></div>
        <div><dt>Start date</dt><dd>{data.schedule.start_date}</dd></div>
        <div><dt>Expense source</dt><dd>{data.schedule.source_kind === 'mixed_use' ? 'Mixed-use expense' : 'Ordinary expense'}</dd></div>
      </dl>
      {data.schedule.attention_message && <p role="alert" className="notice warning">{data.schedule.attention_message}</p>}
    </section>
    <section className="reportSection">
      <SectionHeader title="Generated occurrences" description="Existing expenses retain their dates, amounts and initial/generated roles when the template changes." />
      <TableRegion label="Generated occurrences"><table><caption>Current occurrences and provenance</caption>
        <thead><tr><th scope="col">Date</th><th scope="col" className="amount">Amount</th><th scope="col">Role</th>
          <th scope="col">Schedule date</th><th scope="col">Source</th><th scope="col">Entry ID</th></tr></thead>
        <tbody>{data.occurrences.length === 0 ? <tr><td className="tableEmpty" colSpan={6}>No current occurrences to show.</td></tr> : data.occurrences.map(row => <tr key={row.id}>
          <th scope="row">{row.txn_date}</th><td className="amount">{row.amount}</td><td>{row.recurring_occurrence_type}</td>
          <td>{row.recurring_scheduled_date}</td><td className="tableText">{row.source_id || 'Ordinary expense'}</td><td className="tableText">{row.id}</td>
        </tr>)}</tbody></table></TableRegion>
    </section>
    {session.capabilities.includes('bookkeeping:write') ? <ProtectedEditor topBack={false} key={recordId} session={session} loaded={loaded(data.schedule)} title="Edit recurring schedule"
      fields={form => <RecurringFields form={form} mixed={data.schedule.source_kind === 'mixed_use'} />} onClose={() => navigate('/recurring')}
      remove={data.can_delete ? (id, baseline) => api.deleteRecurring(session, id, baseline) : undefined}
      removeDescription="Permanently delete this recurrence template and detach its original expense. The original entry and its receipts are retained as an ordinary editable record. Future generation stops."
      save={async (id, baseline, changes) => loaded(await api.updateRecurring(session, id, { baseline, changes }))}
      loadLatest={async id => loaded((await api.recurringSchedule(session, id)).schedule)}
      commands={(data.schedule.status === 'ended' ? [] : [data.schedule.status === 'active' ? 'pause' : 'resume', 'end'] as const).map(transition => ({
        label: transition[0].toUpperCase() + transition.slice(1) + ' schedule', description: transition === 'end' ? 'End future generation permanently; retain this schedule and its occurrences.' : transition === 'pause' ? 'Pause future generation.' : 'Resume future generation.',
        run: async (id, baseline) => { await api.transitionRecurring(session, id, baseline, transition); return 'Schedule ' + transition + ' completed.'; },
      }))} /> : <SavedFields title="Saved recurring schedule" values={loaded(data.schedule).values}
        fields={form => <RecurringFields form={form} mixed={data.schedule.source_kind === 'mixed_use'} />} />}
  </>;
}
