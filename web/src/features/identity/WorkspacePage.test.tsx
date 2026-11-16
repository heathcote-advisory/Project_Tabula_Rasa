import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, ApiError, type ErrorCode } from '../../api/client';
import { session } from '../../test/fixtures';
import { ScopeBoundary } from '../../app/ScopeBoundary';
import { WorkspacePage } from './WorkspacePage';
import { InvitationAcceptance } from './InvitationAcceptance';
import * as authSignals from '../../app/authSignals';

afterEach(() => vi.restoreAllMocks());
async function reviewInvitation() {
  vi.spyOn(api, 'previewInvitation').mockResolvedValue({ business_id: 'alpha', business_name: 'Alpha', role: 'read_only', expires_at: '2026-10-25T00:00:00Z' });
  fireEvent.click(screen.getByRole('button', { name: 'Review invitation' }));
  await screen.findByRole('button', { name: 'Accept invitation' });
}

it('shows the specific offered business before responding and discards it when the credential changes', async () => {
  const preview = vi.spyOn(api, 'previewInvitation').mockResolvedValue({
    business_id: 'independent-b', business_name: 'Independent B', role: 'accountant', expires_at: '2026-10-25T00:00:00Z',
  });
  const respond = vi.spyOn(api, 'respondInvitation');
  render(<InvitationAcceptance session={session} />);
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'private-credential' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review invitation' }));
  expect(await screen.findByText('Business reference: independent-b')).toBeVisible();
  expect(screen.getByText('Your role in this business: Accountant')).toBeVisible();
  expect(preview).toHaveBeenCalledExactlyOnceWith(session, 'private-credential');
  expect(respond).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'different-credential' } });
  expect(screen.queryByRole('region', { name: 'Invited business' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Accept invitation' })).not.toBeInTheDocument();
});

it('does not retry a confirmed acceptance when subsequent business selection fails', async () => {
  const respond = vi.spyOn(api, 'respondInvitation').mockResolvedValue({ state: 'accepted' });
  const select = vi.spyOn(api, 'selectEntity').mockRejectedValue(new ApiError('forbidden'));
  render(<InvitationAcceptance session={session} />);
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'private-credential' } });
  await reviewInvitation();
  fireEvent.click(screen.getByRole('button', { name: 'Accept invitation' }));
  expect(await screen.findByText(/Invitation accepted.*reload My Businesses/)).toBeVisible();
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(respond).toHaveBeenCalledTimes(1);
  expect(select).toHaveBeenCalledExactlyOnceWith(session, 'alpha');
  expect(screen.getByLabelText('Invitation credential')).toHaveValue('');
  expect(screen.queryByRole('button', { name: 'Accept invitation' })).not.toBeInTheDocument();
});

it('stops after an uncertain invitation response and clears the private credential', async () => {
  const respond = vi.spyOn(api, 'respondInvitation').mockRejectedValue(new ApiError('unavailable'));
  render(<InvitationAcceptance session={session} />);
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'private-credential' } });
  await reviewInvitation();
  fireEvent.click(screen.getByRole('button', { name: 'Accept invitation' }));
  expect(await screen.findByText(/The invitation outcome could not be confirmed/)).toBeVisible();
  expect(screen.getByLabelText('Invitation credential')).toHaveValue('');
  expect(screen.getByLabelText('Invitation credential')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Review invitation' })).toBeDisabled();
  expect(respond).toHaveBeenCalledTimes(1);
});

