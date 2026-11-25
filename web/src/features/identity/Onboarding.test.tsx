import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { api, ApiError, type SessionState } from '../../api/client';
import { session } from '../../test/fixtures';
import { BusinessesPage } from './BusinessesPage';
import { ScopeBoundary } from '../../app/ScopeBoundary';
import { EntitySelectionContext } from '../../app/entitySelectionContext';
import { LedgerSetup, Onboarding } from './Onboarding';
import { ContextBoundary } from '../../app/ContextBoundary';
import { InvitationAcceptance } from './InvitationAcceptance';

const setup: SessionState = { state: 'needs_setup', actor_id: 'new-user', organization_id: null,
  entity: null, capabilities: [], cache_scope: 'setup', csrf_token: 'csrf', trusted_development: false, business_creation_available: true };
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); window.history.replaceState({}, '', '/'); });

async function review() {
  await screen.findByLabelText('Business name');
  expect(screen.queryByLabelText('Workspace name')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Business name'), { target: { value: 'Business' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review defaults' }));
}

it('separates fresh books, invitations and operator migration without automatic creation', async () => {
  const create = vi.spyOn(api, 'createBusiness');
  const view = render(<Onboarding session={setup} />);
  fireEvent.click(screen.getByRole('button', { name: 'Move existing Sheets books' }));
  expect(screen.getByText(/explicit migration and cutover/)).toBeVisible();
  expect(create).not.toHaveBeenCalled();
  view.unmount();
  window.history.replaceState({}, '', '/invitations');
  render(<Onboarding session={setup} />);
  expect(screen.getByLabelText('Invitation credential')).toBeVisible();
  expect(screen.queryByLabelText('Business name')).not.toBeInTheDocument();
});

it('discloses beta feature exclusions and the accountant handoff during first-use setup', async () => {
  vi.spyOn(api, 'betaStatus').mockRejectedValue(new ApiError('unavailable'));
  const user = userEvent.setup();
  render(<Onboarding session={setup} />);
  const exclusions = screen.getByText(/bank feeds, invoicing, payroll, inventory/);
  expect(exclusions).not.toBeVisible();
  await user.click(screen.getByText('Beta scope and service limits'));
  expect(exclusions).toBeVisible();
  expect(exclusions).toHaveTextContent('category template imports, standalone report downloads and compliance or audit exports');
  expect(exclusions).toHaveTextContent('The accountant handoff is a bookkeeping archive.');
  expect(screen.getByText(/Depreciation posting and period overrides are unavailable/)).toBeVisible();
});

it('preserves names on Back, validates whitespace, and reuses the exact request on explicit retry', async () => {
  const create = vi.spyOn(api, 'createBusiness').mockRejectedValue(new ApiError('unavailable'));
  vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'not_started' });
  render(<LedgerSetup session={setup} />);
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(screen.getByLabelText('Business name')).toHaveValue('Business');
  fireEvent.change(screen.getByLabelText('Business name'), { target: { value: '   ' } });
  expect(screen.getByRole('button', { name: 'Review defaults' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Business name'), { target: { value: 'Business' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review defaults' }));
  fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry same setup' }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[0][1]).toEqual(create.mock.calls[1][1]);
  expect(sessionStorage.length).toBe(1);
  expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(create.mock.calls[0][1].request_id);
  expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
});

it('resumes only the authenticated actor receipt after refresh and never restores private names', async () => {
  sessionStorage.setItem('tabula-setup:new-user:first', 'original-request-id');
  sessionStorage.setItem('tabula-setup:other-user:first', 'foreign-request-id');
  const status = vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'not_started' });
  render(<LedgerSetup session={setup} />);
  expect(await screen.findByText(/Setup was interrupted/)).toBeVisible();
  expect(status).toHaveBeenCalledWith('original-request-id', expect.any(AbortSignal));
  expect(screen.getByLabelText('Business name')).toHaveValue('');
});

