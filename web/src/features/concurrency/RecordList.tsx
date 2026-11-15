import { Fragment, useState, type ReactNode } from 'react';
import { compareText, hierarchy } from '../../components/ordering';
import { HierarchyName } from '../../components/HierarchyName';
import { useTableSort } from '../../components/useTableSort';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { EmptyState, ErrorNotice, LoadingState } from '../../components/States';
import type { Session } from '../../api/client';
import { ActionButton, TableRegion } from '../../components/VisualPrimitives';

export function RecordList<R extends { id: string }>({ resource, title, list, label, editor, canEdit = () => true, columns = [], receiptTarget, detailsTarget, sorting, tree }: {
  sorting?: { defaultCompare: (a: R, b: R) => number; record?: (a: R, b: R) => number };
  tree?: (row: R) => { name: string; parent_id?: string | null };
  resource: string; title: string; list: (session: Session, signal?: AbortSignal) => Promise<{ items: R[] }>;
  label: (row: R) => string; editor: (row: R, close: () => void) => ReactNode; canEdit?: (row: R) => boolean;
  receiptTarget?: (row: R) => boolean;
  detailsTarget?: (row: R) => string | undefined;
  columns?: { title: string; className?: string; compare?: (a: R, b: R) => number; render: (row: R, rows: R[]) => ReactNode }[];
}) {
  const session = useEntity();
  const sort = useTableSort<R>(sorting?.defaultCompare ?? ((a, b) => compareText(label(a), label(b))));
  const detailAction = session.capabilities.includes('bookkeeping:write') ? 'Manage' : 'View';
  const [selected, setSelected] = useState<R>();
  const query = useQuery({ queryKey: resourceKey(session, resource), queryFn: ({ signal }) => list(session, signal) });
  const compare = sort.compare(Object.fromEntries([['Record', sorting?.record ?? ((a: R, b: R) => compareText(label(a), label(b)))],
    ...columns.filter(column => column.compare).map(column => [column.title, column.compare!]) ]));
  const records = query.data?.items ?? [];
  const rows = tree ? hierarchy(records.map(row => ({ ...row, ...tree(row) })), compare)
    : [...records].sort(sorting ? compare : () => 0).map(row => ({ row, depth: 0, parent: undefined }));
  return <div>
    {selected ? <>{editor(selected, () => setSelected(undefined))}</> : query.isPending ? <LoadingState />
      : query.isError ? <ErrorNotice error={query.error} operation="read" onReview={() => void query.refetch()} />
      : query.data.items.length === 0 ? <EmptyState title="No records to edit">Records for this business will appear here.</EmptyState>
      : <TableRegion label={title}><table aria-label={title}>
        <thead><tr>{sorting ? sort.header('Record') : <th scope="col">Record</th>}{columns.map(column => <Fragment key={column.title}>{sorting && column.compare ? sort.header(column.title, column.title, column.className) : <th scope="col" className={column.className}>{column.title}</th>}</Fragment>)}<th scope="col" className="tableActions">Actions</th></tr></thead>
        <tbody>{rows.map(({ row, depth, parent }) => <tr key={row.id}><th scope="row" className="tableText"><span className="recordTitle">{tree ? <HierarchyName name={label(row)} depth={depth} parent={parent} /> : label(row)}</span></th>
          {columns.map(column => <td key={column.title} className={column.className}>{column.render(row, query.data.items)}</td>)}<td className="tableActions"><div className="rowActions">{canEdit(row) && session.capabilities.includes('bookkeeping:write')
          && <ActionButton aria-label={'Edit ' + label(row)} onClick={() => setSelected(structuredClone(row))}>Edit</ActionButton>}
          {detailsTarget?.(row) && <Link className="button action-secondary" aria-label={detailAction + ' ' + label(row)} to={detailsTarget(row)!}>{detailAction}<span aria-hidden="true">↗</span></Link>}
          {receiptTarget?.(row) && <Link className="button action-secondary" aria-label={'Receipts for ' + label(row)} to={'/transactions/' + encodeURIComponent(row.id) + '/receipts'}>Receipts</Link>}</div></td>
        </tr>)}</tbody></table></TableRegion>}
  </div>;
}