it.each([
  ['reauthentication_required', 'Sign in again to continue. Re-enter any invitation credential after login.'],
  ['invitation_used', 'This invitation was already redeemed, declined or revoked. Ask your administrator for a new one.'],
  ['invitation_expired', 'This invitation expired. Ask your administrator for a new one.'],
  ['invitation_invalid', 'This invitation is unavailable for this account. Check the invited email with your administrator.'],
])('clears the submitted invitation credential and explains %s', async (code, message) => {
  const notify = vi.spyOn(authSignals, 'notifyPeerScopeChange');
  const respond = vi.spyOn(api, 'respondInvitation').mockRejectedValue(new ApiError(code as ErrorCode));
  render(<InvitationAcceptance session={session} />);
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'synthetic-credential' } });
  await reviewInvitation();
  fireEvent.click(screen.getByRole('button', { name: 'Accept invitation' }));
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.getByLabelText('Invitation credential')).toHaveValue('');
  expect(respond).toHaveBeenCalledWith(session, 'synthetic-credential', true);
  expect(notify).not.toHaveBeenCalled();
  if (code === 'reauthentication_required') expect(screen.getByRole('link', { name: 'Sign in again' })).toBeVisible();
});

it('allows an authenticated decline without retaining the credential', async () => {
  const notify = vi.spyOn(authSignals, 'notifyPeerScopeChange');
  vi.spyOn(api, 'respondInvitation').mockResolvedValue({ state: 'declined' });
  render(<InvitationAcceptance session={session} />);
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'synthetic-credential' } });
  await reviewInvitation();
  fireEvent.click(screen.getByRole('button', { name: 'Decline invitation' }));
  expect(await screen.findByText('Invitation declined.')).toBeVisible();
  expect(screen.getByLabelText('Invitation credential')).toHaveValue('');
  expect(notify).not.toHaveBeenCalled();
});

it('clears a previous decline notice before another invitation response', async () => {
  vi.spyOn(api, 'respondInvitation').mockResolvedValueOnce({ state: 'declined' })
    .mockRejectedValueOnce(new ApiError('invitation_invalid'));
  render(<InvitationAcceptance session={session} />);
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'first-credential' } });
  await reviewInvitation();
  fireEvent.click(screen.getByRole('button', { name: 'Decline invitation' }));
  expect(await screen.findByText('Invitation declined.')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'next-credential' } });
  await reviewInvitation();
  fireEvent.click(screen.getByRole('button', { name: 'Accept invitation' }));
  expect(screen.queryByText('Invitation declined.')).not.toBeInTheDocument();
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByText('Invitation declined.')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Invitation credential')).toHaveValue('');
});

it('read-only workspace UI offers invitation acceptance without fetching administration', () => {
  const members = vi.spyOn(api, 'members');
  const invitations = vi.spyOn(api, 'invitations');
  render(<ScopeBoundary session={{ ...session, trusted_development: false, capabilities: ['bookkeeping:read'] }}><WorkspacePage /></ScopeBoundary>);
  expect(screen.getByText('Business administration requires Owner or Admin access.')).toBeVisible();
  expect(screen.queryByLabelText('Business currency')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Invitation credential')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Create invitation' })).not.toBeInTheDocument();
  expect(members).not.toHaveBeenCalled();
  expect(invitations).not.toHaveBeenCalled();
});

