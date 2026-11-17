import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type SessionState } from '../../api/client';
import { PolicyAcknowledgement, PublicPolicyPage } from './PolicyPages';

afterEach(() => vi.restoreAllMocks());
const session = { actor_id: 'synthetic', csrf_token: 'synthetic-csrf' } as SessionState;
const digest = 'a'.repeat(64);

it('renders public policy content without requesting private identity', async () => {
  const privateRead = vi.spyOn(api, 'session');
  vi.spyOn(api, 'publicPolicies').mockResolvedValue({ digest, version: 'synthetic', effective_date: '2026-11-16',
    terms: '# Terms\n\n## Limits\n\n<script>never execute</script>', privacy: '', beta: false, support_email: null });
  render(<MemoryRouter><PublicPolicyPage kind="terms" /></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: 'Limits' })).toBeVisible();
  expect(screen.getByText('<script>never execute</script>')).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
  expect(privateRead).not.toHaveBeenCalled();
});

it('requires explicit unchecked acknowledgement and persists the loaded digest', async () => {
  vi.spyOn(api, 'policyStatus').mockResolvedValue({ digest, required: true, acknowledged: false });
  const accept = vi.spyOn(api, 'acceptPolicies').mockResolvedValue({ digest, required: true, acknowledged: true });
  const user = userEvent.setup();
  render(<MemoryRouter><PolicyAcknowledgement session={session} /></MemoryRouter>);
  const button = await screen.findByRole('button', { name: 'Record acknowledgement' });
  expect(button).toBeDisabled();
  await user.click(screen.getByRole('checkbox'));
  await user.click(button);
  expect(accept).toHaveBeenCalledWith(session, digest);
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});

it('shows reload guidance after an uncertain acceptance without automatic retry', async () => {
  vi.spyOn(api, 'policyStatus').mockResolvedValue({ digest, required: true, acknowledged: false });
  const accept = vi.spyOn(api, 'acceptPolicies').mockRejectedValue(new Error('synthetic failure'));
  const user = userEvent.setup();
  render(<MemoryRouter><PolicyAcknowledgement session={session} /></MemoryRouter>);
  await user.click(await screen.findByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'Record acknowledgement' }));
  expect(await screen.findByText(/Reload to check acknowledgement/)).toBeVisible();
  expect(accept).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button', { name: 'Record acknowledgement' })).not.toBeInTheDocument();
});
