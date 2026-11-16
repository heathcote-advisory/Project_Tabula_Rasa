import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Schemas } from '../../api/client';
import { resourceKey } from '../../api/query';
import { useEntity } from '../../app/context';
import { ErrorNotice, Field, LoadingState, SuccessNotice } from '../../components/States';
import { ActionButton, FormSection, PageHeader } from '../../components/VisualPrimitives';
import { InvitationAcceptance } from './InvitationAcceptance';
import { ConfirmationDialog } from '../../components/ConfirmationDialog';
import { roleNames, type Role } from '../../app/roles';
import { notifyPeerAuthorityCheck } from '../../app/authSignals';
import { uncertainIdentityCommand } from './commandOutcome';
import { BusinessCurrency } from './BusinessCurrency';

function MemberEditor({ member, owner, onSave, onGrant }: { member: Schemas['Member']; owner: boolean; onSave: (role: Role, active: boolean) => Promise<void>; onGrant: () => Promise<void> }) {
  const actor = useEntity().actor_id;
  const [role, setRole] = useState<Role>(member.role);
  const [active, setActive] = useState(member.state === 'active');
  const [confirm, setConfirm] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [grant, setGrant] = useState(false);
  const submitting = useRef(false);
  const [uncertain, setUncertain] = useState(false);
  async function submit(operation: () => Promise<void>, close: () => void) {
    if (submitting.current || uncertain) return;
    submitting.current = true; setPending(true); setError(undefined);
    try { await operation(); close(); }
    catch (failure) {
      setError(failure);
      setUncertain(uncertainIdentityCommand(failure));
    } finally { submitting.current = false; setPending(false); }
  }
  const allowed = owner || member.role === 'admin' && member.user_id === actor || !['owner', 'admin'].includes(member.role);
  return <section className="surface"><h3>{member.display_name || 'Business member'}</h3>
    <p>Current role: {roleNames[member.role]} · {member.state}</p>
    {allowed && <><Field id={'role-' + member.user_id} label="Member role"><select id={'role-' + member.user_id} value={role} disabled={pending || uncertain}
      onChange={event => setRole(event.target.value as Role)}>
      {(Object.keys(roleNames) as Role[]).filter(item => item === 'owner' ? member.role === 'owner' : owner || item !== 'admin' || member.role === 'admin').map(item => <option key={item} value={item}>{roleNames[item]}</option>)}
    </select></Field>
    <label><input type="checkbox" checked={active} disabled={pending || uncertain} onChange={event => setActive(event.target.checked)} /> Active membership</label>
    <div className="actions"><ActionButton disabled={pending || uncertain} onClick={() => { setError(undefined); setConfirm(true); }}>Review membership change</ActionButton></div></>}
    {owner && (member.role !== 'owner' || member.state !== 'active') && <section className="ownershipAction" aria-label="Ownership">
      <h4>Ownership</h4><p>Owners can administer this business and authorize recovery. A recent sign-in is required.</p>
      <div className="actions"><ActionButton disabled={pending || uncertain} onClick={() => { setError(undefined); setGrant(true); }}>Grant ownership</ActionButton></div></section>}
    {grant && <ConfirmationDialog title="Grant ownership" keepLabel="Keep current role" confirmLabel="Confirm ownership grant" pending={pending} confirmDisabled={uncertain} onKeep={() => setGrant(false)}
      onConfirm={() => void submit(onGrant, () => setGrant(false))}>
      Give this member full Owner authority, including business administration and recovery approval?
      {error !== undefined && <ErrorNotice error={error} />}
      {uncertain && <p role="alert">The outcome could not be confirmed. Close this dialog and reload the member list before making another change.</p>}
    </ConfirmationDialog>}
    {confirm && <ConfirmationDialog title="Change membership" keepLabel="Keep current membership" confirmLabel="Confirm membership change" pending={pending} confirmDisabled={uncertain} onKeep={() => setConfirm(false)}
      onConfirm={() => void submit(() => onSave(role, active), () => setConfirm(false))}>
      Set this member to {roleNames[role]} and {active ? 'active' : 'inactive'}? Access changes on their next request. A business must retain an active Owner.
      {error !== undefined && <ErrorNotice error={error} />}
      {uncertain && <p role="alert">The outcome could not be confirmed. Close this dialog and reload the member list before making another change.</p>}
    </ConfirmationDialog>}
    {error !== undefined && !confirm && !grant && <ErrorNotice error={error} />}
    {uncertain && !confirm && !grant && <p role="alert">Reload the member list to inspect the outcome before making another change.</p>}
  </section>;
}

