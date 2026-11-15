import { compareDecimal, compareText } from '../../components/ordering';
import { useState, type ReactNode } from 'react';
import { FormSection, PageHeader, SectionHeader } from '../../components/VisualPrimitives';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useWatch, type UseFormReturn } from 'react-hook-form';
import { api, type Schemas } from '../../api/client';
import { resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { ErrorNotice, LoadingState, SuccessNotice } from '../../components/States';
import { localDate } from '../../components/localDate';
import { TextField } from '../concurrency/Fields';
import { ProtectedEditor } from '../concurrency/ProtectedEditor';
import { RecordList } from '../concurrency/RecordList';
import { SavedFields } from '../concurrency/SavedFields';
import { useLoadedRecord } from '../concurrency/useLoadedRecord';
import { CreateEditor } from '../workflows/CreateEditor';
import { ExpenseReferences } from './ExpenseReferences';

type Draft = Schemas['MixedUseChanges'];
const initial = (): Draft => ({ txn_date: localDate(), total_expense: '', business_use_percentage: '100', allocation_rationale: '',
  account_id: '', category_id: '', payee_id: '', tag_ids: [], description: '', reference: '', new_payee_name: '', new_tag_name: '' });
const loaded = (row: Schemas['MixedUse']): { metadata: { id: string; baseline: string }; values: Draft } => ({ metadata: { id: row.id, baseline: row.baseline }, values: {
  txn_date: row.txn_date, total_expense: row.total_expense, business_use_percentage: row.business_use_percentage, allocation_rationale: row.allocation_rationale,
  account_id: row.account_id, category_id: row.category_id, payee_id: row.payee_id, tag_ids: row.tag_ids,
  description: row.description, reference: row.reference, new_payee_name: '', new_tag_name: '',
} satisfies Draft });

function AllocationFields({ form, children }: { form: UseFormReturn<Draft>; children?: ReactNode }) {
  return <FormSection title="Expense allocation" description="The ledger records the business allocation of this source expense.">
    <TextField form={form} name="txn_date" label="Date" type="date" />
    <TextField form={form} name="total_expense" label="Total expense" inputMode="decimal" />
    <TextField form={form} name="business_use_percentage" label="Business use %" inputMode="decimal" />
    <TextField form={form} name="allocation_rationale" label="Allocation rationale" multiline />
    {children}
  </FormSection>;
}

function MixedFields({ form, creating = false }: { form: UseFormReturn<Draft>; creating?: boolean }) {
  const session = useEntity();
  const [total, percentage] = useWatch({ control: form.control, name: ['total_expense', 'business_use_percentage'] });
  const valid = /^\d+(\.\d+)?$/.test(total) && /^\d+(\.\d+)?$/.test(percentage);
  const preview = useQuery({ queryKey: [...resourceKey(session, 'mixed-use'), 'allocation', total, percentage],
    queryFn: ({ signal }) => api.previewAllocation(session, { total_expense: total, business_use_percentage: percentage }, signal), enabled: valid, retry: false });
  return <><AllocationFields form={form}>
    <div className="allocationResult">
      <p role="status">Ledger result: <span className="numeric">{valid && preview.data ? preview.data.allocated_amount : 'Enter valid allocation inputs.'}</span></p>
      {valid && preview.isFetching && <LoadingState label="Calculating allocation…" />}
      {valid && preview.isError && <ErrorNotice error={preview.error} />}
    </div>
  </AllocationFields>
    <ExpenseReferences form={form} creating={creating} inline />
  </>;
}

export function MixedUsePage() {
  const session = useEntity();
  return <><PageHeader title="Mixed-use expenses" description="Manage the authoritative source; the ledger receives only its business allocation."
    actions={session.capabilities.includes('bookkeeping:write') && <Link className="button" to="/mixed-use/new">Add mixed-use expense</Link>} />
    <RecordList resource="mixed-use" title="Mixed-use Expense Log" list={api.mixedUse} sorting={{ defaultCompare: (a, b) => compareText(b.txn_date, a.txn_date), record: (a, b) => compareText(a.txn_date, b.txn_date) || compareText(a.description, b.description) }} label={row => row.txn_date + ' · ' + (row.description || row.id)}
      canEdit={() => false} editor={() => null} detailsTarget={row => row.ledger_status === 'linked' ? '/mixed-use/' + encodeURIComponent(row.id) : undefined}
      columns={[{ title: 'Total', compare: (a, b) => compareDecimal(a.total_expense, b.total_expense), className: 'amount', render: row => row.total_expense }, { title: 'Business %', compare: (a, b) => compareDecimal(a.business_use_percentage, b.business_use_percentage), className: 'numeric', render: row => row.business_use_percentage },
        { title: 'Allocated', compare: (a, b) => compareDecimal(a.allocated_amount, b.allocated_amount), className: 'amount', render: row => row.allocated_amount }, { title: 'Rationale', compare: (a, b) => compareText(a.allocation_rationale, b.allocation_rationale), className: 'tableText', render: row => row.allocation_rationale },
        { title: 'Relationship', compare: (a, b) => compareText(a.ledger_status, b.ledger_status), className: 'tableText', render: row => row.ledger_status === 'linked' ? 'Linked · ' + row.linked_transaction_id : 'Inspection required; editing unavailable' },
        { title: 'Schedule', compare: (a, b) => compareText(a.recurring_schedule_id, b.recurring_schedule_id), className: 'tableText', render: row => row.recurring_schedule_id || 'None' }]} />
  </>;
}

export function NewMixedUse() {
  const session = useEntity(); const navigate = useNavigate();
  return <><PageHeader title="Add mixed-use expense" description="Save the source expense and its linked ledger allocation together." />
    {session.capabilities.includes('bookkeeping:write') ?
      <CreateEditor initial={initial} title="Save mixed-use expense" fields={form => <MixedFields form={form} creating />}
        create={(values, id) => api.createMixedUse(session, { ...values, source_id: id, linked_transaction_id: id + ':projection', copied_from_source_id: '' })}
        onClose={() => navigate('/mixed-use')} /> : <p>Read-only access.</p>}</>;
}

export function CopyMixedUse() {
  const { recordId = '' } = useParams();
  return <MixedUseCopyQuery key={recordId} recordId={recordId} />;
}

function MixedUseCopyQuery({ recordId }: { recordId: string }) {
  const session = useEntity(); const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState<Schemas['MixedUseCreate']>();
  const writable = session.capabilities.includes('bookkeeping:write');
  const query = useQuery({ queryKey: [...resourceKey(session, 'mixed-use-copy-draft'), recordId],
    queryFn: ({ signal }) => api.mixedUseCopyDraft(session, recordId, signal), enabled: writable,
    staleTime: 0, refetchOnMount: 'always', refetchOnWindowFocus: false, retry: false });
  if (!snapshot && query.isFetchedAfterMount && query.isSuccess) setSnapshot(structuredClone(query.data));
  if (!writable) return <><PageHeader title="Copy mixed-use expense" /><p>Read-only access.</p></>;
  if (snapshot) return <MixedUseCopyEditor snapshot={snapshot} originalId={recordId} onClose={() => navigate('/mixed-use')} />;
  if (!query.isFetchedAfterMount || query.isFetching) return <><PageHeader title="Copy mixed-use expense" /><LoadingState /></>;
  if (!query.data || query.isError) return <><PageHeader title="Copy mixed-use expense" /><ErrorNotice error={query.error} onReview={() => void query.refetch()} /></>;
  return <LoadingState />;
}

function MixedUseCopyEditor({ snapshot, originalId, onClose }: { snapshot: Schemas['MixedUseCreate']; originalId: string; onClose: () => void }) {
  const session = useEntity(); const [frozen] = useState(() => structuredClone(snapshot));
  const [created, setCreated] = useState(false);
  return <><PageHeader title="Copy mixed-use expense" /><p>Create an independent source from {originalId}. Receipts and schedule links are excluded.</p>
    {created ? <><SuccessNotice>Mixed-use expense copied.</SuccessNotice><button onClick={onClose}>Back to records</button></> :
      <CreateEditor<Draft> prefilledDraft title="Save mixed-use copy" initial={() => loaded({ ...frozen, id: frozen.source_id, baseline: '',
        allocated_amount: '', ledger_status: 'linked', recurring_schedule_id: '', recurring_scheduled_date: '', recurring_occurrence_type: '' }).values}
        uncertainGuidance={<p>The copy may already exist. Inspect the <Link to="/mixed-use">Mixed-use Expense Log</Link> before trying again or starting another copy.</p>}
        fields={form => <MixedFields form={form} creating />} onClose={onClose}
        create={async values => {
          await api.createMixedUse(session, { ...values, source_id: frozen.source_id, linked_transaction_id: frozen.linked_transaction_id,
            copied_from_source_id: frozen.copied_from_source_id });
          setCreated(true);
        }} />}
  </>;
}

export function MixedUseDetail() {
  const { recordId = '' } = useParams();
  return <><Link className="button action-secondary" to="/mixed-use">Back</Link><MixedUseDetailQuery key={recordId} recordId={recordId} /></>;
}

function MixedUseDetailQuery({ recordId }: { recordId: string }) {
  const session = useEntity(); const navigate = useNavigate();
  const query = useLoadedRecord(session, 'mixed-use', recordId, api.mixedUseSource);
  if (query.initializing) return <><PageHeader title="Mixed-use expense" /><LoadingState /></>;
  if (!query.initialized || !query.data) return <><PageHeader title="Mixed-use expense" /><ErrorNotice error={query.error} operation="read" onReview={() => void query.refetch()} /></>;
  const data = query.data;
  return <><PageHeader title="Mixed-use expense" description="Edit the source here. Its ledger allocation is managed with it."
      actions={session.capabilities.includes('bookkeeping:write') && <Link className="button action-secondary" to={'/mixed-use/' + encodeURIComponent(data.id) + '/copy'}>Copy mixed-use expense</Link>} />
    <section className="surface">
      <SectionHeader title="Source and ledger relationship" />
      <dl className="detailSummary">
        <div><dt>Source ID</dt><dd>{data.id}</dd></div>
        <div><dt>Projection ID</dt><dd>{data.linked_transaction_id}</dd></div>
        <div><dt>Allocated amount</dt><dd className="numeric">{data.allocated_amount}</dd></div>
        {data.recurring_schedule_id && <>
          <div><dt>Recurring schedule</dt><dd><Link to={'/recurring/' + encodeURIComponent(data.recurring_schedule_id)}>{data.recurring_schedule_id}</Link></dd></div>
          <div><dt>Schedule date</dt><dd>{data.recurring_scheduled_date}</dd></div>
          <div><dt>Occurrence role</dt><dd>{data.recurring_occurrence_type}</dd></div>
        </>}
      </dl>
    </section>
    <Link className="button action-secondary" to={'/mixed-use/' + encodeURIComponent(data.id) + '/receipts'}>Manage source receipts</Link>
    {session.capabilities.includes('bookkeeping:write') ? <ProtectedEditor topBack={false} key={recordId} session={session} loaded={loaded(data)} title="Edit mixed-use expense"
      fields={form => <MixedFields form={form} />} onClose={() => navigate('/mixed-use')}
      save={async (id, baseline, changes) => loaded(await api.updateMixedUse(session, id, { baseline, changes }))}
      remove={async (id, baseline) => { await api.deleteMixedUse(session, id, baseline); navigate('/mixed-use'); }} loadLatest={async id => loaded(await api.mixedUseSource(session, id))} />
      : <SavedFields title="Saved mixed-use expense" values={loaded(data).values} fields={form => <>
        <AllocationFields form={form} />
        <ExpenseReferences form={form} />
      </>} />}
  </>;
}
