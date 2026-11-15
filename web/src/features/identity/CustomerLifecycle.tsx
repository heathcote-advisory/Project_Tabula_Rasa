import { useEffect, useRef, useState } from 'react';
import { api, type Schemas, type SessionState } from '../../api/client';
import { ActionButton, SectionHeader } from '../../components/VisualPrimitives';
import { ErrorNotice, LoadingState } from '../../components/States';
import { ConfirmationDialog } from '../../components/ConfirmationDialog';

type Record = Schemas['LifecycleRecord'];
type Kind = Record['kind'];
const labels: { [K in Kind]: string } = { business_closure: 'Business closure', account_closure: 'Personal login closure', privacy_erasure: 'Privacy erasure review' };
const states: { [key: string]: string } = { pending: 'Awaiting your confirmation', confirmed: 'Confirmed', cancelled: 'Cancelled', read_only: 'Read-only export/recovery window', closed_retained: 'Access ended; data retained' };

/** Also available without a selected business, including after its access ends. */
export function CustomerLifecycle({ session, businessId }: { session: SessionState; businessId?: string }) {
  const [status, setStatus] = useState<Schemas['LifecycleStatus']>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<{ row: Record; action: 'confirm' | 'cancel' }>();
  const [saved, setSaved] = useState(false);
  const [generation, setGeneration] = useState(0);
  const ids = useRef<Partial<{ [K in Kind]: string }>>({});
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    void api.lifecycleStatus(businessId, controller.signal).then(value => {
      if (!controller.signal.aborted) setStatus(value);
    }, failure => { if (!controller.signal.aborted) setError(failure); });
    return () => controller.abort();
  }, [businessId, generation]);
  async function command(work: () => Promise<Record>) {
    setBusy(true); setError(undefined);
    try {
      const result = await work();
      if (!alive.current) return;
      if (result.state === 'cancelled') delete ids.current[result.kind];
      setConfirm(undefined);
      setGeneration(value => value + 1);
      if (result.kind === 'account_closure' && result.state === 'confirmed') {
        window.location.assign('/signed-out');
      } else {
        // Lifecycle version changes the private scope even when role is unchanged.
        window.dispatchEvent(new Event('tabula:identity-check'));
        if (typeof BroadcastChannel !== 'undefined') {
          const channel = new BroadcastChannel('tabula-auth');
          channel.postMessage('authority-check'); channel.close();
        }
      }
    } catch (failure) {
      if (alive.current) { setError(failure); setConfirm(undefined); setGeneration(value => value + 1); }
    } finally { if (alive.current) setBusy(false); }
  }
  function request(kind: Kind) {
    // Preserve the identity through uncertain HTTP results; status after restart
    // exposes an already committed request rather than silently submitting twice.
    ids.current[kind] ??= crypto.randomUUID();
    void command(() => api.requestLifecycle(session, { request_id: ids.current[kind]!, kind,
      entity_id: kind === 'business_closure' ? businessId : null }));
  }
  function review(row: Record, action: 'confirm' | 'cancel') { setSaved(false); setConfirm({ row, action }); }
  const active = (kind: Kind) => status?.requests.some(row => row.kind === kind && row.state !== 'cancelled');
  return <section className="surface" aria-label="Closure and privacy requests">
    <SectionHeader title="Closure and privacy requests" />
    <p>Business closure affects only this business. Your personal login and other businesses are separate.</p>
    <p>Confirmed business closure makes bookkeeping read-only for 60 days. Save needed bookkeeping and receipt exports first using Accountant handoff in Data &amp; backup.
      During that window any current active Owner may sign in again and cancel closure. Afterwards ordinary customer access ends; contact your operator for review.</p>
    <p>Data remains retained. The proposed deletion target is day 90 after business closure, subject to legal, hold, document and backup review.
      Deletion and backup pruning are unavailable. Historical backups and immutable audit history are preserved under the current policy; immediate complete erasure is not promised.</p>
    <p>Personal login confirmation immediately disables your login and revokes all sessions. It does not close businesses, delete your provider account or erase history.
      A last active business Owner must transfer ownership or resolve it with support first. Privacy erasure is a separate case-specific review.</p>
    {!status && !error && <LoadingState label="Loading lifecycle requests…" />}
    {error !== undefined && <ErrorNotice error={error} onReview={() => { setError(undefined); setGeneration(value => value + 1); }} />}
    {status && <>
      <div className="actions">
        {businessId && !active('business_closure') && <ActionButton tone="destructive" disabled={busy} onClick={() => request('business_closure')}>Request business closure</ActionButton>}
        {!active('account_closure') && <ActionButton tone="destructive" disabled={busy || status.last_owned_businesses.length > 0} onClick={() => request('account_closure')}>Request personal login closure</ActionButton>}
        {!active('privacy_erasure') && <ActionButton disabled={busy} onClick={() => request('privacy_erasure')}>Request privacy erasure review</ActionButton>}
      </div>
      {status.last_owned_businesses.length > 0 && <p>You are the last active Owner of {status.last_owned_businesses.length} business(es). Login closure is blocked until ownership is resolved.</p>}
      <ul className="recordList">{status.requests.map(row => <li key={row.id}>
        <div><strong>{labels[row.kind]}</strong><p role="status">{states[row.effective_state] ?? row.effective_state}</p>
          {row.entity_id && <p>Business reference: <code>{row.entity_id}</code></p>}
          <p>Request reference: <code>{row.id}</code></p>
          {row.state === 'confirmed' && row.recover_until && <p>Export/recovery window ends: {new Date(row.recover_until).toLocaleString()}</p>}
          {row.kind === 'privacy_erasure' && row.state === 'confirmed' && <p>Awaiting case-specific operator/legal review; no erasure is scheduled.</p>}
          <ul>{row.events.map(event => <li key={event.action}>{event.action} · {new Date(event.occurred_at).toLocaleString()}</li>)}</ul>
          <div className="actions">
            {row.state === 'pending' && <ActionButton disabled={busy} onClick={() => review(row, 'confirm')}>Review and confirm {labels[row.kind].toLowerCase()}</ActionButton>}
            {row.state !== 'cancelled' && row.effective_state !== 'closed_retained' && <ActionButton disabled={busy} onClick={() => review(row, 'cancel')}>Cancel {labels[row.kind].toLowerCase()}</ActionButton>}
          </div>
        </div></li>)}</ul>
      <p>Confirming or cancelling requires sign-in within the last five minutes. For review, access loss, a hold or an expired recovery window, contact your service operator through your existing private support channel with the request reference. Do not send private records through public issue trackers.</p>
    </>}
    {confirm && <ConfirmationDialog title={`${confirm.action === 'cancel' ? 'Cancel' : 'Confirm'} ${labels[confirm.row.kind].toLowerCase()}`}
      keepLabel="Keep reviewing" confirmLabel={confirm.action === 'cancel' ? 'Cancel request' : 'Confirm request'} pending={busy}
      confirmDisabled={confirm.action === 'confirm' && confirm.row.kind === 'business_closure' && !saved}
      onKeep={() => setConfirm(undefined)} onConfirm={() => void command(() => api.changeLifecycle(session, confirm.row.id,
        { action: confirm.action, version: confirm.row.version, export_saved: saved }))}>
      {confirm.row.entity_id && <p>This request affects business <code>{confirm.row.entity_id}</code> only.</p>}
      <p>{confirm.action === 'cancel' ? 'Cancel this request. Cancelling business closure within the recovery window restores normal authorized access.'
        : confirm.row.kind === 'business_closure' ? 'Confirm 60 days of read-only bookkeeping, followed by loss of ordinary access. No records or files will be deleted.'
        : confirm.row.kind === 'account_closure' ? 'Your login will be disabled immediately and all sessions revoked. Reactivation requires support. Identity and history are retained.'
        : 'Submit a privacy review. No erasure, retention change or completion deadline is promised.'}</p>
      {confirm.action === 'confirm' && confirm.row.kind === 'business_closure' && <label><input type="checkbox" checked={saved} onChange={event => setSaved(event.target.checked)} /> I saved the bookkeeping and receipt exports I need.</label>}
    </ConfirmationDialog>}
  </section>;
}