it('refreshes invitation state after an uncertain create without exposing or retrying the credential', async () => {
  vi.spyOn(api, 'members').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'invitations').mockResolvedValueOnce({ items: [] }).mockResolvedValue({ items: [{
    id: 'issued', recipient_email: 'recipient@example.test', role: 'read_only', state: 'pending', expires_at: '2026-10-15T12:00:00Z',
  }] });
  const invite = vi.spyOn(api, 'invite').mockRejectedValue(new ApiError('unavailable'));
  render(<ScopeBoundary session={{ ...session, trusted_development: false, capabilities: ['bookkeeping:read', 'members:manage'] }}><WorkspacePage /></ScopeBoundary>);
  await screen.findByText('No invitations.');
  fireEvent.change(screen.getByLabelText('Recipient email'), { target: { value: 'recipient@example.test' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create invitation' }));
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(await screen.findByText(/recipient@example.test.*pending/)).toBeVisible();
  expect(screen.queryByLabelText('New invitation credential')).not.toBeInTheDocument();
  expect(invite).toHaveBeenCalledTimes(1);
});


it.each(['success', 'reauthentication_required', 'uncertain'] as const)('membership confirmation gives immediate feedback and handles %s without duplicate submissions', async outcome => {
  const member = { user_id: 'other', display_name: 'Synthetic member', role: 'read_only' as const, state: 'active' as const, authorization_version: 1 };
  vi.spyOn(api, 'members').mockResolvedValue({ items: [member] });
  vi.spyOn(api, 'invitations').mockResolvedValue({ items: [] });
  let resolve!: () => void;
  let reject!: (reason: unknown) => void;
  const change = vi.spyOn(api, 'changeMember').mockImplementation(() => new Promise<void>((yes, no) => { resolve = yes; reject = no; }));
  render(<ScopeBoundary session={{ ...session, trusted_development: false, capabilities: ['bookkeeping:read', 'members:manage', 'ownership:manage'] }}><WorkspacePage /></ScopeBoundary>);
  fireEvent.click(await screen.findByRole('button', { name: 'Review membership change' }));
  const confirm = screen.getByRole('button', { name: 'Confirm membership change' });
  fireEvent.click(confirm); fireEvent.click(confirm);
  expect(screen.getByText(/Working.*Please wait/)).toBeVisible();
  expect(confirm).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Keep current membership' })).toBeDisabled();
  expect(change).toHaveBeenCalledTimes(1);
  await act(async () => { if (outcome === 'success') resolve(); else reject(new ApiError(outcome === 'uncertain' ? 'unavailable' : outcome)); });
  expect(screen.queryByText(/Working.*Please wait/)).not.toBeInTheDocument();
  if (outcome === 'success') {
    expect(await screen.findByText('Membership change confirmed.')).toBeVisible();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  } else {
    expect(screen.getByRole('button', { name: 'Keep current membership' })).toBeEnabled();
    if (outcome === 'uncertain') {
      expect(screen.getByText(/The outcome could not be confirmed/)).toBeVisible();
      expect(confirm).toBeDisabled();
    } else {
      expect(screen.getByRole('link', { name: 'Sign in again' })).toBeVisible();
      expect(confirm).toBeEnabled();
    }
  }
  expect(change).toHaveBeenCalledTimes(1);
});

it.each(['membership', 'ownership'] as const)('preserves a confirmed %s concurrency rejection even when the member refresh fails', async operation => {
  const member = { user_id: 'other', display_name: 'Synthetic member', role: 'read_only' as const, state: 'active' as const, authorization_version: 1 };
  const members = vi.spyOn(api, 'members').mockResolvedValueOnce({ items: [member] }).mockRejectedValue(new ApiError('unexpected'));
  vi.spyOn(api, 'invitations').mockResolvedValue({ items: [] });
  const mutation = vi.spyOn(api, operation === 'membership' ? 'changeMember' : 'grantOwnership').mockRejectedValue(new ApiError('concurrent_write'));
  render(<ScopeBoundary session={{ ...session, trusted_development: false, capabilities: ['bookkeeping:read', 'members:manage', 'ownership:manage'] }}><WorkspacePage /></ScopeBoundary>);
  fireEvent.click(await screen.findByRole('button', { name: operation === 'membership' ? 'Review membership change' : 'Grant ownership' }));
  const dialog = within(screen.getByRole('alertdialog'));
  const confirm = dialog.getByRole('button', { name: operation === 'membership' ? 'Confirm membership change' : 'Confirm ownership grant' });
  fireEvent.click(confirm);
  expect(await dialog.findByText('This record changed or conflicts with another record. Your draft is preserved.')).toBeVisible();
  expect(confirm).toBeEnabled();
  expect(dialog.queryByText(/The outcome could not be confirmed/)).not.toBeInTheDocument();
  expect(members).toHaveBeenCalledTimes(2);
  expect(mutation).toHaveBeenCalledTimes(1);
});
