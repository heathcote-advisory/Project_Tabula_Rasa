import { PolicyAcknowledgement, PolicyLinks } from '../features/identity/PolicyPages';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api, ApiError, type Schemas, type SessionState } from '../api/client';
import { ErrorNotice, LoadingState } from '../components/States';
import { ScopeBoundary } from './ScopeBoundary';
import { EntitySelectionContext } from './entitySelectionContext';
import { AuthContext } from './authContext';
import { ActionButton, PageHeader } from '../components/VisualPrimitives';
import { InvitationAcceptance } from '../features/identity/InvitationAcceptance';
import { Onboarding } from '../features/identity/Onboarding';
import { CustomerLifecycle } from '../features/identity/CustomerLifecycle';

const authorityKey = (session: SessionState) => JSON.stringify([session.actor_id, session.cache_scope,
  session.organization_id, session.entity?.id, session.capabilities]);

function LoginFailure() {
  const query = new URLSearchParams(window.location.search);
  const reason = query.get('reason');
  if (reason !== 'login_failed' && reason !== 'unavailable') return null;
  const messages: Record<string, string> = {
    login_start: 'The sign-in service could not start a new login.',
    login_transaction: 'This sign-in attempt expired, was already used, or could not be matched to this browser.',
    provider_authorization: 'The sign-in provider did not complete authentication.',
    token_exchange: 'The sign-in provider could not complete the secure login exchange.',
    token_validation: 'The sign-in response could not be verified.',
    login_freshness: 'The provider session did not confirm a recent sign-in. Please authenticate again.',
    userinfo_validation: 'Your verified account details could not be confirmed with the sign-in provider.',
    session_issue: 'The application could not establish a new session.',
  };
  const category = query.get('category') ?? '';
  const reference = query.get('reference') ?? '';
  return <div role="alert">
    <p>{reason === 'unavailable' ? 'The sign-in service is temporarily unavailable.'
      : Object.hasOwn(messages, category) ? messages[category] : 'Sign-in did not complete.'}</p>
    <p>Use Sign in to restart with a new attempt and authenticate with the provider. If it fails again, contact your operator with the request reference. You do not need to delete browser cookies.</p>
    {/^[a-f0-9]{32}$/.test(reference) && <p>Request reference: <code>{reference}</code></p>}
  </div>;
}

