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
    <h2>Invitation-only free beta</h2>
    <p>The beta is noncharging. Each business and named collaborator needs operator approval; signing in or owning another business does not approve enrollment.</p>
    {status && !status.internal && <p role="status">{status.paused || status.invitations_paused
      ? 'New admissions are paused. Existing authorized access and export remain available.'
      : 'Enrollment is open only for the approved cohort.'}</p>}
    {status && status.requests.length > 0 && <ul>{status.requests.map(item => <li key={item.request_id}>
      {item.business_name}: {item.state}.
    </li>)}</ul>}
    <details><summary>Beta scope and service limits</summary>
    <p>New U.S. businesses start in USD; new Canadian businesses start in CAD. Owner/Admin can later change the bookkeeping label in Business & members without converting numeric amounts. Business country and approval do not change. Actual enrollment requires a separately approved release and cohort. No foreign exchange or consolidated cross-currency reporting, tax filing, payroll, bank feeds or unverified depreciation operations are offered.</p>
    <p>Beta receipts require customer-owned Google Drive. Ordinary bookkeeping and receipts may be used only after a separately approved release. Do not submit passwords, tokens, government IDs, full payment-card credentials, medical information or other unnecessary sensitive data.</p>
    <p>Support is best-effort with a target first response within two business days and weekly defect triage; no fix-time or 24/7 SLA. Independent database and receipt recovery targets of 24 hours and full-service recovery within 48 hours remain unverified. Your operator must provide the reviewed policies and contact channel before enrollment.</p>
    </details>
  </section>;
}