function MemberAdministration() {
  const session = useEntity();
  const cache = useQueryClient();
  const owner = session.capabilities.includes('ownership:manage');
  const key = resourceKey(session, 'members');
  const invitationKey = resourceKey(session, 'invitations');
  const members = useQuery({ queryKey: key, queryFn: ({ signal }) => api.members(session, signal) });
  const invitations = useQuery({ queryKey: invitationKey, queryFn: ({ signal }) => api.invitations(session, signal) });
  const [result, setResult] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Schemas['InviteRequest']['role']>('read_only');
  const [issued, setIssued] = useState<string>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const submitting = useRef(false);
  async function invite() {
    if (submitting.current) return;
    submitting.current = true;
    setIssued(undefined); setError(undefined); setPending(true);
    try {
      const result = await api.invite(session, { email, role });
      setIssued(result.credential); setEmail('');
    } catch (failure) { setError(failure); }
    finally { await cache.invalidateQueries({ queryKey: invitationKey }); setPending(false); submitting.current = false; }
  }
  return <><form className="surface" onSubmit={event => { event.preventDefault(); void invite(); }}>
    <FormSection title="Invite to this business"><p>{session.entity.name} · Business reference: {session.entity.id}</p><Field id="invite-email" label="Recipient email"><input id="invite-email" type="email" value={email} required maxLength={320} disabled={pending} onChange={event => setEmail(event.target.value)} /></Field>
      <Field id="invite-role" label="Invitation role"><select id="invite-role" value={role} disabled={pending} onChange={event => setRole(event.target.value as typeof role)}>
        {(['admin', 'editor', 'accountant', 'read_only'] as const).filter(item => owner || item !== 'admin').map(item => <option key={item} value={item}>{roleNames[item]}</option>)}
      </select></Field></FormSection>
    <p>Share the credential privately with the intended recipient. It expires after 48 hours and can be used once.</p>
    <div className="actions"><ActionButton type="submit" disabled={pending}>Create invitation</ActionButton></div>
  </form>
  {issued && <section className="notice neutral"><h2>Share this credential once</h2><p>This value disappears when you leave this business or dismiss it.</p>
    <Field id="issued-invitation" label="New invitation credential"><input id="issued-invitation" readOnly value={issued} autoComplete="off" spellCheck={false} /></Field>
    <ActionButton onClick={() => setIssued(undefined)}>Dismiss credential</ActionButton></section>}
  {error !== undefined && <ErrorNotice error={error} />}
  <h2>Members</h2>
  {result && <SuccessNotice>{result}</SuccessNotice>}
  {members.isPending && <LoadingState />}{members.error && <ErrorNotice error={members.error} operation="read" />}
  {members.data?.items.map(member => <MemberEditor key={member.user_id + ':' + member.authorization_version} member={member} owner={owner} onSave={async (role, active) => {
    setResult('');
    try {
      await api.changeMember(session, member.user_id, { role, active, authorization_version: member.authorization_version });
      setResult('Membership change confirmed.');
      window.dispatchEvent(new Event('tabula:identity-check'));
      notifyPeerAuthorityCheck();
    } finally { await cache.invalidateQueries({ queryKey: key }); }
  }} onGrant={async () => {
    setResult('');
    try { await api.grantOwnership(session, member.user_id, member.authorization_version); setResult('Ownership grant confirmed.'); window.dispatchEvent(new Event('tabula:identity-check')); notifyPeerAuthorityCheck(); }
    finally { await cache.invalidateQueries({ queryKey: key }); }
  }} />)}
  <h2>Invitations</h2>
  {invitations.error && <ErrorNotice error={invitations.error} operation="read" />}
  {invitations.data?.items.length === 0 && <p>No invitations.</p>}
  {invitations.data?.items.map(item => <section className="surface" key={item.id}><p>{item.recipient_email} · {roleNames[item.role]} · {item.state} · expires {new Date(item.expires_at).toLocaleString()}</p>
    {item.state === 'pending' && (owner || item.role !== 'admin') && <ActionButton disabled={pending} onClick={() => {
      if (submitting.current) return;
      submitting.current = true;
      setPending(true); setError(undefined); void api.revokeInvitation(session, item.id).catch(setError).finally(async () => { await cache.invalidateQueries({ queryKey: invitationKey }); setPending(false); submitting.current = false; });
    }}>Revoke invitation</ActionButton>}</section>)}
  </>;
}

export function WorkspacePage() {
  const session = useEntity();
  return <div className="workflowSections"><PageHeader title="Business & members" description={session.entity.name} />
    <p>Business reference: {session.entity.id}</p>
    <p>Manage business settings, members and invitations for the selected business.</p>
    {session.capabilities.includes('members:manage') && !session.trusted_development && !session.closure_expires_at && <BusinessCurrency />}
    {session.capabilities.includes('members:manage') && !session.trusted_development ? <MemberAdministration /> : <p>Business administration requires Owner or Admin access.</p>}
    {!session.trusted_development && <InvitationAcceptance session={session} />}
  </div>;
}
