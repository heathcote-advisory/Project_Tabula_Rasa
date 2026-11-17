import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Schemas, type SessionState } from '../../api/client';
import { ErrorNotice, LoadingState } from '../../components/States';
import { ActionButton } from '../../components/VisualPrimitives';

export function PolicyLinks() {
  return <nav aria-label="Policies and help"><a href="/terms">Terms of Use</a>{' · '}
    <a href="/privacy">Privacy Policy</a>{' · '}<a href="/help">Help &amp; feedback</a></nav>;
}

export function PublicHomePage() {
  return <main className="standalone"><section className="surface"><h1>Tabula Rasa</h1>
    <p>Bookkeeping for one or several independently managed businesses, operated by Heathcote Advisory LLC.</p>
    <p>Manage business accounts, transactions, journals and supporting receipts within your authorized scope. Review records before relying on them. The application does not provide professional tax, accounting or legal advice.</p>
    <p>Access requires individual authorization. Creating a login does not approve a business or invite anyone into a beta.</p>
    <PolicyLinks /><p><Link to="/">Open application</Link></p>
  </section></main>;
}

// Only the headings, lists, emphasis and links used by our fixed policy sources.
// Raw HTML and non-HTTPS/non-mail links remain inert text.
function PolicyInline({ text }: { text: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    const link = /^\[([^\]]+)\]\((https:\/\/[^\s]+|mailto:[^\s]+)\)$/.exec(part);
    if (link) return <a key={i} href={link[2]}>{link[1]}</a>;
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>;
    return part;
  })}</>;
}

function PolicyText({ text }: { text: string }) {
  return <article>{text.split(/\n\s*\n/).map((block, i) => {
    if (block.startsWith('# ')) return <h1 key={i}>{block.slice(2)}</h1>;
    if (block.startsWith('## ')) return <h2 key={i}>{block.slice(3)}</h2>;
    if (block.startsWith('- ')) return <ul key={i}>{block.split('\n- ').map((line, j) =>
      <li key={j}><PolicyInline text={j === 0 ? line.slice(2) : line} /></li>)}</ul>;
    return <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }} key={i}><PolicyInline text={block} /></p>;
  })}</article>;
}

export function PublicPolicyPage({ kind }: { kind: 'terms' | 'privacy' }) {
  const [policies, setPolicies] = useState<Schemas['PublicPolicies']>();
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    const controller = new AbortController();
    void api.publicPolicies(controller.signal).then(setPolicies).catch(e => { if (!controller.signal.aborted) setError(e); });
    return () => controller.abort();
  }, []);
  return <main className="standalone"><section className="surface">
    <PolicyLinks />
    {error ? <ErrorNotice error={error} operation="read" onReview={() => window.location.reload()} />
      : policies ? <><p>Policy version: {policies.version} · Effective date: {policies.effective_date}</p>
        <PolicyText text={policies[kind]} />
        <p><a href="https://maps.google.com/help/terms_maps/">Google Maps/Google Earth Additional Terms</a>{' · '}
          <a href="https://policies.google.com/privacy">Google Privacy Policy</a></p></> : <LoadingState />}
    <Link to="/">Return to application</Link>
  </section></main>;
}

export function PolicyAcknowledgement({ session }: { session: SessionState }) {
  const [status, setStatus] = useState<Schemas['PolicyStatus']>();
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const lock = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    void api.policyStatus(controller.signal).then(setStatus).catch(e => { if (!controller.signal.aborted) setError(e); });
    return () => controller.abort();
  }, []);
  async function accept() {
    if (!status || !checked || lock.current) return;
    lock.current = true; setBusy(true); setError(undefined);
    try { setStatus(await api.acceptPolicies(session, status.digest)); }
    catch (e) { setError(e); }
    finally { lock.current = false; setBusy(false); }
  }
  if (status && (!status.required || status.acknowledged)) return <PolicyLinks />;
  return <section className="surface" aria-label="Policy acknowledgement"><PolicyLinks />
    {error ? <><ErrorNotice error={error} operation="read" onReview={() => window.location.reload()} />
      <p>Reload to check acknowledgement after an uncertain response. Existing export and closure access remains available.</p></>
      : !status ? <LoadingState label="Checking policy version…" /> : <>
        <h2>Review the current policies</h2><p>Before creating or joining a business, review the linked Terms and Privacy Policy. Updates require a new acknowledgement; existing bookkeeping, export and closure access remains available.</p>
        <label><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} /> I accept the Terms of Use and acknowledge receipt of the Privacy Policy. This does not opt me into research or marketing.</label>
        <ActionButton disabled={!checked || busy} onClick={() => void accept()}>Record acknowledgement</ActionButton>
      </>}
  </section>;
}
