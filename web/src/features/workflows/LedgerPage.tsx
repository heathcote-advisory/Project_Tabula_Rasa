import { FilterPanel } from '../../components/FilterPanel';
import { DateRangeFields } from '../../components/DateRangeFields';
import { ActionButton, PageHeader } from '../../components/VisualPrimitives';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api, type Schemas } from '../../api/client';
import { resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { EmptyState, ErrorNotice, Field, LoadingState, SuccessNotice } from '../../components/States';
import { JournalEditor, TransactionEditor } from '../concurrency/BookkeepingEditors';
import { latest } from '../concurrency/mutations';
import { useReferenceChoices } from '../concurrency/useReferenceChoices';
import { LedgerTable } from './LedgerTable';
import { LedgerBulkActions } from './LedgerBulkActions';
import { TransactionCopyEditor } from './TransactionCopyEditor';

type Selection = { transaction: Schemas['Transaction'] } | { journal: Schemas['Journal'] };
const sortFields = { Date: 'txn_date', Type: 'kind', Account: 'account', Payee: 'payee', Amount: 'amount', Description: 'description', ID: 'record_id' } as const;
type Kind = 'All' | 'Income' | 'Expense' | 'Transfer' | 'Journal';

export function LedgerPage() {
  const session = useEntity();
  const choices = useReferenceChoices(session);
  const [filters, setFilters] = useState<{ start?: string; end?: string; kind: Kind; account_ids: string[]; category_ids: string[]; tag_ids: string[] }>({ kind: 'All', account_ids: [], category_ids: [], tag_ids: [] });
  const [search, setSearch] = useState('');
  const [sortField, setSortField] = useState<keyof typeof sortFields>('Date');
  const [descending, setDescending] = useState(true);
  const [selected, setSelected] = useState<Selection>();
  const [loadingRecord, setLoadingRecord] = useState(false);
  const [failure, setFailure] = useState<unknown>();
  const [bulkRecords, setBulkRecords] = useState<Schemas['Transaction'][]>();
  const [bulkSelection, setBulkSelection] = useState<{ scope: string; ids: string[] }>({ scope: '', ids: [] });
  const [bulkReview, setBulkReview] = useState(false);
  const [submittedSelection, setSubmittedSelection] = useState<Schemas['LedgerItem'][]>([]);
  const [bulkResult, setBulkResult] = useState<string>();
  const [ledgerRevision, setLedgerRevision] = useState(0);
  const [copy, setCopy] = useState<{ draft: Schemas['TransactionChanges']; sourceId: string }>();
  const query = useQuery({ queryKey: [...resourceKey(session, 'ledger'), filters], queryFn: ({ signal }) => api.ledger(session, filters, signal) });
  const selectionScope = JSON.stringify([filters, search, sortField, descending]);
  // A changed view/revision discards the stored selection, so restoring a filter
  // cannot resurrect it and a refreshed ledger cannot mutate a silent subset.
  if (bulkSelection.ids.length && bulkSelection.scope !== selectionScope) {
    setBulkSelection({ scope: selectionScope, ids: [] });
    setBulkReview(false); setSubmittedSelection([]);
  }
  if (ledgerRevision !== query.dataUpdatedAt) {
    setLedgerRevision(query.dataUpdatedAt);
    // Keep a submitted selection and its draft/error for explicit review after
    // invalidation. Ordinary background refreshes still invalidate selections.
    if (!bulkReview && bulkSelection.ids.length) setBulkSelection({ scope: selectionScope, ids: [] });
  }
  const selectedIds = bulkSelection.scope === selectionScope ? bulkSelection.ids : [];
  async function startBulk() {
    setLoadingRecord(true); setFailure(undefined); setBulkReview(false); setBulkResult(undefined);
    try {
      const loaded = (await api.transactions(session)).items;
      // Refresh the displayed rows after loading baselines, before selection is
      // enabled. A later concurrent edit still fails the original baseline.
      const refreshed = await query.refetch();
      if (refreshed.isError) throw refreshed.error;
      setBulkRecords(loaded); setBulkSelection({ scope: '', ids: [] });
    }
    catch (error) { setFailure(error); }
    finally { setLoadingRecord(false); }
  }
  async function edit(row: Schemas['LedgerItem']) {
    setLoadingRecord(true); setFailure(undefined);
    try {
      setSelected(row.entry_type === 'journal' ? { journal: await latest(api.journals, session, row.record_id) }
        : { transaction: await latest(api.transactions, session, row.record_id) });
    } catch (error) { setFailure(error); }
    finally { setLoadingRecord(false); }
  }
  const rows = query.data?.items.filter(row => !search.trim() || [row.kind, row.account, row.to_account, row.category, row.payee, ...row.tags,
    row.description, row.reference, row.id, row.record_id, ...row.postings.flatMap(line => [line.target, line.payee, line.description])]
    .some(text => text.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))) ?? [];
  rows.sort((a, b) => {
    const field = sortFields[sortField];
    const left = field === 'amount' ? BigInt(a.amount.replace('.', '')) : a[field].toLocaleLowerCase();
    const right = field === 'amount' ? BigInt(b.amount.replace('.', '')) : b[field].toLocaleLowerCase();
    const compared = left < right ? -1 : left > right ? 1 : 0;
    return descending ? -compared : compared;
  });
  const selectedTargets = [...choices.accounts.filter(row => filters.account_ids.includes(row.id)).map(row => row.name),
    ...choices.categories.filter(row => filters.category_ids.includes(row.id)).map(row => `${row.kind} category · ${row.name}`)];
  const selectedTags = choices.tags.filter(row => filters.tag_ids.includes(row.id)).map(row => row.name);
  const multi = (element: HTMLSelectElement) => Array.from(element.selectedOptions, option => option.value);
  return <><PageHeader title="Transactions" description={<>Simple transactions and complete journals for {session.entity.name}.</>}
    actions={!loadingRecord && !selected && !copy && session.capabilities.includes('bookkeeping:write') && <>
      <Link className="button" to="/transactions/new">Add transaction</Link><Link to="/journals/new" className="button action-secondary">Add journal</Link></>} />
    {loadingRecord ? <LoadingState label="Loading record…" /> : copy ? <TransactionCopyEditor draft={copy.draft} sourceId={copy.sourceId} onClose={() => { setCopy(undefined); setBulkRecords(undefined); setBulkSelection({ scope: '', ids: [] }); }} /> : selected ? 'transaction' in selected ? <TransactionEditor row={selected.transaction} onClose={() => setSelected(undefined)} />
      : <JournalEditor row={selected.journal} onClose={() => setSelected(undefined)} /> : <>
      <FilterPanel name="ledger" title="Filter ledger" label="Ledger filters" summary={[
        filters.start && `From ${filters.start}`, filters.end && `To ${filters.end}`, filters.kind !== 'All' && filters.kind,
        (filters.account_ids.length + filters.category_ids.length > 0) && `${filters.account_ids.length + filters.category_ids.length} account/category selections`,
        filters.tag_ids.length > 0 && `${filters.tag_ids.length} tags`, search.trim() && `Search: ${search.trim()}`,
        (sortField !== 'Date' || !descending) && `Sort: ${sortField} / ${descending ? 'Descending' : 'Ascending'}`,
      ].filter(Boolean).join(' · ') || 'All entries · Date / Descending'}>
      <div className="filterGrid ledgerFilterGrid">
        <DateRangeFields prefix="ledger" start={filters.start} end={filters.end}
          onStart={value => setFilters({ ...filters, start: value || undefined })} onEnd={value => setFilters({ ...filters, end: value || undefined })} />
        <Field id="kind" label="Type"><select id="kind" value={filters.kind} onChange={event => setFilters({ ...filters, kind: event.target.value as Kind })}>{['All', 'Income', 'Expense', 'Transfer', 'Journal'].map(kind => <option key={kind}>{kind}</option>)}</select></Field>
        <Field id="account-filter" label="Accounts"><select id="account-filter" multiple size={4} aria-describedby="account-filter-help" value={[...filters.account_ids.map(id => 'account:' + id), ...filters.category_ids.map(id => 'category:' + id)]} onChange={event => {
          const targets = multi(event.target);
          setFilters({ ...filters, account_ids: targets.filter(id => id.startsWith('account:')).map(id => id.slice(8)),
            category_ids: targets.filter(id => id.startsWith('category:')).map(id => id.slice(9)) });
        }}>{choices.accounts.map(row => <option key={row.id} value={'account:' + row.id}>{row.name}{row.active ? '' : ' (Inactive)'}</option>)}{choices.categories.map(row => <option key={'category:' + row.id} value={'category:' + row.id}>{row.kind} category · {row.name}</option>)}</select><p id="account-filter-help" className="fieldHint">Accounts and category targets. Select multiple with Ctrl/Command or Shift; no selection includes all.</p>{selectedTargets.length > 0 && <p className="fieldHint">Selected: {selectedTargets.join('; ')}</p>}</Field>
        <Field id="tag-filter" label="Tags"><select id="tag-filter" multiple size={4} aria-describedby="tag-filter-help" value={filters.tag_ids} onChange={event => setFilters({ ...filters, tag_ids: multi(event.target) })}>{choices.tags.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select><p id="tag-filter-help" className="fieldHint">Matches any selected tag. Select multiple with Ctrl/Command or Shift; no selection includes all.</p>{selectedTags.length > 0 && <p className="fieldHint">Selected: {selectedTags.join('; ')}</p>}</Field>
        <div className="ledgerSearch"><Field id="search" label="Search transactions"><input id="search" type="search" value={search} onChange={event => setSearch(event.target.value)} /></Field>
        {search.trim() && <ActionButton onClick={() => setSearch('')}>Clear search</ActionButton>}</div>
        <Field id="sort-field" label="Sort entries by"><select id="sort-field" value={sortField} onChange={event => setSortField(event.target.value as keyof typeof sortFields)}>{Object.keys(sortFields).map(field => <option key={field}>{field}</option>)}</select></Field>
        <Field id="entry-order" label="Entry order"><select id="entry-order" value={descending ? 'Descending' : 'Ascending'} onChange={event => setDescending(event.target.value === 'Descending')}><option>Descending</option><option>Ascending</option></select></Field>
      </div><div className="actions filterActions"><ActionButton onClick={() => { setFilters({ kind: 'All', account_ids: [], category_ids: [], tag_ids: [] }); setSearch(''); setSortField('Date'); setDescending(true); }}>Clear filters</ActionButton></div></FilterPanel>
      {!query.isPending && !query.isError && <p role="status">{rows.length} matching entries</p>}
      {choices.error && <ErrorNotice error={choices.error} />}
      {failure !== undefined && <ErrorNotice error={failure} />}
      {bulkResult && <SuccessNotice>{bulkResult}</SuccessNotice>}
      {session.capabilities.includes('bookkeeping:write') && <div className="actions"><ActionButton onClick={() => { if (bulkRecords) { setBulkRecords(undefined); setBulkSelection({ scope: '', ids: [] }); } else void startBulk(); }}>{bulkRecords ? 'Leave Bulk mode' : 'Bulk actions'}</ActionButton></div>}
      {bulkRecords && <LedgerBulkActions key={selectionScope + selectedIds.join(',')} loaded={bulkRecords}
        selected={bulkReview ? submittedSelection : rows.filter(row => selectedIds.includes(row.id))}
        onAction={() => { if (!bulkReview) setSubmittedSelection(rows.filter(row => selectedIds.includes(row.id))); setBulkReview(true); }}
        onCopy={(draft, sourceId) => setCopy({ draft, sourceId })}
        onApplied={message => { setBulkResult(message); setBulkRecords(undefined); setBulkSelection({ scope: '', ids: [] }); }} />}
      {query.isPending ? <LoadingState label="Loading ledger…" /> : query.isError ? <ErrorNotice operation="read" error={query.error} onReview={() => void query.refetch()} />
        : rows.length === 0 ? <EmptyState title="No matching entries">Add an entry or adjust the filters.</EmptyState>
        : <LedgerTable key={JSON.stringify([filters, search, sortField, descending, rows.map(row => row.id)])}
            rows={rows} filters={filters} edit={edit} loadingRecord={loadingRecord} bulk={bulkRecords ? { selectedIds, disabled: bulkReview, onSelect: ids => setBulkSelection({ scope: selectionScope, ids }) } : undefined} />}
    </>}
  </>;
}
