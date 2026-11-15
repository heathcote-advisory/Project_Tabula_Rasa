import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Schemas } from '../../api/client';
import { useEntity } from '../../app/context';
import { ActionButton, TableRegion } from '../../components/VisualPrimitives';
import { ErrorNotice, LoadingState } from '../../components/States';
import { useCsvDownload } from '../exports/useCsvDownload';

export function LedgerTable({ rows, filters, edit, loadingRecord, bulk }: {
  rows: Schemas['LedgerItem'][];
  filters: Parameters<typeof api.ledger>[1];
  edit: (row: Schemas['LedgerItem']) => Promise<void>;
  loadingRecord: boolean;
  bulk?: { selectedIds: string[]; disabled: boolean; onSelect: (ids: string[]) => void };
}) {
  const session = useEntity();
  const journalColumns = rows.some(row => row.entry_type === 'journal' || row.postings.length > 0);
  const [selected, setSelected] = useState<string[]>([]);
  const transfer = useCsvDownload();
  const selection = rows.filter(row => selected.includes(row.id)).map(row => row.id);
  return <>
    {!bulk && <section className="surface" aria-label="Selected ledger exports">
      <h2>Export selected</h2>
      <p>Select visible entries below. Selecting a journal includes its complete parent and all postings.</p>
      <p role="status">{selection.length} entry(s) selected</p>
      <div className="actions"><ActionButton disabled={selection.length === 0 || transfer.busy}
        onClick={() => void transfer.download(signal => api.downloadSelectedLedgerCsv(session, selection, filters, signal), `tabula-rasa-selected-${new Date().toISOString().slice(0, 10)}.csv`)}>Export selected</ActionButton>
        <ActionButton disabled={selection.length === 0 || transfer.busy} onClick={() => setSelected([])}>Clear selection</ActionButton></div>
      {transfer.busy && <LoadingState label="Preparing selected CSV…" />}
      {transfer.error !== undefined && <><ErrorNotice operation="read" error={transfer.error} /><p>Refresh the ledger and review the selection before trying again.</p></>}
    </section>}
    <TableRegion label="Ledger"><table className="ledgerTable"><caption>Business ledger</caption>
      <thead><tr>{['Select', 'Date', 'Type', 'Account', 'To account', 'Category', 'Payee', 'Amount', ...(journalColumns ? ['Debit', 'Credit'] : []), 'Tags', 'Description', 'Receipt', 'Reference', 'Actions'].map(name => <th scope="col" className={['Amount', 'Debit', 'Credit', 'Receipt'].includes(name) ? 'amount' : name === 'Actions' ? 'tableActions' : undefined} key={name}>{name}</th>)}</tr></thead>
      {rows.map(row => <tbody key={row.id} className={row.entry_type === 'journal' ? 'journalGroup' : undefined}><tr>
        <td><input type="checkbox" checked={(bulk?.selectedIds ?? selected).includes(row.id)} disabled={bulk ? bulk.disabled : transfer.busy}
          aria-label={`Select ${row.txn_date} · ${row.description || row.kind} · ${row.amount}`}
          onChange={event => { const ids = bulk?.selectedIds ?? selected; (bulk?.onSelect ?? setSelected)(event.target.checked ? [...ids, row.id] : ids.filter(id => id !== row.id)); }} /></td>
        <td>{row.txn_date}</td><th scope="row" className="tableText" aria-label={row.kind + (row.source_type === 'mixed_use_expense' ? ' · Mixed-use' : '') + (row.recurring_schedule_id ? ' · Recurring' : '')}><div className="recordBadges"><span className="recordBadge">{row.kind}</span>{row.source_type === 'mixed_use_expense' && <span className="recordBadge">Mixed-use</span>}{row.recurring_schedule_id && <span className="recordBadge">Recurring</span>}</div></th><td className="tableText">{row.account}</td><td className="tableText">{row.to_account}</td><td className="tableText">{row.category}</td><td className="tableText">{row.payee}</td><td className="amount">{row.amount}</td>{journalColumns && <><td /><td /></>}<td className="tableText">{row.tags.length > 0 && <span className="recordBadge">{row.tags.join(', ')}</span>}</td><td className="tableText"><details className="descriptionPreview"><summary title={row.description} aria-label={row.description || 'No description'}><span>{row.description || '—'}</span></summary><div role="tooltip">{row.description}</div></details></td><td className="amount">{row.receipt_count > 0 && <span aria-label={`${row.receipt_count} receipt(s)`}>📎 ({row.receipt_count})</span>}</td><td className="tableText">{row.reference}</td>
        <td className="tableActions"><div className="rowActions">{row.entry_type === 'transaction' && !row.source_type && <Link className="button action-secondary" to={'/transactions/' + encodeURIComponent(row.record_id) + '/receipts'} aria-label={`Receipts for ${row.txn_date} · ${row.description || row.kind} · ${row.amount}`}>Receipts</Link>}{row.source_type === 'mixed_use_expense' ? <Link to="/mixed-use">Manage mixed-use expense</Link> : row.source_type === 'depreciation' ? <Link to="/assets">Manage in Assets</Link> : row.source_type ? 'Managed by ' + row.source_type : session.capabilities.includes('bookkeeping:write') && <ActionButton disabled={loadingRecord} aria-label={`Edit ${row.txn_date} · ${row.description || row.kind} · ${row.amount}`} onClick={() => void edit(row)}>Edit</ActionButton>}</div></td></tr>
        {row.postings.map((line, index) => <tr key={index} className="postingRow"><td /><td /><th scope="row">↳ Posting</th><td className="tableText">{line.target}</td><td /><td /><td className="tableText">{line.payee}</td><td /><td className="amount">{line.side === 'Debit' ? line.amount : ''}</td><td className="amount">{line.side === 'Credit' ? line.amount : ''}</td><td /><td className="tableText"><details className="descriptionPreview"><summary title={line.description} aria-label={line.description || 'No description'}><span>{line.description || '—'}</span></summary><div role="tooltip">{line.description}</div></details></td><td colSpan={3} /></tr>)}
      </tbody>)}</table></TableRegion>
  </>;
}
