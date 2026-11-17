import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Schemas, type SessionState } from '../../api/client';
import { ErrorNotice, Field, LoadingState } from '../../components/States';
import { ActionButton, PageHeader } from '../../components/VisualPrimitives';
import { BetaNotice } from './BetaNotice';
import { InvitationAcceptance } from './InvitationAcceptance';
import { notifyPeerScopeChange } from '../../app/authSignals';

/** Persist only an opaque receipt key. Authority always comes from the session/API. */
function receiptKey(session: SessionState) {
  return `tabula-setup:${session.actor_id}:${session.state === 'needs_setup' ? 'first' : `additional:${session.entity.id}`}`;
}

export function LedgerSetup({ session }: { session: SessionState }) {
  const key = receiptKey(session);
  const [step, setStep] = useState(1);
  const [business, setBusiness] = useState('');
  const [country, setCountry] = useState<'US' | 'CA'>('US');
  const [admission, setAdmission] = useState<string>();
  const [pending, setPending] = useState(false);
  const [checking, setChecking] = useState(true);
  const [interrupted, setInterrupted] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<unknown>();
  const submitted = useRef<Schemas['BusinessCreateRequest'] | undefined>(undefined);
  const confirmed = useRef<Schemas['BusinessCreateResult'] | undefined>(undefined);
  const mounted = useRef(false);
  const request = useRef<string | undefined>(undefined);
  const receiptSlot = useRef(key);

  async function finish(result: Schemas['BusinessCreateResult']) {
    if (result.state !== 'ready' || !result.entity_id) return;
    // Select through the authorized server boundary, including additional businesses.
    const current = await api.session();
    if (!mounted.current) return;
    if (current.actor_id !== session.actor_id || current.state === 'needs_setup') throw new Error('Business context unavailable');
    if (current.entity.id !== result.entity_id) await api.selectEntity(current, result.entity_id);
    if (!mounted.current) return;
    sessionStorage.removeItem(receiptSlot.current);
    notifyPeerScopeChange();
    window.location.replace('/');
  }

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    async function resume() {
      try {
        request.current = sessionStorage.getItem(key) ?? undefined;
        if (!request.current && session.state !== 'needs_setup') {
          // Resume the preceding workspace UI's exact handle without allocating a new request.
          const previous = `tabula-setup:${session.actor_id}:${session.organization_id}`;
          const existing = sessionStorage.getItem(previous);
          if (existing) { request.current = existing; receiptSlot.current = previous; }
        }
        if (request.current) {
          const result = await api.bootstrapStatus(request.current, controller.signal);
          if (controller.signal.aborted) return;
          if (result.state === 'ready') {
            confirmed.current = result;
            setAttempted(true); setStep(2);
            await finish(result);
          }
          else { setInterrupted(true); if (result.state !== 'not_started') setAdmission(result.state); }
        }
      } catch (failure) { if (!controller.signal.aborted) setError(failure); }
      finally { if (!controller.signal.aborted) setChecking(false); }
    }
    void resume();
    return () => { mounted.current = false; controller.abort(); };
    // The parent keys this component by authenticated actor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function create() {
    if (pending) return;
    setPending(true); setError(undefined);
    try {
      if (confirmed.current) { await finish(confirmed.current); return; }
      if (!request.current) {
        const id = crypto.randomUUID();
        // A failed storage write must stop before a remote create can happen.
        sessionStorage.setItem(key, id);
        request.current = id;
      }
      submitted.current ??= { request_id: request.current, business_name: business.trim(), country, currency: country === 'CA' ? 'CAD' : 'USD' };
      setAttempted(true);
      let result: Schemas['BusinessCreateResult'];
      try {
        result = await api.createBusiness(session, submitted.current);
      } catch (failure) {
        if (failure instanceof ApiError && !['unavailable', 'unexpected'].includes(failure.code)) throw failure;
        // One bounded readback after an uncertain response; no automatic create retry.
        const status = await api.bootstrapStatus(request.current);
        if (status.state !== 'ready') throw failure;
        result = status;
      }
      if (result.state === 'ready') { confirmed.current = result; if (mounted.current) await finish(result); }
      else if (mounted.current) setAdmission(result.state);
    } catch (failure) { if (mounted.current) setError(failure); }
    finally { if (mounted.current) setPending(false); }
  }

  if (checking) return <LoadingState label="Checking interrupted setup…" />;
  return <section className="workflowSections" aria-label="Create business">
    <h2>{session.state === 'needs_setup' ? 'Create your first business' : 'Create another business'}</h2>
    <p role="status">Step {step} of 2 · {step === 1 ? 'Name your business' : 'Review bookkeeping defaults'}</p>
    {admission && <p role="status">Enrollment: {admission}. {admission === 'approved'
      ? 'Re-enter the original details and retry the same setup to create your approved business.'
      : 'Your business has not been created. Ask your operator to review this request; retry the same setup after approval. Expired, denied or withdrawn requests require explicit reapproval.'}</p>}
    {interrupted && <p>Setup was interrupted. Re-enter the same business name to resume. Your original request will be reused.</p>}
    <form onSubmit={event => { event.preventDefault(); if (step === 1) setStep(2); else void create(); }}>
      {step === 1 ? <>
        <Field id="setup-business" label="Business name"><input id="setup-business" required maxLength={160} value={business}
          onChange={event => setBusiness(event.target.value)} /></Field>
        <Field id="setup-country" label="Business country and currency"><select id="setup-country" value={country}
          onChange={event => setCountry(event.target.value as 'US' | 'CA')}>
          <option value="US">United States · USD</option><option value="CA">Canada · CAD</option>
        </select></Field>
      </> : <>
        <p>{business.trim() ? `Business: ${business.trim()}` : 'Your business was created. Retry to open its ledger.'}</p>
        <Field id="setup-defaults" label="Bookkeeping defaults"><select id="setup-defaults" defaultValue="standard"><option value="standard">Standard basic bookkeeping</option></select></Field>
        <p>Book currency: {country === 'CA' ? 'CAD' : 'USD'}. Owner/Admin can later change this label in Business & members. Numeric amounts are never converted.</p>
        <p>Start with the supported chart of accounts and Income/Expense categories. Opening balances start at zero. Review accounts before recording your first transaction.</p>
        <p>Beta receipts require customer-owned Google Drive. Connect Google Drive later from Data &amp; backup, or invite collaborators later from business settings.</p>
        {!attempted && <ActionButton disabled={pending} onClick={() => setStep(1)}>Back</ActionButton>}
      </>}
      <div className="actions"><ActionButton tone="primary" type="submit" pending={pending}
        disabled={step === 1 && !business.trim()}>
        {step === 1 ? 'Review defaults' : attempted ? 'Retry same setup' : 'Create business'}</ActionButton></div>
    </form>
    {pending && <LoadingState label="Submitting your business setup…" />}
    {error !== undefined && <><ErrorNotice error={error} /><p>Setup could not be confirmed. Retry uses the same request. If this page was refreshed, use the original business name. Enable session storage if your browser blocks it.</p></>}
  </section>;
}

