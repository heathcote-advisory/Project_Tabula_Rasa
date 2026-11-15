import { compareDecimal, compareRank, compareText, hierarchy } from '../../components/ordering';
import { HierarchyName } from '../../components/HierarchyName';
import { useTableSort } from '../../components/useTableSort';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { accountQuery } from '../../api/query';
import { EmptyState, ErrorNotice, LoadingState } from '../../components/States';
import { useEntity } from '../../app/context';
import { useState } from 'react';
import type { Account } from '../../api/client';
import { AccountEditor } from '../concurrency/ReferenceEditors';
import { ActionButton, PageHeader, TableRegion } from '../../components/VisualPrimitives';

export function Dashboard() {
  const session = useEntity();
  return <><p className="eyebrow">Your business</p><h1>Dashboard</h1>
    <p className="lead">A clear view of {session.entity.name}.</p>
    <section className="surface"><h2>Start with your accounts</h2>
      <p>Review the accounts connected to this business.</p><Link to="/accounts" className="button">View accounts</Link>
    </section></>;
}

export function Accounts() {
  const session = useEntity();
  const [selected, setSelected] = useState<Account>();
  const query = useQuery(accountQuery(session));
  const sort = useTableSort<Account>((a, b) => compareRank(a.classification, b.classification, ['Asset', 'Liability', 'Equity']) || compareText(a.name, b.name));
  const compare = sort.compare({
    Name: (a, b) => compareText(a.name, b.name), Type: (a, b) => compareText(a.type, b.type),
    Classification: (a, b) => compareRank(a.classification, b.classification, ['Asset', 'Liability', 'Equity']),
    'Opening balance': (a, b) => compareDecimal(a.opening_balance, b.opening_balance),
    Status: (a, b) => Number(a.active) - Number(b.active),
  });
  return <><PageHeader title="Accounts" description={<>Accounts for {session.entity.name}.</>}
    actions={!selected && session.capabilities.includes('bookkeeping:write') && <Link to="/accounts/new" className="button">Add account</Link>} />
    {selected ? <AccountEditor row={selected} onClose={() => setSelected(undefined)} /> : query.isPending ? <LoadingState label="Loading accounts…" />
      : query.isError ? <ErrorNotice error={query.error} operation="read" onReview={() => void query.refetch()} />
      : query.data.items.length === 0 ? <EmptyState title="No accounts yet">Add an account to start bookkeeping.</EmptyState>
      : <TableRegion label="Accounts"><table>
        <caption>Business accounts</caption><thead><tr>{sort.header('Name')}{sort.header('Type')}{sort.header('Classification')}<th scope="col">Parent account</th>{sort.header('Opening balance', 'Opening balance', 'amount')}{sort.header('Status')}<th scope="col" className="tableActions">Actions</th></tr></thead>
        <tbody>{hierarchy(query.data.items, compare).map(({ row: account, depth, parent }) => <tr key={account.id}><th scope="row" className="tableText"><span className="recordTitle"><HierarchyName name={account.name} depth={depth} parent={parent} /></span></th>
          <td>{account.type}</td><td>{account.classification}</td><td className="tableText">{query.data.items.find(parent => parent.id === account.parent_id)?.name ?? '—'}</td><td className="amount">{account.opening_balance}</td>
          <td>{account.active ? 'Active' : 'Inactive'}</td><td className="tableActions"><div className="rowActions">{session.capabilities.includes('bookkeeping:write') &&
            <ActionButton aria-label={'Edit ' + account.name} onClick={() => setSelected(structuredClone(account))}>Edit</ActionButton>}</div></td></tr>)}</tbody></table></TableRegion>}
  </>;
}

export function PlannedPage({ title }: { title: string }) {
  return <><PageHeader title={title} /><EmptyState title="Not available in this technical alpha">
    {title === 'Data & backup' ? 'Import and backup controls are not available on this commercial page. Mileage CSV is available from Mileage.' : 'This workflow is not available for this business.'}
  </EmptyState></>;
}

export function MissingPage() {
  return <><PageHeader title="Page not found" description="Choose a page from the navigation." /><Link className="button action-secondary" to="/">Return to dashboard</Link></>;
}

export function RouteError() {
  return <main className="standalone"><PageHeader title="Page unavailable" /><ErrorNotice error={undefined} operation="read" /><a className="button action-secondary" href="/">Return to dashboard</a></main>;
}