it('uses a bounded readback after lost response, then selects only through authorized API', async () => {
  vi.spyOn(api, 'createBusiness').mockRejectedValue(new ApiError('unavailable'));
  const status = vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'ready', entity_id: 'created' });
  vi.spyOn(api, 'session').mockResolvedValue({ ...session, actor_id: setup.actor_id, trusted_development: false });
  const select = vi.spyOn(api, 'selectEntity').mockRejectedValue(new ApiError('forbidden'));
  render(<LedgerSetup session={setup} />);
  await review(); fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  await screen.findByRole('alert');
  expect(status).toHaveBeenCalledTimes(1);
  expect(select).toHaveBeenCalledWith(expect.objectContaining({ actor_id: setup.actor_id }), 'created');
  expect(sessionStorage.length).toBe(1);
});

it('does not create if the receipt cannot be stored', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
  const create = vi.spyOn(api, 'createBusiness');
  render(<LedgerSetup session={setup} />);
  await review(); fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  await screen.findByRole('alert'); expect(create).not.toHaveBeenCalled();
});

it('does not reinterpret a rejected request as uncertain success', async () => {
  vi.spyOn(api, 'createBusiness').mockRejectedValue(new ApiError('conflict'));
  const status = vi.spyOn(api, 'bootstrapStatus');
  render(<LedgerSetup session={setup} />);
  await review(); fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  await screen.findByRole('alert');
  expect(status).not.toHaveBeenCalled();
});

it('returns invitees to acceptance after required reauthentication and clears the credential', async () => {
  vi.spyOn(api, 'previewInvitation').mockRejectedValue(new ApiError('reauthentication_required'));
  render(<InvitationAcceptance session={setup} />);
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'private-credential' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review invitation' }));
  expect(await screen.findByRole('link', { name: 'Sign in again' })).toHaveAttribute('href', '/api/v1/auth/login?return_path=/invitations');
  expect(screen.getByLabelText('Invitation credential')).toHaveValue('');
});