export function Onboarding({ session }: { session: SessionState }) {
  const [path, setPath] = useState<'choose' | 'fresh' | 'invite' | 'migration'>(() => window.location.pathname === '/invitations' ? 'invite' : 'choose');
  return <div className="workflowSections"><PageHeader title={path === 'invite' ? 'Join an invited business' : 'My Businesses'} description="Your account has no active business access. Join an invited business with its privately shared credential, or start business setup to create a ledger or move existing books." />
    {path === 'choose' && <div className="actions">
      {session.business_creation_available && <ActionButton tone="primary" onClick={() => setPath('fresh')}>Create your first business</ActionButton>}
      <ActionButton onClick={() => setPath('invite')}>Join an invited business</ActionButton>
      <ActionButton onClick={() => setPath('migration')}>Move existing Sheets books</ActionButton>
    </div>}
    {path === 'fresh' && <LedgerSetup key={session.actor_id} session={session} />}
    {path === 'invite' && <InvitationAcceptance session={session} />}
    {path === 'migration' && <section><h2>Existing live books</h2><p>Do not create a replacement ledger here. Ask your operator to follow the explicit migration and cutover process. Browser setup does not import, claim or switch authority for existing Sheets books.</p></section>}
    {path !== 'choose' && <div className="actions"><a className="button" href="/">Return to My Businesses</a></div>}
    <BetaNotice />
  </div>;
}