export function ContextBoundary({ children, onScopeChange }: { children: ReactNode; onScopeChange?: () => void }) {
  const [state, setState] = useState<{ session?: SessionState; error?: unknown; signedOut?: boolean }>(
    () => window.location.pathname === '/signed-out' ? { signedOut: true } : {});
  const identityGeneration = useRef(0);
  const resetRoute = useRef(false);
  const authChannel = useRef<BroadcastChannel | undefined>(undefined);
  // Retain no private identity/entity data after a failed authority check.
  const logoutContext = useRef<{ csrf_token: string } | undefined>(undefined);
  const [generation, setGeneration] = useState(0);
  const [entities, setEntities] = useState<Schemas['EntityInfo'][]>([]);
  const [entityError, setEntityError] = useState<unknown>();
  const [switching, setSwitching] = useState(false);
  const selecting = useRef(false);
  const [selectionError, setSelectionError] = useState<unknown>();
  useEffect(() => {
    if (resetRoute.current && state.session) {
      resetRoute.current = false;
      onScopeChange?.();
    }
  }, [state.session, onScopeChange]);
  useEffect(() => {
    // Listen while initial/retry loading is pending, too: a late response must
    // not restore private authority after another tab has signed out.
    const channel = typeof BroadcastChannel === 'undefined' ? undefined : new BroadcastChannel('tabula-auth');
    authChannel.current = channel;
    if (channel) channel.onmessage = event => {
      if (event.data === 'authority-check') {
        window.dispatchEvent(new Event('tabula:identity-check'));
        return;
      }
      if (event.data === 'scope-changed') {
        resetRoute.current = true;
        identityGeneration.current++; setEntities([]); setState({}); setGeneration(value => value + 1);
        return;
      }
      if (event.data !== 'signed-out') return;
      logoutContext.current = undefined;
      identityGeneration.current++; setEntities([]); setEntityError(undefined); setSelectionError(undefined);
      setState({ signedOut: true });
    };
    return () => { channel?.close(); authChannel.current = undefined; };
  }, []);
  useEffect(() => {
    if (!state.session || state.session.trusted_development) return;
    const controller = new AbortController();
    const expected = identityGeneration.current;
    let checking = false;
    const check = async () => {
      if (checking || document.visibilityState === 'hidden') return;
      checking = true;
      try {
        const [session, choices] = await Promise.all([api.session(controller.signal), api.entities(controller.signal)]);
        if (!controller.signal.aborted && expected === identityGeneration.current) {
          if (authorityKey(session) !== authorityKey(state.session!)) {
            resetRoute.current = true;
            identityGeneration.current++;
            setEntities([]); setEntityError(undefined); setSelectionError(undefined);
            setGeneration(value => value + 1);
          }
          logoutContext.current = { csrf_token: session.csrf_token };
          setEntities(choices.items); setEntityError(undefined);
          setState({ session });
        }
      } catch (error) {
        if (!controller.signal.aborted && expected === identityGeneration.current) {
          identityGeneration.current++; setEntities([]); setEntityError(undefined); setSelectionError(undefined);
          setState({ error });
        }
      } finally { checking = false; }
    };
    let lastActivity = 0;
    const activity = () => {
      if (document.visibilityState === 'hidden' || Date.now() - lastActivity < 60_000) return;
      lastActivity = Date.now();
      void api.activity(state.session!).catch(() => void check());
    };
    const timer = window.setInterval(() => void check(), 60_000);
    window.addEventListener('focus', check);
    window.addEventListener('pageshow', check);
    window.addEventListener('tabula:identity-check', check);
    document.addEventListener('visibilitychange', check);
    window.addEventListener('pointerdown', activity);
    window.addEventListener('keydown', activity);
    return () => {
      controller.abort(); window.clearInterval(timer);
      window.removeEventListener('focus', check); window.removeEventListener('pageshow', check);
      window.removeEventListener('tabula:identity-check', check);
      document.removeEventListener('visibilitychange', check);
      window.removeEventListener('pointerdown', activity); window.removeEventListener('keydown', activity);
    };
  }, [state.session]);
  async function logout() {
    const context = logoutContext.current;
    logoutContext.current = undefined;
    // Unmount immediately; server failure never keeps private drafts on screen.
    identityGeneration.current++;
    setEntities([]); setEntityError(undefined); setSelectionError(undefined);
    setState({ signedOut: true });
    // Use the listener's channel so this tab does not receive its own message
    // and erase a subsequent server-revocation failure notice.
    authChannel.current?.postMessage('signed-out');
    try { await api.logout(context); }
    catch (error) { setState({ error, signedOut: true }); }
  }
  useEffect(() => {
    if (window.location.pathname === '/signed-out') return;
    const controller = new AbortController();
    const expected = identityGeneration.current;
    api.session(controller.signal).then(
      session => {
        if (!controller.signal.aborted && expected === identityGeneration.current) {
          logoutContext.current = { csrf_token: session.csrf_token };
          setState({ session });
          if (session.state === 'needs_setup') { setEntities([]); return; }
          api.entities(controller.signal).then(
            result => { if (!controller.signal.aborted && expected === identityGeneration.current) { setEntities(result.items); setEntityError(undefined); } },
            error => { if (!controller.signal.aborted && expected === identityGeneration.current) { setEntities([]); setEntityError(error); } },
          );
        }
      },
      error => { if (!controller.signal.aborted && expected === identityGeneration.current) setState({ error }); },
    );
    return () => controller.abort();
  }, [generation]);
  if (state.signedOut || state.error instanceof ApiError && state.error.code === 'unauthorized')
    return <main className="standalone"><section className="surface"><PageHeader title="Project Tabula Rasa" description="You are signed out. Sign in to continue." />
      {state.error !== undefined && <ErrorNotice error={state.error} />}
      {window.location.pathname === '/signed-out' && <LoginFailure />}
      <p>New here? Choose Create account to register with our secure sign-in provider using your invited email, verify it, and set up the required authentication factors. Already have an account? Choose Sign in.</p>
      <p>Creating an account does not create a business or grant access. After registration, join your invited business using the credential shared privately by its administrator. Business creation requires separate approval.</p>
      <p>If registration is unavailable or you cannot complete email verification or factor setup, contact your service operator through your existing private support channel. You can still use Sign in with an existing account.</p>
      <p>If your login was closed or disabled, contact your service operator through your existing private support channel. Signing in does not reactivate a closed login.</p>
      <PolicyLinks /><div className="actions"><a className="button" href={window.location.pathname === '/invitations' ? '/api/v1/auth/login?return_path=/invitations' : '/api/v1/auth/login'}>Sign in</a>
        <a className="button" href="/api/v1/auth/register?return_path=/invitations">Create account</a></div></section></main>;
  if (state.error) return <main className="standalone"><section className="surface"><PageHeader title="Project Tabula Rasa" />
    <ErrorNotice error={state.error} operation="read" onReview={() => { setState({}); setGeneration(value => value + 1); }} />
    <div className="actions"><ActionButton onClick={() => void logout()}>Sign out</ActionButton></div>
  </section></main>;
  if (!state.session) return <main className="standalone"><LoadingState label="Loading business context…" /></main>;
  if (state.session.state === 'needs_setup' || window.location.pathname === '/invitations') return <main className="standalone"><section className="surface">
    <PolicyAcknowledgement key={'policies:' + state.session.actor_id} session={state.session} />
    {state.session.state === 'needs_setup' ? <Onboarding key={state.session.actor_id} session={state.session} />
      : <><PageHeader title="Join an invited business" /><InvitationAcceptance key={state.session.actor_id} session={state.session} /><div className="actions"><a className="button" href="/">Return to your dashboard</a></div></>}
    <CustomerLifecycle key={'lifecycle:' + state.session.actor_id + state.session.cache_scope} session={state.session} />
    <div className="actions"><ActionButton onClick={() => void logout()}>Sign out</ActionButton></div></section></main>;
  async function select(id: string) {
    if (!state.session || state.session.state === 'needs_setup' || selecting.current) return;
    selecting.current = true;
    setSwitching(true);
    if (!state.session.trusted_development) identityGeneration.current++;
    const expected = identityGeneration.current;
    setSelectionError(undefined);
    setEntityError(undefined);
    try {
      const session = await api.selectEntity(state.session, id);
      if (expected === identityGeneration.current) {
        resetRoute.current = !session.trusted_development;
        logoutContext.current = { csrf_token: session.csrf_token };
        setState({ session });
        if (!session.trusted_development) {
          setEntities([]);
          void api.entities().then(
            result => { if (expected === identityGeneration.current) setEntities(result.items); },
            error => { if (expected === identityGeneration.current) setEntityError(error); },
          );
        }
        if (!session.trusted_development) authChannel.current?.postMessage('scope-changed');
      }
    }
    catch (error) {
      if (expected === identityGeneration.current) {
        if (!state.session.trusted_development) setState({ error });
        else setSelectionError(error);
      }
    }
    finally { selecting.current = false; setSwitching(false); }
  }
  return <AuthContext.Provider value={{ logout: () => void logout() }}><EntitySelectionContext.Provider value={{ items: entities, select, pending: switching }}>
    {switching && <LoadingState label="Switching business…" />}
    {selectionError !== undefined && <div className="standalone">
      <p>Business switching could not be confirmed. Your current business and draft are preserved.</p>
      <ErrorNotice error={selectionError} />
    </div>}
    {entityError !== undefined && <div className="standalone">
      <p>Business choices could not be loaded.</p><ErrorNotice error={entityError} onReview={() => window.location.reload()} />
    </div>}
    {!(switching && !state.session.trusted_development) && <div inert={switching}><ScopeBoundary session={state.session}>{children}</ScopeBoundary></div>}
  </EntitySelectionContext.Provider></AuthContext.Provider>;
}
