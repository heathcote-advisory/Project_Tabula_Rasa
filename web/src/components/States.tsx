import type { ReactNode } from 'react';
import { ApiError } from '../api/client';

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return <p role="status" aria-live="polite" className="notice neutral">{label}</p>;
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return <section className="empty"><h2>{title}</h2>{children && <p>{children}</p>}</section>;
}

export function SuccessNotice({ children }: { children: ReactNode }) {
  return <p role="status" className="notice success">{children}</p>;
}

const messages = {
  validation: 'Check the highlighted fields and try again.',
  conflict: 'This record changed or conflicts with another record. Your draft is preserved.',
  period_closed: 'This accounting month is closed. Your draft is preserved. Use an authorized reversal in an open month or ask the Owner to unlock it.',
  identity: 'Business access is unavailable. Sign in again or check your membership.',
  read: 'The requested data could not be loaded. Reload to try again.',
  unexpected: 'The request could not be completed. Your draft is preserved. Check the latest record before trying a save again.',
};

export function ErrorNotice({ error, onReview, operation = 'write', reauthenticationPath }: { error: unknown; onReview?: () => void; operation?: 'read' | 'write'; reauthenticationPath?: '/invitations' }) {
  const failure = error instanceof ApiError ? error : new ApiError('unexpected');
  const conflict = ['stale', 'conflict', 'duplicate', 'concurrent_write', 'period_closed', 'recurring_in_use'].includes(failure.code);
  const validation = ['validation', 'reference', 'invariant', 'required', 'baseline_required', 'baseline_invalid'].includes(failure.code);
  const identity = ['unauthorized', 'forbidden'].includes(failure.code);
  const invitationMessages: Record<string, string> = {
    recurring_in_use: 'This schedule no longer has only its original occurrence. Reload to inspect it, or End the schedule to stop future generation while retaining history.',
    reauthentication_required: 'Sign in again to continue. Re-enter any invitation credential after login.',
    invitation_invalid: 'This invitation is unavailable for this account. Check the invited email with your administrator.',
    invitation_used: 'This invitation was already redeemed, declined or revoked. Ask your administrator for a new one.',
    invitation_expired: 'This invitation expired. Ask your administrator for a new one.',
  };
  return <div role="alert" className={'notice ' + (conflict ? 'warning' : 'error')}>
    <p>{failure.code === 'period_closed' ? messages.period_closed : invitationMessages[failure.code] ?? messages[conflict ? 'conflict' : validation ? 'validation' : identity ? 'identity' : operation === 'read' ? 'read' : 'unexpected']}</p>
    {failure.fields.length > 0 && <ul>{failure.fields.map(field =>
      <li key={field.field}>{field.field}: Check this value.</li>)}</ul>}
    {failure.requestId && <p className="muted">Request reference: {failure.requestId}</p>}
    {failure.code === 'reauthentication_required' && <a href={reauthenticationPath ? '/api/v1/auth/login?return_path=' + reauthenticationPath : '/api/v1/auth/login'}>Sign in again</a>}
    {onReview && <button type="button" onClick={event => { event.currentTarget.focus(); onReview(); }}>{conflict ? 'Review latest record' : 'Reload'}</button>}
  </div>;
}

export function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: ReactNode }) {
  return <div className="field"><label htmlFor={id}>{label}</label>{children}
    {error && <p id={id + '-error'} role="alert" className="fieldError">{error}</p>}
  </div>;
}
