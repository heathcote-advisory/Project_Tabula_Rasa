import { useState } from 'react';
import { Link } from 'react-router-dom';
import { PolicyLinks } from './PolicyPages';

import { SUPPORT_EMAIL, categories, severityFor, helpDraft, type HelpCategory } from './helpDraft';

export function HelpPage() {
  const [category, setCategory] = useState<HelpCategory>('Product feedback');
  const [reference, setReference] = useState('');
  const [release, setRelease] = useState('');
  const [consent, setConsent] = useState(false);
  const [research, setResearch] = useState(false);
  const [answers, setAnswers] = useState('');
  const [preview, setPreview] = useState<string>();
  return <main className="standalone"><section className="surface"><PolicyLinks /><h1>Help &amp; feedback</h1>
    <p>Contact <a href={'mailto:' + SUPPORT_EMAIL}>{SUPPORT_EMAIL}</a>, including when you cannot sign in. This form prepares a draft in your mail client; it does not send or upload anything.</p>
    <p>Best-effort support targets an ordinary first response within two business days, excluding weekends and operator-observed holidays. Weekly triage; no guaranteed repair deadline or 24/7 staffed response.</p>
    <p>Suspected unauthorized access, serious security incidents, data loss or corruption: report privately now. P0 containment/escalation starts on detection, independently of the ordinary queue. Stop affected work and preserve evidence. Do not paste sensitive evidence here or into public GitHub.</p>
    <form onSubmit={e => { e.preventDefault(); if (consent) setPreview(helpDraft(category, reference, release, research, answers)); }} onChange={() => setPreview(undefined)}>
      <div className="field"><label htmlFor="help-category">Category</label><select id="help-category" value={category} onChange={e => setCategory(e.target.value as HelpCategory)}>{categories.map(value => <option key={value}>{value}</option>)}</select></div>
      <p>Provisional priority: {severityFor(category)}. The operator verifies severity; material core outages are P1 and block expansion until resolved. Routine defects are P2; suggestions P3.</p>
      <div className="field"><label htmlFor="help-reference">Safe request or reproduction reference (optional)</label><input id="help-reference" value={reference} maxLength={80} pattern="[A-Za-z0-9_-]*" onChange={e => setReference(e.target.value)} /></div>
      <div className="field"><label htmlFor="help-release">Release number (optional)</label><input id="help-release" value={release} maxLength={100} pattern="[A-Za-z0-9._-]*" onChange={e => setRelease(e.target.value)} /></div>
      <label><input type="checkbox" checked={research} onChange={e => { setResearch(e.target.checked); if (!e.target.checked) setAnswers(''); }} /> Optional: share qualitative product research. Help does not require this choice.</label>
      {research && <><p>Without names, business identifiers or financial records: Did your first ledger and core tasks succeed? What is missing? Would you continue with one business or add a second/third? What value do independent scopes provide? Prefer charging per owner, business or collaborator? What monthly amount/currency would you consider, and what prevents switching? Answers are directional research, not an agreement to pay.</p>
        <div className="field"><label htmlFor="help-research">Optional research answers</label><textarea id="help-research" maxLength={2000} value={answers} onChange={e => setAnswers(e.target.value)} /></div></>}
      <p>Your mail provider receives your sender address and anything you choose to send. Only the operator and authorized case reviewers should access private cases. Retention/legal review remains separate; no automatic purge is promised. Request research withdrawal through the same email.</p>
      <label><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} /> I reviewed these fields, excluded sensitive content, and choose to prepare a private email draft.</label>
      <button type="submit" disabled={!consent}>Review email draft</button>
    </form>
    {preview && <section aria-label="Email draft"><h2>Review before sending</h2><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{preview}</pre>
      <a className="button" href={`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent('Tabula Rasa help: ' + category)}&body=${encodeURIComponent(preview)}`}>Open in mail client</a>
      <p>No message has been sent. Review the recipient, text and attachments in your mail client before sending. If no mail client opens, copy this draft into your email service.</p></section>}
    <Link to="/">Return to application</Link>
  </section></main>;
}
