# Named-user authentication and sessions

Named authentication uses hosted OIDC, server-owned PostgreSQL sessions and
application-owned memberships. [Provider setup](PROVIDER_SETUP.md) and
[ADR 0003](../adr/0003-commercial-identity-and-tenancy.md) define configuration
and security contracts. [Authorization](AUTHORIZATION.md) owns action policy;
[business onboarding](WORKSPACES.md) owns switching and onboarding. The
[local operator CLI](../SETUP.md#local-profile-administration) uses the application
password and opens loopback browser callbacks for Google consent and Picker.
Provider-hosted acceptance and live migration require their own approved procedures.

## Explicit deployment configuration

Run the normal explicit PostgreSQL migration command before deployment. Startup
never migrates, seeds, assigns old entities, or copies ledger data. Identity
revisions 0006/0007 are forward-only; compatible recovery, not a destructive
downgrade, is the rollback boundary. Entity bookkeeping exports retain the
canonical v1 inventory and exclude identity records, sessions and credentials.

Set `TABULA_RASA_ACCESS_MODE=named_user`, `TABULA_RASA_ENVIRONMENT`,
`TABULA_RASA_PUBLIC_ORIGIN`, the normal database configuration, and:

- `TABULA_RASA_OIDC_ISSUER`: exact HTTPS ZITADEL issuer, without trailing slash.
- `TABULA_RASA_OIDC_CLIENT_ID` and `TABULA_RASA_OIDC_CLIENT_SECRET`: server-side
  confidential Web application configured with Basic Auth and S256 PKCE.
- `TABULA_RASA_SESSION_GENERATION`: random operator-managed value of at least
  32 characters; change it to invalidate all sessions/login attempts for this
  environment (including signing-key/security-policy recovery).

Production requires explicit `TABULA_RASA_ACCESS_MODE=named_user`; missing, unknown or
restricted-alpha mode fails configuration validation before runtime allocation. Never
set `TABULA_RASA_TRUSTED_ENTITY_ID` or alpha Basic credentials in named mode. Production
and named mode reject injected trusted-development contexts at the HTTP guard as well as
rejecting synthetic configuration. Restricted alpha remains a separate explicit fixture
mode; it grants no named membership. The migrated-runtime factory rejects a named
identity resolver unless named-user mode is explicit, before allocating a database. Use
separate provider clients, secrets and databases for each environment. Hosted startup
requires an explicit access mode even for restricted alpha; only loopback
development/test keeps the fixture default. The [authenticated hosted rollout
checklist](HOSTED_ROLLOUT.md) owns exact operator configuration records, promotion smoke
and release-specific verification. Register exactly `<public
origin>/api/v1/auth/callback` without wildcard URLs. HTTP settings canonicalize
scheme/hostname case and omit default ports before binding the Host guard, exact browser
Origin/CSRF check and OIDC callback. Non-default ports remain part of the origin;
foreign origins are still refused. Provider accounts, region/plan, MFA/recovery, mail
and authorized secret setup remain operator checkpoints. No provider resource is
provisioned by this code.

## Login and browser lifecycle

Signed-out, session-unavailable and business-setup-required views consume the shared
commercial headers, surfaces and native action controls. They retain the context
boundary's private-view removal, reload and sign-out behavior; business selection and
onboarding follow the [business lifecycle contract](WORKSPACES.md).

The infrastructure adapter uses pinned Authlib 1.7.2 (BSD-3-Clause, Python 3.10+)
and its joserfc 1.7.5 JOSE implementation. Authlib owns authorization-code/PKCE
request preparation, client authentication, bearer UserInfo requests and OIDC
claim validation. Each exchange owns a separate client; provider tokens are not
shared between users. The adapter retains bounded transport, issuer-host endpoint
restrictions and the application's freshness, subject and verified-email policy.
PyJWT is a development-only independent signer for synthetic provider tests.

An unauthorized private response clears private browser views but does not delete
shared authentication cookies: a delayed response may belong to a session older
than a login completed in another tab. Explicit login/logout transitions own
cookie replacement and deletion. Retaining a rejected cookie grants no authority;
every later request still validates its durable session.

`GET /api/v1/auth/login` starts a five-minute durable browser-bound one-time
transaction. Return paths are only `/`, `/onboarding` and `/invitations` (after forward migration
`0010_onboarding_routes`). The code callback
consumes it before exchange; failures require starting a new login. The server
prunes expired and consumed login transactions in the same environment when a
new login starts, including clients without a previous login cookie. Active
attempts and other environments are preserved. Successful session issuance also
prunes revoked sessions and sessions at their idle or absolute expiry boundary
in the same environment; active sessions and other environments are preserved.
Expiry and revocation are still enforced on every protected request, independently
of this cleanup. It checks signed RS256 identity,
issuer, audience/authorized party, nonce, fresh
authentication time, expiry and mandatory matching-subject verified-email
UserInfo. Discovery/JWKS/endpoints are restricted to the configured HTTPS issuer
host; response size, network timeouts and unknown-key refresh are bounded.
Provider tokens remain transient server-side and are never app credentials.

The server binds an immutable exact `(issuer, subject)` to a random internal user ID.
Equal or changed email addresses never link accounts. New users receive no organization
membership or preexisting business access automatically. Ownership associates with
existing canonical `business_entities`; it does not create an alternate ledger,
rebaseline records, or change cutover provenance. Repository operations are internal
trusted building blocks, not public administration endpoints. Callers must enforce the
[server action policy](AUTHORIZATION.md) and membership-administration safeguards before
invoking them. Forward revision `0008_owner_lock_scope` keeps organization-owner
serialization on user state updates/deletion, not login display-metadata or
revocation-epoch updates. Membership owner locks and deferred last-active-owner checks
remain. It changes no bookkeeping fields or portable-export inventory.

Hosted opaque session/login cookies use `__Host-tabula_session` and
`__Host-tabula_login`: Secure, HttpOnly, SameSite=Lax, Path=/, no Domain. Loopback
HTTP uses distinct `tabula_dev_*` names. A matching HttpOnly host-only CSRF copy
(`__Host-tabula_csrf`) permits Origin/CSRF-valid logout to clear cookies even
when PostgreSQL is unavailable. That copy grants no bookkeeping authority.
Other unsafe routes use the freshly validated session's synchronizer token.
Passive identity denials return no cookie deletion: a delayed 401 must not erase
a newer session installed by a successful callback. Stale cookies still fail
fresh server validation; explicit logout/revoke-all clear the browser cookies.
The failure screen retains only logout CSRF, not private session/entity data,
and offers Sign out. After an initial reload during an outage, the browser reads
`GET /api/v1/auth/logout-context` with `X-Tabula-Logout: 1` to obtain only this
cookie's logout token without querying authorization storage. This no-store,
same-origin-only read rejects foreign Origin/Fetch-Metadata and grants no access
or renewal; CORS remains disabled. The following logout POST still requires
exact Origin and matching CSRF. A network/server outage that prevents cookie
clearing is reported as unavailable, never as confirmed server revocation.

Only session credential hashes are stored. Sessions survive worker restarts,
expire after 30 minutes idle or 12 hours absolute, and are freshly checked on
each protected request. Background session checks do not extend idle lifetime.
Visible foreground pointer/keyboard activity sends a throttled CSRF-protected
activity request. Local logout does not terminate provider SSO; fresh login
requires `max_age=0` and explicit `prompt=login`. ZITADEL documents `login` as
requiring reauthentication / a user name in its
[authorization endpoint contract](https://zitadel.com/docs/apis/openidoauth/endpoints#authorization_endpoint).
The signed-token freshness policy remains unchanged: `auth_time` must be within
the preceding five minutes, with at most 60 seconds of future clock skew.
Provider reauthentication is requested on every new application login, including
the deliberate restart action; no provider cookie deletion or silent retry is
part of the normal flow.

### Retained provider sessions and safe failure recovery

An existing provider SSO session must satisfy the same state/nonce/PKCE, signed
token, freshness and matching verified-email UserInfo checks as a fresh browser.
An old provider session is not an application session. A failed callback returns
to the signed-out screen with a fixed category, a server-generated callback
request reference and **Sign in** to start a new transaction. No callback code
is exchanged twice; the screen never restarts login automatically. If the new
attempt also fails, contact the operator with that reference. An ordinary
`/signed-out` visit shows no spurious login-failure warning.

The safe `security_reason` categories distinguish the boundary that failed:

| Category | Operator investigation boundary |
| --- | --- |
| `login_start` | Transaction creation or provider discovery / authorization URL preparation |
| `login_transaction` | Missing, unmatched, expired, consumed or configuration-invalid browser transaction |
| `provider_authorization` | Provider returned an authorization error after transaction validation |
| `token_exchange` | Authorization code / confidential-client / PKCE exchange |
| `token_validation` | Missing/malformed token or signature, issuer, audience, nonce or claim validation |
| `login_freshness` | Verified signed authentication time outside the accepted window |
| `userinfo_validation` | UserInfo transport, subject or verified account details |
| `session_issue` | Local user resolution or durable session issuance |

Unavailable failures retain `failure_code=unavailable`; the screen explains the
temporary service failure. Categories identify rejection boundaries, not a
diagnosis of provider fault. Raw provider error names/descriptions are never
displayed, logged or reflected into the clean redirect.

Triage by the screen's request reference in the private application log sink,
then match stage, release, operation, failure code and `security_reason`.
`login_ref` correlates `begin_login` with `complete_login` when the existing
telemetry scope key is configured. Its source is a separate five-minute,
HttpOnly, host-only diagnostic reference cookie with the login cookie's flags;
the log contains only a domain-separated HMAC pseudonym. This reference grants
no authority and is cleared with the transaction cookie. A missing/altered
reference can affect diagnostics only, never transaction validation. Never
record cookies, codes, token/claim values, email, provider subject, callback
queries or screenshots/network exports containing them. Keep operational
incident notes and provider-specific investigation in approved private custody.

### Bounded operator identity-dependency launcher

Normal startup remains `python -m tabula_rasa.http.serve`. An explicitly approved
staging operator may instead select the following alternate command in a fresh
process. It installs a process-local CPython resolver audit hook, verifies its
registration without DNS, then calls the normal single-worker serve entry point:

```sh
python -m tabula_rasa.http.identity_rehearsal --expected-origin https://APPROVED_STAGE_ORIGIN --expected-issuer https://APPROVED_ISSUER --expected-release EXACT_40_HEX_SHA --deadline-utc YYYY-MM-DDTHH:MM:SSZ --max-seconds 300 --acknowledge-staging-cold-discovery approved
```

Replace every placeholder from the separately approved private operator packet.
The command requires named-user staging, the exact canonical public origin and
configured issuer, and the exact configured release. Missing/invalid configuration,
production, mismatched bindings, absent acknowledgement, expired UTC deadlines,
and windows longer than the supplied maximum (at most 300 seconds) refuse startup.
The issuer, client, secret, callback and session generation are never changed.
Arguments contain no credentials; fixed diagnostics print no target hostnames,
callback material or database URLs. Keep actual target bindings in private custody.

The hook denies only `socket.getaddrinfo` for the issuer's normalized hostname
(Requests-prepared IDNA, case and trailing-dot normalization) and effective HTTPS
port. Requests continues using `trust_env=False`, TLS verification and existing endpoint checks.
The real discovery failure reaches the unchanged OIDC adapter and login route:
new login fails closed with `unavailable` / `login_start`; no fallback or session
issuance is added. Existing durable sessions retain the normal outage policy.
The launcher does not touch memberships, grants, database resolution or telemetry
configuration. Other hostnames and ports continue resolving, including another
hostname on the same server. Any other process component resolving the exact
same hostname and port is also affected: inspect that overlap before approval.

Resolution is automatically permitted at the earlier of the absolute UTC
deadline and the monotonic lifetime cap. Once observed expired, the hook stays
inactive. No operator presence or timer thread is needed; clocks are never changed.
Restart after the absolute deadline refuses to arm. This restores resolution,
not provider health, successful login or notification delivery. The hook remains
registered until process exit. Remove the startup override through a separately
approved deployment to return to ordinary startup.

Interpret only a **fresh-process cold discovery** attempt as the demonstrated
case. Discovery/JWKS caches last 300 seconds and are not cleared; populated
caches and already-open sockets may avoid resolution. Native clients can bypass
this boundary. Audit hooks are trusted-process diagnostics, not a security
sandbox ([Python audit hooks](https://docs.python.org/3.12/library/sys.html#sys.addaudithook),
[resolver audit event](https://docs.python.org/3.12/library/audit_events.html)).
Do not apply this mechanism to database/session-store faults or claim all identity
traffic is blocked. Do not use proxy environment settings to represent this fault.

The commercial Dockerfile already copies the package and uses CMD, without an
ENTRYPOINT. A new image build is required to include the launcher; its ordinary
CMD, user 10001, headers, access-log policy and defaults remain unchanged. A local
source-mounted transport test is not proof that a future published image includes
the module. Verify the exact immutable built image before any hosted approval.
Local loopback TLS tests use an explicitly trusted synthetic CA without altering
machine trust, real credentials, external requests or a database. They establish
transport feasibility and HTTP failure semantics, not hosted acceptance.

The [incident rehearsal preconditions](../operations/INCIDENT_RESPONSE.md#identity-dependency-rehearsal-preconditions)
own hosted approval, current alert verification and the sanitized evidence packet.

### Provider-backed verification

Signed synthetic provider, disposable PostgreSQL and compiled-browser tests cover
stale/fresh responses, one-shot failure, explicit recovery and two-user isolation. They
do not establish real provider SSO behavior.

For each selected hosted release, use authorized synthetic staging users to verify fresh
login and retained-session reauthentication, a controlled failed attempt followed by
explicit recovery, sign-out, session expiry and revocation. Test retained and clean
browser profiles without clearing site data. Record the exact release, safe request
references, failure categories and observed outcomes. If a check fails, inspect the
classified boundary privately; do not replay a callback or weaken validation. Deployment
and external admission require separate approval. The [login recovery
record](https://github.com/Roklion/Project_Tabula_Rasa/issues/395) contains historical
diagnosis, remediation and acceptance evidence.

`GET /api/v1/session` reports `ready` or authenticated `needs_setup`, without
composing ledger services for an unscoped user. Inspection, authorized entity
selection, activity, logout and revoke-all do not require bookkeeping-write
permission. The React boundary shows pending/sign-in/setup/failure states,
rechecks visible identity at least every 60 seconds and on focus/visibility/
pageshow, and unmounts private caches/drafts on authoritative loss or outage.
Confirmed scope changes remount the existing private query boundary; logout is
broadcast across same-origin tabs without private payloads. A received sign-out
invalidates pending session responses as well as an already loaded private scope,
so a late initial response cannot reopen a signed-out tab. Failed logout clears
the local cookie but reports 503 if durable revocation cannot be confirmed;
it does not claim a copied credential was revoked.

## Revocation and owner recovery

[Security incident
response](../operations/INCIDENT_RESPONSE.md#security-and-identity-incidents) defines
immediate-revocation incident classes; [secret
operations](../operations/SECRETS.md#named-identity-and-restored-session-revocation)
coordinates custody, worker drain, this epoch command and restored-session invalidation.
Provider recovery and local app revocation remain separate.

Membership roles are Owner, Admin, Editor, Accountant and Read-only. Role/state
changes increment the authorization version. Missing/inactive membership,
suspended organizations, inactive ownership, disabled users and unknown roles
grant no entity context for that business. If the selected business loses access
but another authorized business remains, validation persists an available
authorized selection and the browser replaces its private scope. Only users
with no available business receive needs_setup. Active organizations require an active Owner; native
deferred constraints and serialized organization locks protect role changes,
membership removals, user disable and reactivation. Transfer ownership first,
or explicitly suspend the organization before removing its last active Owner.

User disable changes that user's session epoch across every organization.
Credential changes/forced resets must call the trusted
`revoke_user_sessions(user_id)` operation before declaring local recovery
complete. An authenticated user can use CSRF-protected
`POST /api/v1/auth/revoke-all`; others' sessions are unaffected. Provider-only
password/MFA resets do not notify this app automatically. Coordinate local
revocation with provider recovery and verify that copied credentials fail;
ordinary session deadlines are not a substitute. If an external change cannot
be reconciled safely, suspend affected access until revocation is confirmed.

Issuer, client, client-secret, public-origin or session-generation changes require
explicit operator rotation of the durable environment epoch. Startup initializes an
empty environment or validates its existing configuration; a mismatch fails closed and
cannot replace the durable hash. Drain all workers, read the current epoch for the
selected environment from `identity_environments` through the authorized operator
database connection, and supply the new named-user settings and database URL in the
process environment. Run `python scripts/rotate_identity_configuration.py
--expected-epoch N` with that observed epoch, then restart all workers with the new
configuration. The command bypasses `.env.local` database fallback and refuses stale
epochs, unchanged configurations and uninitialized environments. A restarted old release
cannot reconfigure the environment or issue sessions. Verify previously issued
credentials fail on every worker before completing the operation. Client-secret rotation
revokes existing sessions; retaining sessions across a configuration-secret change is
unsupported.

The [server action matrix and exposed-route inventory](AUTHORIZATION.md) enforce
role-specific authority before private service/document binding. Production/migrated
HTTP uses the named membership resolver, and identity/ledger database and environment
bindings must agree before profile grants are bound. Recovery/backup/destructive
restore/migration authorization is Owner-only; platform operators execute separately
authorized recovery. Do not synthesize an in-app break-glass identity. Application and
provider grants are unrelated to entity-scoped Google receipt OAuth. Restore identity
control-plane recovery records separately from canonical bookkeeping exports and revoke
restored sessions before bringing a restored environment online (platform operations
recovery gate).

## Bounded synthetic session-revocation client

The standalone Node 24 client exercises the existing revoke-all route without
copying credentials. Install the locked web dependencies and Chromium as described
for the [synthetic diagnostics client](../operations/SUPPORT.md#synthetic-privileged-denial-client).
It is not part of the product bundle or navigation.

```powershell
node web/session-revocation.ts --origin https://APPROVED_SYNTHETIC_ORIGIN --release EXACT_40_HEX_SHA --organization APPROVED_A_ORG --business APPROVED_A_BUSINESS --control-organization APPROVED_B_ORG --control-business APPROVED_B_BUSINESS --approval PRIVATE_APPROVAL_REFERENCE --expires-at APPROVED_UTC_WINDOW_END --read-budget 10 --synthetic-approved --consent-one-revocation
```

Replace the window end with an exact UTC `YYYY-MM-DDTHH:MM:SSZ` timestamp,
in the future and at most 30 minutes away. Before invocation, obtain external
approval naming the origin, immutable release, existing synthetic A and B
identities/businesses, user-wide revocation impact, exact window, request budgets
and private custody. The opaque approval reference and flags attest that decision;
they do not grant authority or prove a target is staging. Revoke-all affects A's
sessions across all its staging businesses, including separately authorized
businesses outside the selected one. It changes no role, membership, support
grant, provider credential or environment epoch.

The operator completes normal login/MFA and business selection in three separate
ephemeral Chromium contexts: invoking A, a second independently issued A session
held for denial verification, and independently authorized B. Credentials are
entered only in the ordinary identity-provider UI. The terminal requests each
fixed `READY INVOKING-A`, `READY HELD-A` and `READY CONTROL-B` phrase. Login traffic
uses the diagnostics client's existing narrow guard, with at most 30 same-origin
API GET attempts and four business-selection POST attempts per context. Other
application API reads/mutations are blocked; dashboard requests can show errors.
Identity-provider interaction and static assets are separate from these API budgets.

After selection, the login pages close without clearing their contexts' cookies.
Locally fulfilled blank same-origin pages stop SPA polling and run only the fixed
probes. Ten probe GETs are allowed in total: version and ready named session in
each context, another fresh version/session check for invoking A immediately
before the action, then held-A and B session checks. Session and CSRF data remain
inside each page. Per-run salted tags compare actor equality and independent
session scopes without exporting raw actor identifiers or session DTOs; tags are
not printed or retained.

Only after the two A identities match, their issued session scopes differ and
B's identity differs does the terminal request `SEND ONE USER-WIDE REVOCATION`.
Exactly one fixed CSRF-protected revoke-all POST can proceed from invoking A.
There is no mutation retry. Redirect, transport uncertainty, unexpected response,
expired approval or identity/scope/release mismatch stops the exercise. The held
A context must return the canonical 401 after the POST while retaining its own
issued credential; merely clearing the invoking context is insufficient. B must
remain ready with its original actor/session scope and authorized business.

All contexts and the browser close on completion or failure. Output contains only
fixed status/phase, verified release, allowlisted request references, revocation
confirmation state and held-A/B outcomes. `unconfirmed` means the POST might have
taken effect: inspect private server state before a new approved attempt. A
`stopped` result after confirmation does not undo revocation. Do not enable
diagnostic environment/runtime flags, tracing, HAR, video or credential handoff.
The CLI rejects those diagnostic modes and uses no test-runner recording fixtures.

Client success proves neither collector/notification delivery nor hosted execution
when run with synthetic transport tests. Correlate the actual authorized run's
`user_sessions_revoked` request and held-session denial independently. This
single-worker user revocation does not establish idle/absolute expiry, restart,
multi-worker rotation, identity-provider outage or database/configuration faults.
Keep execution evidence in the owning operational discussion and private record;
deployment, hosted execution and stage/release acceptance remain separate approvals.

## Validation and remaining checkpoints

Regression coverage uses signed synthetic RSA tokens, bounded fake provider responses,
disposable native PostgreSQL, real HTTP transport, two users and organizations, retained
canonical entities, owner constraints, transaction replay, revocation/config rotation,
setup/read-only identity management and private-browser scope cleanup. These are not
evidence of a real ZITADEL tenant. The authorized hosted rehearsal must verify exact
callback/PKCE plus Basic authentication (including bad verifier/secret), Secure cookie
flags, MFA/recovery, multi-worker revoke/rotation and provider/database outages.
Security logs emit only fixed event/failure codes and safe request identifiers; never
log callback queries, provider payloads, emails, credentials or ledger content.
[Operational telemetry](../operations/OBSERVABILITY.md) consumes only these allowlisted
diagnostic signals.

## Creating an identity before joining a business

Signed-out users choose **Create account** or **Sign in**. Create account starts
ZITADEL's hosted registration with `prompt=create` and `max_age=0`; Sign in keeps
`prompt=login`. Both use the existing callback, verification and session security.
Registration allows 15 minutes for the hosted form, email verification and factor
enrollment. Both browser cookies and the durable transaction expire at that
boundary. The callback still requires recent provider authentication.
Registration returns to `/invitations` by default. It grants no membership,
ledger or beta approval. See [provider setup](PROVIDER_SETUP.md#hosted-end-user-self-registration)
and [hosted verification](HOSTED_ROLLOUT.md#hosted-registration-and-invitation-smoke).

If registration is disabled or incomplete, return to the app for private operator
guidance or Sign in with an existing account. Canceled/failed callbacks use the
existing safe failure page and deliberate restart, without reflecting provider
error descriptions. Provider signup never reactivates a closed application login.