it('routes an existing invited member to acceptance and preserves that route through login', async () => {
  window.history.replaceState({}, '', '/invitations');
  vi.spyOn(api, 'session').mockRejectedValueOnce(new ApiError('unauthorized')).mockResolvedValue({ ...session, trusted_development: false });
  vi.spyOn(api, 'entities').mockResolvedValue({ items: [session.entity] });
  const first = render(<ContextBoundary><p>Private dashboard</p></ContextBoundary>);
  expect(await screen.findByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/api/v1/auth/login?return_path=/invitations');
  first.unmount();
  render(<ContextBoundary><p>Private dashboard</p></ContextBoundary>);
  expect(await screen.findByLabelText('Invitation credential')).toBeVisible();
  expect(screen.queryByText('Private dashboard')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Create your first business' })).not.toBeInTheDocument();
});


it('offers independent business creation to an existing read-only member and lists current access', async () => {
  const member = { ...session, trusted_development: false, capabilities: ['bookkeeping:read'], business_creation_available: true };
  const select = vi.fn();
  render(<ScopeBoundary session={member}><EntitySelectionContext.Provider value={{ pending: false, select,
    items: [{ ...member.entity, role: 'read_only' }, { currency_code: 'USD' as const, id: 'other', name: 'Other business', role: 'owner' }] }}>
    <BusinessesPage /></EntitySelectionContext.Provider></ScopeBoundary>);
  expect(screen.getByRole('heading', { name: 'My Businesses' })).toBeVisible();
  expect(screen.getByText('Read-only')).toBeVisible();
  expect(screen.getByText('Other business')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Create another business' }));
  await review();
  expect(screen.queryByText(/Workspace:/)).not.toBeInTheDocument();
});

it('retains invitation and migration paths while hiding creation when runtime bootstrap is disabled', () => {
  const member = { ...session, trusted_development: false, business_creation_available: false };
  const view = render(<ScopeBoundary session={member}><BusinessesPage /></ScopeBoundary>);
  expect(screen.queryByRole('button', { name: 'Create another business' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Join an invited business' })).toBeVisible();
  view.unmount();
  render(<Onboarding session={{ ...setup, business_creation_available: false }} />);
  expect(screen.queryByRole('button', { name: 'Create your first business' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Join an invited business' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Move existing Sheets books' })).toBeVisible();
});

it('keeps creation pending and submits a business-only payload once', async () => {
  let reject!: (error: unknown) => void;
  const create = vi.spyOn(api, 'createBusiness').mockImplementation(() => new Promise((_, no) => { reject = no; }));
  render(<LedgerSetup session={setup} />);
  await review();
  const submit = screen.getByRole('button', { name: 'Create business' });
  fireEvent.click(submit); fireEvent.click(submit);
  expect(submit).toBeDisabled();
  expect(await screen.findByText('Submitting your business setup…')).toBeVisible();
  expect(create).toHaveBeenCalledTimes(1);
  expect(create.mock.calls[0][1]).toEqual({ request_id: expect.any(String), business_name: 'Business', country: 'US', currency: 'USD' });
  reject(new ApiError('validation'));
  expect(await screen.findByRole('alert')).toBeVisible();
});


it('retries selection of a confirmed refreshed receipt without submitting creation again', async () => {
  sessionStorage.setItem('tabula-setup:new-user:first', 'original-request-id');
  vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'ready', entity_id: 'created' });
  vi.spyOn(api, 'session').mockResolvedValue({ ...session, actor_id: setup.actor_id, trusted_development: false });
  const select = vi.spyOn(api, 'selectEntity').mockRejectedValue(new ApiError('forbidden'));
  const create = vi.spyOn(api, 'createBusiness');
  render(<LedgerSetup session={setup} />);
  await screen.findByRole('alert');
  expect(screen.getByText('Your business was created. Retry to open its ledger.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry same setup' }));
  await waitFor(() => expect(select).toHaveBeenCalledTimes(2));
  expect(create).not.toHaveBeenCalled();
  expect(sessionStorage.getItem('tabula-setup:new-user:first')).toBe('original-request-id');
});

it('starts an additional business independently of stale first-use and other-actor receipts', async () => {
  const member = { ...session, actor_id: setup.actor_id, trusted_development: false };
  sessionStorage.setItem('tabula-setup:new-user:first', 'completed-first-request');
  sessionStorage.setItem('tabula-setup:other-user:additional:alpha', 'foreign-request');
  sessionStorage.setItem('tabula-setup:new-user:additional:old-business', 'old-context-request');
  const status = vi.spyOn(api, 'bootstrapStatus').mockRejectedValue(new ApiError('forbidden'));
  const create = vi.spyOn(api, 'createBusiness').mockRejectedValue(new ApiError('validation'));
  render(<LedgerSetup session={member} />);
  await review();
  expect(status).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  await screen.findByRole('alert');
  const payload = create.mock.calls[0][1];
  expect(payload.request_id).not.toBe('completed-first-request');
  expect(payload.request_id).not.toBe('foreign-request');
  expect(payload.request_id).not.toBe('old-context-request');
  expect(sessionStorage.getItem('tabula-setup:new-user:additional:alpha')).toBe(payload.request_id);
  expect(sessionStorage.getItem('tabula-setup:new-user:first')).toBe('completed-first-request');
});

it('resumes an interrupted additional creation and retries the same payload after refresh', async () => {
  const member = { ...session, actor_id: setup.actor_id, trusted_development: false };
  sessionStorage.setItem('tabula-setup:new-user:first', 'completed-first-request');
  sessionStorage.setItem('tabula-setup:new-user:additional:alpha', 'additional-request');
  const status = vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'not_started' });
  const create = vi.spyOn(api, 'createBusiness').mockRejectedValue(new ApiError('validation'));
  render(<LedgerSetup session={member} />);
  expect(await screen.findByText(/Setup was interrupted/)).toBeVisible();
  expect(status).toHaveBeenCalledExactlyOnceWith('additional-request', expect.any(AbortSignal));
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry same setup' }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[0][1]).toEqual({ request_id: 'additional-request', business_name: 'Business', country: 'US', currency: 'USD' });
  expect(create.mock.calls[1][1]).toEqual(create.mock.calls[0][1]);
});

it('resumes a preceding workspace receipt and retries only authorized selection', async () => {
  const member = { ...session, actor_id: setup.actor_id, trusted_development: false };
  const oldSlot = `tabula-setup:${member.actor_id}:${member.organization_id}`;
  sessionStorage.setItem(oldSlot, 'workspace-request');
  const status = vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'ready', entity_id: 'existing-business' });
  vi.spyOn(api, 'session').mockResolvedValue(member);
  const select = vi.spyOn(api, 'selectEntity').mockRejectedValue(new ApiError('forbidden'));
  const create = vi.spyOn(api, 'createBusiness');
  render(<LedgerSetup session={member} />);
  await screen.findByRole('alert');
  expect(status).toHaveBeenCalledExactlyOnceWith('workspace-request', expect.any(AbortSignal));
  fireEvent.click(screen.getByRole('button', { name: 'Retry same setup' }));
  await waitFor(() => expect(select).toHaveBeenCalledTimes(2));
  expect(create).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(oldSlot)).toBe('workspace-request');
});

