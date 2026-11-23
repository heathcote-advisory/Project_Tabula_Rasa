# ADR 0003: Commercial identity, sessions and tenant authorization

Status: Selected identity contract; subject to maintainer review. Runtime implementation
and hosted acceptance remain in identity control plane and hosted identity rollout.

## Scope and decision

Use **ZITADEL Cloud hosted login**, OpenID Connect Authorization Code with
**S256 PKCE**, and a **durable opaque application session in PostgreSQL**.
FastAPI is the confidential OIDC client; React receives safe session/context
DTOs and a CSRF token. User, organization, membership, role policy and entity
ownership belong to Tabula Rasa's control plane, not provider organization or
role claims.

[ADR 0002](0002-commercial-web-runtime.md) already selects React/Vite, FastAPI,
PostgreSQL, Render/Neon and same-origin hosting. Retain its host-only session
cookie and central context seam. This decision supplies the missing identity
contract; it does not implement login, provision accounts, authorize a live
cutover, retire Streamlit, or approve external beta.

[Provider setup](../identity/PROVIDER_SETUP.md) describes configuration, threat cases
and component responsibilities; [authentication](../identity/AUTHENTICATION.md) and
[authorization](../identity/AUTHORIZATION.md) define the executable contracts.
Restricted synthetic alpha, live owner use and external admission have distinct
requirements. Live owner use requires named authorization, approved entity and document
bindings, explicit cutover and accepted recovery limits. Provider setup and a synthetic
test pass do not supply those approvals.

## Options and consequences

Evaluation uses official documentation checked on the decision date, not a
hosted proof or a permanent pricing commitment.

| Option | Staging/internal cost and beta suitability | Python, recovery, operations and portability | Decision |
| --- | --- | --- | --- |
| ZITADEL Cloud hosted OIDC | Published Free plan includes 100 daily active users and security features; one instance and one administrator are listed. Appropriate for small synthetic staging subject to actual quotas. Environment separation, production mail, support and capacity require operator verification. | Standard OIDC fits Python; hosted login includes verification/recovery and MFA/passkeys. Managed service removes identity-server operations. Provider account lifecycle remains vendor-specific; a later OIDC provider needs explicit subject rebinding. | Selected: low initial operating burden with security features available for a bounded rollout. |
| Auth0 hosted OIDC | Published Free plan has 25,000 monthly active users. Plan-dependent environment, organization, MFA and support features need verification for the actual application. App-owned organizations avoid provider organization quotas as a tenant limit. | Mature hosted login and standard OIDC; recovery/mail and administration still require setup. Managed operations, but paid feature boundaries and provider-specific lifecycle are a tradeoff. | Credible alternative if the selected service cannot meet the operator checkpoint. Changing provider requires an ADR revision, not an automatic runtime fallback. |
| Self-hosted Keycloak | No managed identity subscription is selected, but hosting, database, TLS, patching, monitoring and recovery cost remain. Can support staging/beta if operated reliably. | Standard OIDC and own infrastructure reduce hosted-provider dependency. Identity-server availability, secure email and upgrade/backup drills add another critical service to platform operations. | Defer: unnecessary operator burden for the first vertical slices. |

