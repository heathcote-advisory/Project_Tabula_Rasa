import { compareRank, compareText, hierarchy } from '../../components/ordering';
import { HierarchyName } from '../../components/HierarchyName';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Schemas } from '../../api/client';
import { resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { ErrorNotice, LoadingState } from '../../components/States';
import { ActionButton, PageHeader, SectionHeader, TableRegion } from '../../components/VisualPrimitives';
import { DateRangeFields } from '../../components/DateRangeFields';

// Decimal strings stay intact. Styling never computes or rounds accounting values.
function Metric({ label, value, signed = false }: { label: string; value?: string | null; signed?: boolean }) {
  const tone = value == null ? 'metric-unavailable' : !signed || /^-?0(?:\.0+)?$/.test(value) ? ''
    : value.startsWith('-') ? 'metric-negative' : 'metric-positive';
  return <div><dt>{label}</dt><dd className={'amount ' + tone}>{value ?? '—'}</dd>{value == null && <span className="metric-context">Unavailable</span>}</div>;
}

function StatementTable({ title, rows }: { title: string; rows: Schemas['BalanceSheet']['assets'] }) {
  return <TableRegion label={title}><table><caption>{title}</caption><thead><tr>
    <th scope="col">Account</th><th scope="col" className="amount">Direct balance</th><th scope="col" className="amount">Including children</th>
  </tr></thead><tbody>{rows.length ? hierarchy(rows).map(({ row, depth, parent }) => <tr key={row.id}><th scope="row" className="tableText"><HierarchyName name={row.name} depth={depth} parent={parent} /></th>
    <td className="amount">{row.direct_balance ?? '—'}</td><td className="amount">{row.rollup_balance ?? '—'}</td></tr>)
    : <tr><td colSpan={3} className="tableEmpty">No accounts in {title}.</td></tr>}</tbody></table></TableRegion>;
}

export function ReportsPage({ dashboard = false }: { dashboard?: boolean }) {
  const session = useEntity();
  const [filters, setFilters] = useState<{ start?: string; end?: string }>({});
  const query = useQuery({ queryKey: [...resourceKey(session, 'reports'), filters], queryFn: ({ signal }) => api.reports(session, filters, signal) });
  const report = query.data;
  const difference = report?.balance_sheet.difference;
  const balanced = difference != null && /^-?0(?:\.0+)?$/.test(difference);
  return <><PageHeader title={dashboard ? 'Dashboard' : 'Reports'} description={<>Bookkeeping summary for {session.entity.name}.</>}
    actions={dashboard && <><Link to="/transactions" className="button">View ledger</Link><Link to="/accounts" className="button action-secondary">Set up accounts</Link></>} />
    {dashboard && !session.trusted_development && <section aria-label="Ledger next actions"><h2>Your ledger is ready</h2>
      <p>Review your accounts and opening balances, then open your ledger. An empty ledger is ready to use.</p>
      <div className="actions"><Link className="button" to="/accounts">Review accounts</Link>
        {session.capabilities.includes('bookkeeping:write') && <Link className="button" to="/transactions/new">Add a transaction</Link>}
        <Link className="button" to="/transactions">Open ledger</Link><Link className="button" to="/business/members">Business &amp; members</Link></div>
    </section>}
    <section className="surface periodControls" aria-label="Report period"><SectionHeader title="Report period"
      description="From and To include both dates. The balance sheet uses To as its as-of date; current account balances always cover all dates." />
      <div className="formGrid"><DateRangeFields prefix="report" start={filters.start} end={filters.end} endLabel="To / balance sheet as of"
        onStart={value => setFilters({ ...filters, start: value || undefined })} onEnd={value => setFilters({ ...filters, end: value || undefined })} /></div>
      <ActionButton onClick={() => setFilters({})}>Clear dates</ActionButton>
    </section>
    {query.isPending ? <LoadingState label="Loading reports…" /> : query.isError ? <ErrorNotice operation="read" error={query.error} onReview={() => void query.refetch()} /> : report && <div className="reportSections">
      <section aria-label="Profit and loss" className="reportSection"><SectionHeader title="Profit and loss"
        description={<>Inclusive period: {filters.start || 'All prior dates'} through {filters.end || 'latest date'}. Transfers and mileage do not affect income or expenses.</>} />
        <dl className="metrics reportMetrics"><Metric label="Income" value={report.profit_loss.income} /><Metric label="Expenses" value={report.profit_loss.expenses} />
          <Metric label="Net income" value={report.profit_loss.net} signed /></dl>
      </section>
      <section aria-label="Balance sheet" className="reportSection"><SectionHeader title="Balance sheet"
        description={<>As of {filters.end || 'latest date'}, including opening balances and current earnings. From does not limit this statement.</>} />
        <dl className="metrics reportMetrics"><Metric label="Assets" value={report.balance_sheet.total_assets} /><Metric label="Liabilities" value={report.balance_sheet.total_liabilities} />
          <Metric label="Equity" value={report.balance_sheet.total_equity} /><Metric label="Current earnings" value={report.balance_sheet.current_earnings} signed />
          <Metric label="Assets − Liabilities − Equity" value={difference} /></dl>
        <p role="status" className={'notice ' + (difference == null ? 'neutral' : balanced ? 'success' : 'warning')}>
          {difference == null ? 'Accounting-equation status is unavailable.' : balanced ? 'Assets = Liabilities + Equity.' : <>The balance sheet has a difference of {difference}. Review opening balances and ledger entries.</>}</p>
        <StatementTable title="Assets" rows={report.balance_sheet.assets} /><StatementTable title="Liabilities" rows={report.balance_sheet.liabilities} /><StatementTable title="Equity" rows={report.balance_sheet.equity} />
      </section>
      <section aria-label="All-date balances" className="reportSection"><SectionHeader title="Current account balances"
        description="All dates, including opening balances and inactive accounts. These balances are independent of the selected report period." />
        <TableRegion label="Current account balances"><table><caption>Current account balances · all dates</caption><thead><tr><th scope="col">Account</th><th scope="col">Classification</th><th scope="col">Status</th><th scope="col" className="amount">Ledger balance</th></tr></thead>
          <tbody>{report.account_balances.length ? hierarchy(report.account_balances, (a, b) => compareRank(a.classification, b.classification, ['Asset', 'Liability', 'Equity']) || compareText(a.name, b.name)).map(({ row, depth, parent }) => <tr key={row.id}><th scope="row" className="tableText"><HierarchyName name={row.name} depth={depth} parent={parent} /></th><td>{row.classification}</td><td>{row.active ? 'Active' : 'Inactive'}</td><td className="amount">{row.balance ?? '—'}</td></tr>)
            : <tr><td colSpan={4} className="tableEmpty">No account balances. <Link to="/accounts">Review accounts</Link> or <Link to="/transactions">open the ledger</Link>.</td></tr>}</tbody></table></TableRegion>
      </section>
    </div>}
  </>;
}