it('keeps the preceding workspace request ID on an uncommitted additional setup', async () => {
  const member = { ...session, actor_id: setup.actor_id, trusted_development: false };
  sessionStorage.setItem(`tabula-setup:${member.actor_id}:${member.organization_id}`, 'workspace-request');
  vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'not_started' });
  const create = vi.spyOn(api, 'createBusiness').mockRejectedValue(new ApiError('validation'));
  render(<LedgerSetup session={member} />);
  expect(await screen.findByText(/Setup was interrupted/)).toBeVisible();
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  await screen.findByRole('alert');
  expect(create).toHaveBeenCalledExactlyOnceWith(member, { request_id: 'workspace-request', business_name: 'Business', country: 'US', currency: 'USD' });
});


it('shows pending enrollment and retries the same approved request without reporting success early', async () => {
  vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'not_started', entity_id: null });
  const create = vi.spyOn(api, 'createBusiness').mockResolvedValue({ state: 'pending', entity_id: null });
  const select = vi.spyOn(api, 'selectEntity');
  render(<LedgerSetup session={setup} />);
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  expect(await screen.findByText(/Enrollment: pending/)).toBeVisible();
  expect(select).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry same setup' }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[0][1]).toEqual(create.mock.calls[1][1]);
});

it('keeps Canadian eligibility explicit and reviews the fixed CAD currency', async () => {
  vi.spyOn(api, 'bootstrapStatus').mockResolvedValue({ state: 'not_started', entity_id: null });
  const create = vi.spyOn(api, 'createBusiness').mockResolvedValue({ state: 'pending', entity_id: null });
  render(<LedgerSetup session={setup} />);
  await screen.findByLabelText('Business name');
  fireEvent.change(screen.getByLabelText('Business country and currency'), { target: { value: 'CA' } });
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Create business' }));
  await screen.findByText(/Enrollment: pending/);
  expect(create.mock.calls[0][1]).toMatchObject({ country: 'CA', currency: 'CAD' });
});


it('shows the server invitation deadline before acceptance', async () => {
  const expires = '2026-11-02T15:00:00Z';
  vi.spyOn(api, 'previewInvitation').mockResolvedValue({ business_id: 'invited', business_name: 'Invited books', role: 'read_only', expires_at: expires });
  render(<InvitationAcceptance session={setup} />);
  fireEvent.change(screen.getByLabelText('Invitation credential'), { target: { value: 'synthetic-private-invitation' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review invitation' }));
  expect(await screen.findByText('Invitation expires: ' + new Date(expires).toLocaleString() + '.')).toBeVisible();
  expect(screen.getByText(/cannot outlive their approval/)).toBeVisible();
});