Sources: [ZITADEL plans](https://zitadel.com/pricing),
[plan detail](https://zitadel.com/pricing/detail),
[Auth0 plans](https://auth0.com/pricing),
[Auth0 web applications](https://auth0.com/docs/get-started/auth0-overview/create-applications/regular-web-apps),
and [Keycloak production configuration](https://www.keycloak.org/server/configuration-production).

A free plan is a cost option, not a waiver of identity and tenancy or external release
and customer lifecycle protections. Provider organization membership is deliberately not
a parallel authorization model. One provider project can authenticate users who belong
to multiple application organizations. No provider role scopes or management-API grant
is required by the normal bookkeeping API.

Use a maintained Python OIDC library rather than handwritten JWT/OAuth crypto.
Named-user authentication should use Authlib's OIDC/HTTP client integration behind a
narrow infrastructure adapter and pin a supported version after compatibility/license
checks. Its framework examples are not the application session design: persist login
transactions and sessions server-side, rather than copying a signed browser-session
example containing tokens. [Authlib Starlette OIDC
integration](https://docs.authlib.org/en/latest/oauth2/client/web/starlette.html).

Costs accepted: an external login dependency, a small session/control-plane
repository and session-expiration UX. There is no Redis, second authentication
server, provider-SDK dependency in application services, or JWT role snapshot
accepted as continuing authorization. Provider portability preserves internal
IDs, but password/MFA credentials and provider subjects cannot be assumed
portable.

## Identity and control-plane model

| Record | Required contract |
| --- | --- |
| User | Immutable opaque internal `user_id`; active/disabled state; a monotonic session-revocation epoch. Email/display name are private mutable attributes, never a primary key or ownership proof. |
| Identity binding | Unique exact `(issuer, subject)` maps to one user. Bind the configured environment's verified OIDC subject; preserve case and issuer semantics. One initial provider binding per user is sufficient. No automatic linking by matching email, provider organization, or display name. |
| Organization/workspace | Stable application ID and active/suspended state. A user can be a member/owner of several organizations. |
| Membership | Unique `(organization_id, user_id)`, active/inactive state, one enumerated role, and monotonic authorization version. Invitations remain separate pending records and confer no membership. |
| Entity ownership | Each existing canonical `business_entities.id` has at most one owning organization. An unclaimed migrated entity has no commercial access. Membership grants the role across all active entities of that organization. |
| Session | Hashed random credential, user, identity binding/environment, creation/authentication times, idle/absolute deadlines, revocation state/epoch, CSRF secret, selected organization/entity (nullable) and independent public cache identifier. No password or provider role claims. |

Use forward-only migrations, foreign keys, uniqueness and lifecycle constraints where
appropriate. Retain bookkeeping IDs, provenance, authority and document metadata. Do not
create another ledger entity or reset deployed data. Existing entity claims and local
administration explicitly binds preexisting entities; neither a login nor the profile's
name/email implicitly claims them.

There are **no entity-specific membership overrides in v1**. Members can access
every active business in their organization; invitations/admin UI must say so.
Businesses needing different audiences belong in distinct organizations.
Accountant multi-client access uses separate memberships, each with its own role.
Adding entity ACLs later requires revising this contract, not undocumented
client-side filtering.

Maintain at least one active Owner for every active organization. Serialize
owner-affecting changes in a transaction against the organization, including concurrent
removal/demotion and owner user-disable operations. Disabling the last owner must
transfer ownership first or explicitly suspend the organization under a documented
operator procedure; no silent ownerless active workspace. Provider administrators gain
no application membership by holding that position. Session/login-transaction/invitation
credentials and private user attributes are excluded from bookkeeping exports. Audit
records may carry safe internal actor IDs under platform operations; exporting books
must never export authentication state.

## Role and capability policy

Keep all five requested roles with useful distinctions. `editor` is displayed
as Bookkeeper/Editor; Accountant adds controlled bulk handoff/import. Read-only
can view reports and linked receipts, but not bulk complete-entity exports.
No custom roles, inheritance engine, user-defined permissions or billing
entitlements are introduced.

| Server capability / operation | Owner (`owner`) | Admin (`admin`) | Bookkeeper/Editor (`editor`) | Accountant (`accountant`) | Read-only (`read_only`) |
| --- | --- | --- | --- | --- | --- |
| `bookkeeping:read`: ledger, accounts/reference data, reports, mileage, recurring/assets, permitted receipt metadata/bytes | Yes | Yes | Yes | Yes | Yes |
| `bookkeeping:write`: normal CRUD, receipt upload/link/unlink, explicit recurring/depreciation posting | Yes | Yes | Yes | Yes | No |
| `bookkeeping:import`: supported bulk bookkeeping imports | Yes | Yes | No | Yes | No |
| `bookkeeping:export`: complete entity export/bulk handoff | Yes | Yes | No | Yes | No |
| `entity:manage`: create another business, edit business/setup defaults | Yes | No | No | No | No |
| `organization:manage`: workspace name/settings | Yes | No | No | No | No |
| `members:manage`: list/invite/change/remove non-owners | Yes | Yes, restricted below | No | No | No |
| `connections:manage`: connect/reconnect/disconnect external grants; inspect safe status | Yes | No | No | No | No |
| `ownership:manage`: grant/revoke Owner, transfer ownership | Yes | No | No | No | No |
| `backup:create` / `backup:list` / `backup:download`: business recovery artifacts, separate from bookkeeping exports | Yes | No | No | No | No |
| `recovery:authorize`: destructive restore, ledger migration and privileged business recovery | Yes | No | No | No | No |

The Business Owner role is distinct from an ordinary user permitted to view or
update entries. Owner-only administration and recovery authority cannot be
inferred from Admin/Editor/Accountant status or a selected business. Existing
bookkeeping import/export permissions remain distinct from backup and restore.
The [implemented action registry and route inventory](../identity/AUTHORIZATION.md)
also distinguish archive recovery, restore, migration/cutover, ledger reset,
privileged audit inspection and legacy local administration. Operator execution
and one-execution recovery approval requirements are absent from tenant roles.
The recovery capabilities above define authorization requirements, not new
endpoints or permission to execute a migration. Preserve fresh authentication,
profile/entity isolation, current backup validation, restoration to a new blank
workbook and refusal to restore over the configured live workbook.

Business Owners authorize recovery affecting their business. Platform operators execute
approved infrastructure recovery without automatically becoming tenant Owners or gaining
bookkeeping access. Tenant roles do not grant deployment credentials, local profile
secrets or service-wide restore authority. platform operations owns the separate
service-wide incident/recovery procedure and its operator approval and audit
requirements; a tenant Owner's approval cannot authorize changes to other businesses.
There is no shared-password or synthetic break-glass login to books. Business recovery
approval must identify the affected business, requested operation and authorized
operator before destructive execution. At execution, verify that the approving Owner
remains active and authorized for the affected business; otherwise stop and obtain a new
authorized approval. Approval covers one execution of that operation, not standing
recovery access; reconfirm current Owner authorization if execution is deferred or its
scope or operator changes. platform operations records approval and execution together
in the recovery audit; no reusable bearer approval credential is implied.

The transition initially used loopback-only Streamlit Business Profiles
administration. The [local operator CLI](../SETUP.md#local-profile-administration)
now owns the retained local credential and profile operations. Commercial login,
tenant roles and the business selector grant no local operator access. Keep the CLI's
temporary Google callbacks on loopback and out of public ingress. The
[local administration inventory](../identity/CONNECTIONS_AND_CLAIMS.md#retained-local-administration-inventory)
defines which actions remain local and which belong to commercial workflows.

Import requires `bookkeeping:write` AND `bookkeeping:import`; complete export
requires `bookkeeping:read` AND `bookkeeping:export`. All other rows with a
management capability also require active authenticated membership in the target
organization. Viewing/downloading an individual receipt and ordinary report
responses remain normal reads; a complete portable archive is the export
boundary. No role may bypass accounting invariants, authority activation,
loaded-baseline/stale-write checks, or a suspended resource.

Admins may invite/assign Editor, Accountant and Read-only. They cannot modify
an Owner or another Admin, grant Admin/Owner, or promote themselves. Owners
may manage all roles subject to last-owner protection. Self-removal is permitted
for non-last owners and other members; self-demotion never increases privilege.
Owner grants/transfers and connection grant changes require fresh provider
authentication within five minutes in addition to the capability. The provider
login policy enforces MFA for all named hosted users; do not infer assurance
from an unchecked client claim.

Creating a new workspace uses authenticated-user bootstrap authority (ledger bootstrap);
it does not require membership in a workspace that does not exist. The new owner is
always the acting internal user. Logout, session inspection and authorized switching are
identity operations available to Read-only members; they **must not require
`bookkeeping:write`**. Unlisted actions deny by default. Workspace deletion, support
impersonation, billing and retention/destructive customer lifecycle are outside v1
roles; external release and customer lifecycle must define them before exposing such
operations.

## Login and callback contract

Use the provider-hosted page with native verified email/password plus enforced
TOTP or WebAuthn/passkey policy. Do not enable social/enterprise federation
initially; its assurance/linking semantics need a separate reviewed extension.
All v1 native accounts reside in one operator-controlled provider user directory
per environment with the enforced login policy; customer-controlled provider
organizations/login-policy overrides are not enabled. That directory is not an
application workspace or an authorization grant. Recovery and factor enrollment
belong to hosted login. Passwords and recovery codes never pass through
bookkeeping DTOs or application logs.
[ZITADEL hosted login](https://zitadel.com/docs/guides/integrate/login/hosted-login).

1. `GET /api/v1/auth/login` starts a login transaction; it grants no private
   context. Generate independent cryptographic state, nonce and PKCE verifier.
   Persist them with a five-minute deadline and a hash of a fresh browser
   transaction cookie. Only one outstanding transaction per browser is needed.
   Allowlisted return destinations are stored server-side.
2. Redirect to the configured issuer's discovery-derived authorization endpoint
   with code flow, `openid email profile`, S256 PKCE, exact callback and
   `max_age=0`. Register a Web client using `client_secret_basic`; keep its
   secret server-side. No implicit/password grant, `offline_access`, provider
   role scope or dynamic client registration.
3. `GET /api/v1/auth/callback` validates transaction cookie/state, expiry and
   provider errors, then atomically consumes the transaction before one bounded
   code exchange. Failed/uncertain exchanges require a new login; never replay
   a code. Exchange uses the exact stored callback/verifier and configured
   client. A second worker/callback cannot consume the same transaction.
4. Validate the ID token's signature with configured algorithm allowlist and
   issuer JWKS, exact issuer, audience/authorized-party rules, expiry,
   issued-at/not-before when present, nonce and `auth_time`. Require nonempty
   subject and authentication no more than five minutes old; clock tolerance
   is at most 60 seconds. Fetch the configured issuer's discovery-derived
   UserInfo endpoint with the exchanged access token, require its subject to
   exactly match the validated ID-token subject, and require a nonempty email
   and boolean `email_verified=true` before mapping the user or issuing a
   session. Missing/malformed claims, subject mismatch or UserInfo failure
   denies login. Unknown keys allow one bounded JWKS refresh; failure denies login.
   Token `jku`, request headers and browser parameters never select an issuer,
   key URL, algorithm or callback.
5. Resolve/create the internal user through the unique binding; disabled users
   stay disabled. No membership is granted by email or identity claims. Revoke
   any application session being replaced; issue a new random session and CSRF
   secret only after database commit. Delete the transaction cookie and redirect
   immediately to a clean allowlisted application path.
6. Discard provider access/ID tokens after verified mapping. Do not persist
   refresh tokens, put them in cookies, or make them bookkeeping bearer tokens.

PKCE remains enabled alongside confidential-client authentication. In the ZITADEL Web
application choose **Basic Auth** (`client_secret_basic`), not the public-client
**PKCE** (`none`) authentication option. Send the S256 challenge on authorization and
the matching verifier with HTTP Basic client credentials on the token request. These are
separate checks: ZITADEL's [client
verification](https://github.com/zitadel/zitadel/blob/ad835efbc3f6115ab4f84230616b018d4d5b4c54/internal/api/oidc/client.go#L216)
checks the Basic client secret, while its [code-exchange
implementation](https://github.com/zitadel/zitadel/blob/ad835efbc3f6115ab4f84230616b018d4d5b4c54/internal/api/oidc/token_code.go#L126)
checks a supplied challenge even for authenticated clients. The provider's [Web
application
overview](https://zitadel.com/docs/guides/manage/console/applications-overview#web) also
recommends PKCE with a client secret. named-user authentication and hosted identity
rollout must verify this combined exchange on their configured hosted client, including
rejection of an incorrect verifier and missing/incorrect client secret; source evidence
is not hosted acceptance evidence. [ZITADEL code
flow](https://zitadel.com/docs/guides/integrate/login/oidc/login-users), [client
authentication](https://zitadel.com/docs/apis/openidoauth/authn-methods), [OIDC
validation](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation) and
[OAuth security BCP](https://www.rfc-editor.org/rfc/rfc9700.html) provide the protocol
basis; the timing/storage policy above is our application decision.

ZITADEL's default code flow supplies email verification through UserInfo rather than
guaranteeing those claims in the ID token. The UserInfo request above is mandatory; do
not depend on the optional provider setting that copies UserInfo claims into ID tokens.
named-user authentication and hosted identity rollout must verify the default client
configuration and subject/verified-email rejection cases. [ZITADEL claims
matrix](https://zitadel.com/docs/apis/openidoauth/claims).

Only configured HTTPS discovery/JWKS/token/UserInfo endpoints are contacted. Bound
timeouts and response sizes; reject redirect-to-untrusted-host fetches. Canonical
callback is `PUBLIC_ORIGIN + /api/v1/auth/callback`; canonical post-logout destination
is `PUBLIC_ORIGIN + /signed-out`. No wildcards, preview hosts, arbitrary `next` URLs,
protocol-relative paths or forwarded-Host construction. v1 return destinations are `/`
and `/onboarding`; membership administration may add a Server-held pending invitation
reference, never embed its credential in a provider redirect.

The short-lived HttpOnly transaction cookie uses the same hosted flags as the
session and a separate name (`__Host-tabula_login`). Lax permits the provider's
top-level GET callback. Callback alone uses state/nonce/PKCE instead of the
browser mutation CSRF header. Callback responses have no private content, no
third-party resources, `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
Authorization codes/state necessarily transit protocol URLs: exclude query
strings from ingress/app/provider diagnostics and sanitize browser history
immediately. No identity/session token is transported in a URL.

## Session lifecycle and recovery

Hosted cookie: `__Host-tabula_session`, `Path=/`, HttpOnly, Secure, SameSite=Lax,
no Domain. Credential is at least 256 random bits; store its SHA-256 hash,
not its raw value. A high-entropy opaque credential requires no signing key
or client-verifiable JWT. Cookie persistence never exceeds its remaining
12-hour absolute deadline; PostgreSQL enforces deadlines regardless of browser
cookie expiry. Plain HTTP loopback uses separately named `tabula_dev_session`
and `tabula_dev_login` cookies; hosted runtimes never accept those names.

Application sessions expire after **30 minutes idle** or **12 hours absolute**.
A foreground authenticated interaction may extend the idle deadline, capped by
the absolute deadline; passive background polling must not keep a user signed
in forever. Reauthentication starts a new login transaction and session, with
fresh provider `auth_time`; there is no silent token refresh or indefinite
sliding absolute lifetime. Store sessions/transactions across workers/restarts;
expired-row cleanup is maintenance, never the enforcement mechanism.

Every protected request freshly checks session revocation/deadlines/epoch,
user state, organization/entity state and active membership. Local logout
revokes the session durably before success, deletes the cookie with matching
attributes and clears private browser state. Logout-all/user suspension
increments the user's revocation epoch (or equivalently revokes all rows).
Membership removal/demotion takes effect on the next authorized request after
commit without waiting for the cookie or provider token to expire. It need not
log the user out of unrelated organizations.

Provider SSO and application session are distinct. After local logout the next
login uses `max_age=0` and validates freshness. Optional provider sign-out uses
the configured end-session endpoint with `client_id`, exact registered return
URI and fresh logout state; do not add an ID-token hint to a URL or retain an
ID token solely for logout. Provider sign-out failure cannot undo a confirmed
local revoke. An Origin/CSRF-validated logout clears the local session cookie
with matching attributes and unmounts private browser state even if database
revocation fails. Return safe unavailable status in that failure case and
explain that server revocation is unconfirmed; clearing this browser's cookie
does not revoke a copied session credential. Offer fresh named login and the
authenticated revoke-all or operator-revoke procedure when service returns;
do not retain a usable cookie solely for retry or report durable logout success
without confirmation. Clearing an already expired/revoked session is
idempotent and grants no private authority.
[ZITADEL end-session contract](https://zitadel.com/docs/apis/openidoauth/endpoints#end_session_endpoint).

Credential or key changes must revoke affected application sessions and require
fresh login. User password/factor changes, forced account reset/recovery and
provider compromise revoke that user's sessions across all organizations;
unrelated users retain their sessions. Environment-wide OIDC client credentials,
session security material or trusted provider signing-key changes revoke that
environment's affected sessions and pending login transactions. Opaque sessions
have no signing key; do not introduce one solely for rotation. Membership
removal/demotion changes the affected organization's authority on the next
request; global user disable blocks every organization and revokes all sessions.

Named-user authentication, membership administration and hosted identity rollout must
provide a verified provider event or coordinated operator revocation path for credential
changes, including resets performed through hosted recovery. Never accept
unsigned/unverified events or treat a JWKS cache refresh as proof that application
sessions were revoked. Revoke application sessions as part of controlled rotation; if an
external change cannot be reconciled safely, suspend affected access until revocation is
confirmed. Suspected compromise recovery disables/revokes first, then recovers provider
credentials and verifies fresh login before re-enabling. The ordinary 12-hour deadline
is not a substitute for this invalidation requirement. hosted identity rollout must
rehearse both per-user and environment-wide changes before named hosted use.

During an identity-provider outage new login/reauthentication fails closed.
An already verified, unexpired application session may continue **only** with
fresh successful local session/membership checks under the ordinary idle and
absolute policy. Normal foreground activity may renew idle time within the
12-hour cap; provider failure never bypasses expiry or extends that cap. If PostgreSQL authorization state is unavailable,
deny access with safe unavailable status; no cached allow decision, synthetic
resolver or shared-password fallback. An operator can suspend a user even when
the provider is down. Lost MFA recovery requires provider/operator identity
verification, not an email-only ownership reassignment or hidden app backdoor.

## Immutable context and authorization boundary

Implementation note (identity control plane and named-user authentication): the
maintainer's identity provider decision recovery decision requires user credential
changes/forced resets to revoke the affected user's sessions in all organizations, and
provider/client/session configuration or key changes to revoke this environment's
sessions. Owner-only recovery authority and the separate loopback operator surface
remain intact. Concrete configuration, coordinated rotation and the additional
logout-only HttpOnly CSRF cookie are documented in [Named-user
authentication](../identity/AUTHENTICATION.md). These implementation notes do not
replace server authorization's exhaustive action authorization.

Reuse `http/context.py`, application-scoped `PostgresRuntime` and
`create_app(runtime_factory=...)`. Resolve an authenticated session first.
For an entity operation, join the requested canonical entity to its active
organization and the user's active membership, derive capabilities from the
single role policy, authorize the action, then construct frozen `RequestContext`:

| Existing field | Commercial source |
| --- | --- |
| `actor_id` | Internal immutable `user_id`; never email/provider token. |
| `organization_id` | Owning organization from validated entity relationship/membership. |
| `entity_id` | Server-authorized canonical business-entity ID. |
| `capabilities` | Frozen set from current membership role; browser/provider role claims ignored. |
| `cache_scope` | Nonsecret per-session cache identifier plus membership authorization version and organization/entity context. Never the session credential/hash. |
| `csrf_token` | Session-bound random synchronizer secret returned only to the authenticated same-origin browser, omitted from logs/repr. |
| `trusted_development` | Always false for real named sessions. |

Route/body/header/cookie IDs are selectors, never proof of access. Server-held
selection must agree with entity resource routes; switching validates membership
and updates selection through a CSRF-protected command. A delayed request for a
previous selection fails safely rather than applying its payload to the new
business. Switching does not rotate the login credential or create a new
identity; its changed cache scope remounts the existing `ScopeBoundary`.

New users and removed members may have **no selected authorized entity**. Named-user
authentication evolves `SessionInfo` to a discriminated authenticated state:
`needs_setup` with nullable organization/entity and no bookkeeping capabilities, or
`ready` with the existing fields. Both have actor, session cache identifier and CSRF.
`RequestContext` remains entity-bound; do not invent a dummy entity or call
`runtime.services` to produce an unscoped session. Identity/bootstrap routes use the
same session validator without demanding an entity. ledger bootstrap can create a
workspace; only then can server authorization produce its entity context. Anonymous
requests get 401, not an empty authenticated user.

The current resolver's unsafe-method-to-`bookkeeping:write` rule is insufficient for
identity/admin commands and imports/exports. server authorization must use an explicit
action-to-capability registry with one shared identity/membership resolver; retain
Origin/CSRF checks separately for **all unsafe browser operations**. Do not build a
second authentication path per feature. A valid loaded-record baseline is an independent
concurrency precondition after authorization.

Enforce before service/repository, document-store, export/import or secret binding.
Qualify document IDs/links/store keys and every read/write by the resolved entity;
knowable IDs do not grant bytes. Jobs carry trusted actor/ organization/entity and an
authorized action, rechecking current authority at execution under platform operations;
never serialize a browser credential into a job.

Missing/expired/revoked identity returns 401; absent/foreign/inactive membership,
entity or denied role returns the same safe 403 without confirming foreign
record existence. An unknown record inside an already authorized entity may
return 404. Infrastructure failure returns safe 503. No provider exceptions,
SQL, credentials, emails or ledger values in errors or request logs.

Exact configured Host/Origin and synchronizer CSRF header protect unsafe commands,
including logout/switch/invite acceptance/bootstrap. Reject missing or `null` Origin;
CORS stays disabled. Provider callbacks are the narrowly defined protocol exception.
Serve private session/data/document/export responses with `no-store`. ScopeBoundary
cancellation/remount clears queries, forms, selections, uploads and object URLs on
identity/scope change. authorized workspace switching also rechecks session on focus and
at most every 60 seconds while a private view is visible, unmounting private state on
denial/change. Cross-tab logout broadcasts cleanup without private payloads. Already
downloaded bytes cannot be recalled, and an in-flight request authorized before
revocation may finish; all later requests must deny. No claim of retroactive erasure or
arbitrary concurrent-request transactions.

## Reuse map and follow-on boundary

- Inspected: `http/context.py` owns frozen context/capability/Origin/CSRF checks;
  `http/composition.py` owns pools and per-context service/document composition;
  `http/routes/session.py` and `http/models/common.py` own session/discovery/
  selection DTOs; `web/src/app/{ContextBoundary,ScopeBoundary}.tsx` and
  `web/src/api/query.ts` own scoped queries and remounted form lifetimes;
  canonical PostgreSQL `business_entities`, `MigratedProfileServices` and
  ledger setup services own ledger identity, accepted profile bindings and defaults.
- Reused/extended: replace the resolver, extend session states and action policy,
  retain scoped browser cache ownership, entity records, existing ledger
  bootstrap and profile-specific Google/DocumentStore adapters.
- New logic required: provider adapter, durable user/membership/session/login
  transaction records, authorization policy and explicit ownership/invitation
  lifecycle. These belong to the control plane, not ledger rows.
- New parallel logic: none in identity provider decision. Follow-on named sessions coexist with the
  deliberately restricted synthetic test resolver and legacy Streamlit sign-in;
  their distinct deployment/stage semantics prevent a shared fallback or a
  second commercial profile ACL implementation.

The testable threat model and full handoff matrix are canonical in
[Commercial identity](../identity/PROVIDER_SETUP.md). Architecture selection is
complete here; provider setup, code, maintainer acceptance of this contract and
hosted/release evidence remain explicit downstream work.
