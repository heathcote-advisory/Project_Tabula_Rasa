import { useEffect, useState } from 'react';
import { api, type Schemas } from '../../api/client';

export function BetaNotice() {
  const [status, setStatus] = useState<Schemas['BetaStatus']>();
  useEffect(() => {
    const controller = new AbortController();
    void api.betaStatus(controller.signal).then(setStatus).catch(() => undefined);
    return () => controller.abort();
  }, []);
  return <section className="surface" aria-label="Beta enrollment">
    <h2>{status?.self_service ? 'Business enrollment' : 'Invitation-only free beta'}</h2>
    <p>{status?.self_service ? 'Eligible accounts can create a business directly while enrollment is open and space is available. Named collaborators still need separate approval.' :
      'The beta is noncharging. External businesses and named collaborators need operator approval. Signing in or owning another business does not approve enrollment.'}</p>
    {status && !status.internal && <p role="status">{status.paused
      ? 'New admissions are paused. Existing authorized access and export remain available.'
      : status.self_service ? 'Self-service business creation is available.' : 'Business creation requires prior approval.'}</p>}
    {status && status.requests.length > 0 && <ul>{status.requests.map(item => <li key={item.request_id}>
      {item.business_name}: {item.state}.
    </li>)}</ul>}
    <details><summary>Beta scope and service limits</summary>
    <p>New U.S. businesses start in USD; new Canadian businesses start in CAD. Owner/Admin can later change the bookkeeping label in Business & members without converting numeric amounts. Business country and approval do not change. Actual enrollment requires a separately approved release and cohort.</p>
    <p>The beta excludes bank feeds, invoicing, payroll, inventory, tax preparation and filing, jurisdiction-specific tax compliance, property-management and rental-tax automation, foreign exchange and consolidated reporting across businesses. It also excludes category template imports, standalone report downloads and compliance or audit exports. The beta does not offer full QuickBooks feature parity.</p>
    <p>Bookkeeping reports are available on screen. Data & backup offers transaction and journal posting CSVs and an accountant handoff archive for eligible roles.</p>
    <p>Beta businesses can view depreciation schedules and history. Depreciation posting and period overrides are unavailable.</p>
    <p>Beta receipts require customer-owned Google Drive. Ordinary bookkeeping and receipts may be used only after a separately approved release. Do not submit passwords, tokens, government IDs, full payment-card credentials, medical information or other unnecessary sensitive data.</p>
    <p>For the initial beta, independent backups of the database and receipt files are scheduled for Wednesday and Sunday. Recovery points are normally 72 to 96 hours apart. A missed backup can leave a longer gap. The full-service recovery target is 48 hours. Recovery for the admitted cohort still needs verification.</p>
    <p>One operator handles recovery and incidents. Response and recovery may take longer when the operator is unavailable. Support is best-effort with a target first response within two business days and weekly defect triage. There is no guaranteed repair time or 24/7 support. Your operator must provide the reviewed policies and contact channel before enrollment.</p>
    </details>
  </section>;
}
