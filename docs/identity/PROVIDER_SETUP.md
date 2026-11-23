# Identity provider configuration and security requirements

[ADR 0003](../adr/0003-commercial-identity-and-tenancy.md) selects ZITADEL Cloud
hosted OIDC, server-owned PostgreSQL sessions and application-owned memberships.
This guide covers provider configuration, threat cases and operator checkpoints.
Read its role and immutable-context contract before changing identity workflows.
Actual release-specific results belong in the [hosted rollout record](https://github.com/Roklion/Project_Tabula_Rasa/issues/321);
provider setup alone establishes neither hosted acceptance nor external admission.

## Stages and migration boundary

[External connections and existing entity claims](CONNECTIONS_AND_CLAIMS.md) owns
external connections and existing entity claims and local administration's encrypted
grant setup, Owner connection controls, claim preflight/idempotent apply and retained
local-admin boundary. Actual live mapping/grant installation and hosted acceptance
remain live owner cutover and hosted identity rollout.

| Mode | Allowed identity/data boundary | Exit or transition requirement |
| --- | --- | --- |
| Local development/test | Existing explicit loopback synthetic fixtures, or fake OIDC/disposable PostgreSQL tests. Real OIDC development uses a dedicated nonproduction client and separately named non-Secure loopback cookies. | Keep fixture injection unavailable on public hosts; no production secrets or private books. |
| Hosted restricted synthetic development | Existing TLS Basic gate, fixed synthetic operator/entity and independently authorized synthetic document binding. No unrelated users or live customer/owner books. | Retain [development promotion and read-only smoke](../operations/DEPLOYMENT.md) and [synthetic document setup](../storage/STAGING_RECEIPTS.md). The hosted synthetic acceptance runner is retired; [named-user acceptance](HOSTED_ROLLOUT.md) remains separate. |
| Lasting owner-operated use (owner-operated runtime and live owner cutover) | Named verified user, durable session and active Owner membership in explicitly bound existing entities. Normal authorized resolver, accepted authority/document bindings. | existing entity claims and local administration and hosted identity rollout as applicable plus live owner cutover's own operator acceptance/cutover; no synthetic login or blanket all-profile authority. Invitations/billing/full commercial commercial recovery agreement sign-off need not precede trusted internal use. |
| Unrelated external beta (external release and customer lifecycle) | Named MFA-protected sessions, server membership enforcement, invitation/onboarding and verified tenant/document/connection isolation. | Applicable identity control plane and hosted identity rollout evidence, platform operations recovery/operations and external release and customer lifecycle legal/release approval. Free provider plans never waive these gates. |
| Paid launch | Same named identity/security contract plus accepted customer lifecycle, capacity/support and operational guarantees. | external release and customer lifecycle and commercial recovery agreement's separate launch and recovery agreement; identity completion alone is insufficient. |

Named sessions and business membership authorize access to the browser/API runtime.
The [local operator CLI](../SETUP.md) manages Google grants, Sheets source access
and recovery. It uses the application password and protected filesystem access.
Credentials, workbooks and folders remain scoped to each profile. Commercial sign-in and business
selection grant no local authority. The [retained local administration inventory](CONNECTIONS_AND_CLAIMS.md#retained-local-administration-inventory)
defines the owners of those actions.

Named login does not migrate books or claim all configured profiles. existing entity
claims and local administration's explicit dry-run/apply maps an approved internal
user/org to existing canonical entity IDs and authorized document/connection bindings,
preserving acceptance, provenance and rollback boundaries. live owner cutover alone owns
actual private/live cutover. No new session or identity provider can silently activate
PostgreSQL authority or restore stale Sheets after target divergence.

Hosted deployments explicitly select `named_user` or `restricted_alpha`. Production
requires `named_user`, which rejects synthetic context, alpha credentials and alpha
document configuration. `restricted_alpha` is available for approved synthetic use in
development and staging. The environment loader defaults to `restricted_alpha` for
loopback development/test. An unknown mode rejects startup.

## Manual provider/secret/mail checkpoint

Preparation of code/tests does not require live credentials. An operator must authorize
provider creation/configuration, secret installation, fixture accounts and any paid plan
before hosted execution. No resources were provisioned by identity provider decision.
Record actual settings and recovery custody privately; public evidence uses synthetic
names, safe statuses and release/check IDs only.

1. Verify actual ZITADEL plan/region, available instance/client/admin quotas,
   rate limits, MFA/recovery capabilities and accepted availability.
   [Current pricing detail](https://zitadel.com/pricing/detail) lists one
   Free instance and one administrator. Never assume two free production and
   staging instances or a support SLA. If required isolation/recovery cannot
   fit the account, stop at this setup checkpoint for an owner decision about
   capacity/spend; do not share production configuration or change provider
   silently.
2. Establish distinct **production and nonproduction instances/issuers**,
   administrator credentials, user populations and secrets before lasting
   owner/live use. Nonproduction local/staging may share an instance with
   separate projects/clients and synthetic users. No staging credential, grant,
   callback, fixture user or session database is valid for production.
   Instance separation protects login/recovery policy as well as client secrets.
3. Create a project and **Web OIDC application** for each environment, using
   code flow with S256 PKCE and `client_secret_basic`: choose **Basic Auth**
   for the Web client's authentication method; FastAPI separately sends the S256
   challenge/verifier. Do not choose the public-client **PKCE** (`none`) option.
   Verify the combined exchange and wrong-verifier/wrong-or-missing-secret
   rejection as described in [ADR 0003](../adr/0003-commercial-identity-and-tenancy.md).
   Use hosted login rather than operating a custom login service. Verify discovery issuer/JWKS,
   configured signing algorithm support and the mandatory UserInfo request:
   with the default code-flow client, its subject must match the validated ID
   token and its email must be nonempty with boolean `email_verified=true`.
   Do not depend on optional email claims inside the ID token. Do not configure provider organization
   roles as application bookkeeping permissions.
4. Register only exact origins/URLs in the table below. No wildcard callbacks,
   transient preview hosts or forwarded-host construction. Keep same-origin
   browser/API, CORS disabled and narrow server endpoint allowlists. A local
   HTTP callback exception belongs only to its dedicated development client.
5. Use native email/password with verified email and enforced TOTP/WebAuthn
   MFA policy for named hosted users. Exercise enrollment, verification,
   lockout, lost-factor and password-reset flows with synthetic accounts.
   Social/enterprise identity, SMS recovery and app-stored passwords are outside
   this first release. Keep at least one independent recovery factor for the
   operator, with recovery material in the approved private vault.
6. Configure and verify a mail sender/domain/provider for production identity
   verification and password recovery, with separate staging delivery and
   recipient restrictions. ZITADEL's built-in SMTP is evaluation-only; test
   mail is not production recovery evidence.
   [Provider mail and security settings](https://zitadel.com/docs/guides/manage/console/default-settings).
   Test domain verification/delivery and failure handling before lasting owner
   use/external beta. Mail credentials remain in provider/server secret stores.
7. Install approved OIDC settings/secrets through Render/environment secret
   injection. Keep them out of image builds, `VITE_*`, source, OpenAPI, query
   state, telemetry, shell history and PR/test artifacts. Verify host-only
   Secure/HttpOnly/Lax session/login cookies and all unsafe Origin/CSRF checks.
8. Rehearse local session revoke/user suspend and provider recovery together.
   Document who holds provider admin access and how it is recovered within the
   actual plan's administrator limits. A provider admin is not automatically a
   Tabula Rasa Owner. No shared-password or synthetic break-glass login to books.
9. hosted identity rollout runs synthetic hosted
login/logout/revoke/switch/document/export tests,
   records exact release, schema and sanitized outcomes, and coordinates technical-alpha validation.
   Follow [deployment promotion](../operations/DEPLOYMENT.md) for immutable image,
   compatible rollback and secret custody. No schema downgrade, live migration
   or external admission is implied by that rehearsal.

| Setting / registration | Contract and owner |
| --- | --- |
| `TABULA_RASA_ACCESS_MODE` | named-user authentication and hosted identity rollout: explicit `named_user` or `restricted_alpha`, with the stage rules above. |
| `TABULA_RASA_PUBLIC_ORIGIN` | Existing exact origin, e.g. synthetic `https://stage.example.test`; loopback development uses `http://127.0.0.1:5173`. No path/trailing slash. |
| `TABULA_RASA_OIDC_ISSUER` | named-user authentication: exact operator-approved environment issuer; use its verified discovery endpoints, never a browser-selected URL. |
| `TABULA_RASA_OIDC_CLIENT_ID` / `TABULA_RASA_OIDC_CLIENT_SECRET` | named-user authentication: dedicated environment Web client; secret is server-only and hidden in settings repr/errors. |
| Registered callback | `PUBLIC_ORIGIN + /api/v1/auth/callback`; local callback goes through Vite's `/api` proxy. |
| Registered post-logout URI | `PUBLIC_ORIGIN + /signed-out`; optional provider logout uses client ID/state, not a token URL. |
| Session/login persistence | identity control plane and named-user authentication: environment's existing PostgreSQL database, opaque credential hashes and durable login transactions. No session signing key, refresh token or new credential database is needed. |
| Mail/recovery custody | Provider-admin-owned SMTP credentials and factor recovery; membership administration owns application invitation delivery, platform operations owns operational secret rotation/incident evidence. |
| Google/document secrets | external connections: authorized organization/entity connection secret reference, independent of OIDC settings. Existing alpha document secret files remain synthetic-alpha-only. |

Provider client-secret rotation interrupts new logins until configured coherently and revokes
affected application sessions and pending login transactions. User credential changes,
forced resets/recovery and compromise revoke that user's sessions across organizations;
environment-wide identity/session security or trusted provider key changes revoke
affected sessions within that environment. Require fresh login, preserve unrelated
users/environment isolation, and verify revocation through the ADR's coordinated
operator or authenticated provider-event path. The implemented path requires a
coordinated worker restart; see [rotation and
recovery](AUTHENTICATION.md#revocation-and-owner-recovery). Issuer/client changes
require reauthentication and explicit binding migration; never remap existing users by
email. Restoring PostgreSQL/control-plane state must invalidate restored sessions/login
transactions before ingress reopens (platform operations): restored revoked credentials
must not become live again.

## Hosted end-user self-registration

**Create account** starts `/api/v1/auth/register` through the existing Web OIDC
client and callback. ZITADEL explicitly supports `prompt=create` for its hosted
registration form ([onboarding](https://zitadel.com/docs/guides/integrate/onboarding/end-users),
[authorization parameters](https://zitadel.com/docs/apis/openidoauth/endpoints)).
Sign in retains `prompt=login`; both send `max_age=0` and require recent
`auth_time`, verified UserInfo, state/nonce/S256 PKCE and one-shot cookies.
Registration defaults to the allowlisted `/invitations` return path. No password,
email or invitation credential is placed in the authorization URL. This creates
an end-user identity, not a Cloud Portal administrator or application business.

Provider documentation verifies the protocol, not the actual tenant policy.
Before claiming hosted acceptance, the operator must inventory the selected
instance and effective organization policy, including overrides. Record safe
values, inheritance, login UI version, issuer/client reference and observation
date in the owning acceptance discussion; keep personal data, secrets and recovery
material private. The retained [synthetic staging handoff](https://github.com/Roklion/Project_Tabula_Rasa/issues/321#issuecomment-6293694820)
confirmed MFA use but retained mail/recovery gaps. It does not establish enabled
self-registration or successful registration mail. Fresh inspection and hosted
verification remain required; code-only implementation changes no provider setting.

1. In Console open **Default Settings > Login Behavior and Security**
   (`{instanceDomain}/ui/console/settings`). Inventory **Register allowed**,
   native username/password login, external identity providers, **Force MFA**,
   permitted factors and login lifetimes. Identify the default registration
   organization and inspect its login/security policy for overrides. Do not
   assume the project's owning organization selects the registration destination.
   Verify the actual hosted login version with the configured Web client.
   Under separately authorized provider configuration, enable **Register allowed**
   on the effective policy and save, preserving native login and mandatory MFA.
   Do not enable organization self-registration or grant provider admin roles.
2. Inspect instance and organization **Password Complexity**, **Password Expiry**,
   **Lockout**, **Notification settings** and verification-code lifetimes.
   Preserve approved password/recovery rules. Require email verification and
   approved TOTP/WebAuthn enrollment before app access. Check boolean
   `email_verified=true` through UserInfo; do not mark emails verified manually
   to pass acceptance or weaken required factors.
3. Verify sender/domain, delivery restrictions, verification and reset mail,
   lost-factor recovery and operator recovery custody with synthetic identities.
   Stop hosted acceptance if required delivery, recovery or factors fail. A
   provider screen or registration alone does not prove mail/recovery capability.
4. Preserve existing exact callback/post-logout allowlists, Basic Auth Web-client
   authentication and S256 PKCE. No new callback or scope is needed. Start at the
   app rather than a bare provider registration URL so OIDC context is retained.
5. Disabled registration may leave the browser on a provider refusal page.
   Return to Tabula Rasa for private operator guidance or Sign in with an existing
   account. Returned errors consume the attempt and use the safe failure page;
   expired/aborted flows need a deliberate new action. Never display raw provider
   errors, retry automatically or relax verification to recover.

See [settings and inheritance](https://zitadel.com/docs/guides/manage/console/default-settings)
and [hosted smoke checks](HOSTED_ROLLOUT.md#hosted-registration-and-invitation-smoke).
Registration grants no membership, ledger or beta approval and cannot bypass
cohort caps, pauses or release authorization.

## Business recovery and operator authority

Follow the ADR's explicit Owner-only backup, recovery, entity/workspace and connection
administration capabilities. Ordinary bookkeeping imports/exports retain their separate
role rules; a complete export is not destructive restore authority. Business Owners
approve backup/download, destructive restore, migration and privileged recovery for
their own businesses. Platform operators execute approved infrastructure recovery
without implicit ownership or ledger access. Record business, operation, owner approval
and operator identity before destructive business recovery. At execution, verify the
approving Owner is still active and authorized for the business; otherwise stop for a
new authorized approval. Approval covers one execution; reconfirm current Owner
authorization after deferral or a scope/operator change and record the execution with
that approval. Keep the legacy administrator's loopback listener outside public
proxy/ingress routes; forwarded headers confer no local authority. Platform operations
separately owns service-wide incident approval and audit. Tenant roles never grant
deployment/local profile secrets. No new recovery/admin endpoint or live execution is
authorized by this contract.

## Invitation and ownership policy

For the first restricted beta, select **operator-distributed invitations**: an
authorized Owner/Admin creates a recipient-bound, single-use, 256-bit invitation
credential with a 48-hour expiry; an authorized management view displays it once. The
inviter delivers it privately to the intended recipient, who signs in and pastes it into
an authenticated acceptance form. Do not put the credential in a URL, provider `state`,
browser storage or logs. This keeps the first app slice independent of an outbound email
service; it does not replace production identity verification/recovery mail. Automated
invitation email can be added by membership administration after its own sender/delivery
checkpoint.

Store only the invitation credential hash, organization, recipient email,
role, inviter and lifecycle. Acceptance requires a freshly provider-verified
email from login within five minutes matching the intended address; never a
client-supplied email. Define email comparison as trimmed, case-insensitive
comparison with **no** plus-tag/dot rewriting. It is an invitation recipient
check, not user identity or automatic account linking. If fresh authentication
is needed, discard the submitted invitation credential and ask the recipient
to paste it again after login. Do not retain the raw credential in process
memory, login transactions, cookies or other pending workflow state; the
durable invitation record retains only its hash and lifecycle.

Transactionally check current inviter/acceptor authority, invitation expiry/
revocation and permitted role, consume once and create/reactivate the unique
membership. If the inviter lost invitation/grant authority, deny acceptance.
Existing active membership acceptance is idempotent and never silently
upgrades a role. Owners may issue Admin invitations; Admins may issue only
Editor/Accountant/Read-only, and Owner assignment uses the separate ownership
action. Deny replay and cross-organization use. Pending invites grant no
discovery/document access. Preserve last-owner rules and safe 403 responses.

First-use business creation binds the signed-in internal user as Owner of that business. Ledger
bootstrap reuses `LedgerSetupService` defaults within the supported database
transaction/idempotent bootstrap; first-use onboarding shows start-fresh versus the
explicitly separate authority cutover and live owner cutover operator migration. Account
creation alone confers no access to any preexisting business. A user with no
memberships remains in the authenticated `needs_setup` state, able to log out, accept an
invitation or create their own business. Each business has its own role and private
authorization container; My Businesses is a flat authorized navigation list.

## Threat model and required acceptance cases

Trust boundaries are browser → same-origin API → control-plane/ledger database; API →
configured identity issuer; and authorized entity → DocumentStore/ external secret
store. Assume attacker-controlled URLs, bodies, headers, cookies, localStorage, client
roles, known foreign IDs and old valid sessions. Provider, host/operator secret
compromise and browser XSS remain real operational risks: HttpOnly does not stop
malicious same-origin code issuing requests. Preserve platform operations secret/logging
controls and browser testing browser hygiene; do not claim this ADR proves those
mitigations.

Use at least two independent organizations/users, two entities, a multi-org accountant,
all five roles and retained preexisting entities. The cases below define required
security behavior. Each selected release needs executable coverage and separate evidence
for provider-hosted cases.

| ID / attack | Required observable result | Primary owner |
| --- | --- | --- |
| T01: anonymous, forged/tampered, expired, revoked or wrong-environment cookie | Private APIs/bytes deny 401 before any ledger/document/secret composition; safe request ID, no credential/private echo. Valid fresh login succeeds and persists across worker/restart. | named-user authentication and identity security testing |
| T02: wrong issuer/audience/algorithm/signature/nonce/state, reused code, unverified email or stale auth_time | No app session/membership created; concurrent callbacks consume at most once. Default code-flow login succeeds using mandatory UserInfo with matching subject and verified email; missing/malformed email claims, false email_verified, subject mismatch or UserInfo failure deny login. Changed email never takes over an existing internal identity. | identity control plane and named-user authentication |
| T03: forged entity in route/body/header/selection cookie | Uniform safe 403 for foreign/missing/inactive ownership; no existence leak, services or grants bound. An authorized selector succeeds only after server validation. | server authorization and identity security testing |
| T04: role value or provider role claim changed by client | Server's current role matrix wins. Read-only cannot mutate/upload/import/export; it can read permitted receipts, inspect session, switch and log out. Unknown action/role denies. | server authorization and authorized workspace switching |
| T05: member demoted/removed, user disabled or org suspended with an open session | Next protected request after committed change denies/reduces authority. Global user disable denies every organization and revokes all of the user's sessions. Membership removal/demotion or organization suspension affects that organization while unrelated authorized organizations remain accessible. Version changes replace browser scope on its next check; visible tabs recheck within 60 seconds/focus. | server authorization, membership administration and authorized workspace switching |
| T06: foreign document ID/store key, unlinked receipt, import/export/job target | Every metadata/byte/connection access qualifies the authorized entity; no ID-only byte path or global credential fallback. Test permitted upload/download/export as well as denials. Jobs recheck scope at execution. | server authorization, external connections, identity security testing and platform operations |
| T07: session fixation, stolen old cookie or callback replay | Login replaces/revokes old session, new CSRF/cache ID; single-use login transaction. Copy of revoked cookie denies on another worker after restart. | named-user authentication |
| T08: missing/foreign/null Origin, missing/wrong CSRF, redirect/forwarded-Host spoof | Every unsafe command denies before effects, including logout/switch/bootstrap/invite; only state/nonce-bound callback is exempt. No open redirect, wildcard client, token URL or caller-selected issuer fetch. | named-user authentication and server authorization |
| T09: provider timeout/unknown JWKS key/mail failure | No new/unverified login, silent fallback or bypass of idle/absolute expiry. Valid existing local session follows ADR deadlines and fresh local authorization; provider-only recovery does not claim app revocation. | named-user authentication and hosted identity rollout |
| T10: database/authorization outage or uncertain logout | Safe 503 and private view unmounted; no cached allow/fixture fallback. Origin/CSRF-valid logout clears the local cookie even on DB failure; a copied cookie is not claimed revoked until DB confirmation. Fresh login and authenticated revoke-all/operator recovery succeed when service returns. | named-user authentication, server authorization and hosted identity rollout |
| T11: Admin self-promotion, owner removal/demotion races or provider-admin claim | Admin grant limits enforced; at least one active Owner or explicitly suspended org. Test concurrent last-owner changes and user-disable effects in real PostgreSQL. | identity control plane and membership administration |
| T12: replayed/expired/revoked/wrong-recipient invite or inviter loses authority | No unauthorized activation/role escalation. Correct verified recipient accepts once, duplicate submission creates no duplicate membership. Existing-member acceptance does not promote. Stale authentication requires fresh login and credential re-entry, including when a different worker handles acceptance; no pending raw credential is retained. | membership administration |
| T13: bootstrap retry/foreign org, arbitrary claim of old profile/entity | Acting user owns only their new workspace; no duplicate defaults/org or unauthorized legacy claim. existing entity claims and local administration explicit mapping preserves original entity/book IDs and independent grants after restart. | ledger bootstrap and existing entity claims and local administration |
| T14: account/entity switch, revoked tab, slow prior-scope response, logout → another login | Cancel queries/unmount drafts/uploads, revoke object URLs, clear selections; old responses never populate a new ScopeBoundary. No private persistent browser cache. Successful permitted multi-org switching remains usable. | named-user authentication, authorized workspace switching, first-use onboarding and identity security testing |
| T15: production/external accidentally configured alpha; restored session replay; unsafe rollback | Reject synthetic/Basic/development cookie configuration, reject restored sessions before ingress, and never roll external deployment back to an alpha bypass image. Safe schema-compatible named-session rollback is rehearsed. | named-user authentication, hosted identity rollout and platform operations |
| T16: private route added without policy; privileged valid request bypasses baseline/accounting checks | Route inventory test requires declared identity/capability policy before composition on every private route/byte/export family. Authorized stale edits still fail; valid bookkeeping retains invariants. | server authorization and identity security testing |
| T17: non-owner backup/restore/migration/admin request or platform operator treated as tenant Owner | Owner succeeds only within the approved business and supported recovery procedure; other tenant roles deny owner-only capabilities. Infrastructure operator authority alone grants no business access. Preserve blank-target restore and current backup validation; record owner approval separately from execution. | server authorization, existing entity claims and local administration and platform operations |
| T18: credential/key change, forced reset or provider recovery with old sessions | User change revokes that user's sessions in every organization; unrelated users remain authorized. Environment-wide change revokes affected sessions/login transactions in that environment only. Copied old cookies deny after confirmed revocation; fresh named login succeeds. Provider events must be authenticated or use coordinated operator revocation; no deadline-only fallback. | named-user authentication, membership administration and hosted identity rollout |
| T19: commercial user attempts to reach local profile administration | Login, role and business selection confer no local credential access. The operator CLI requires filesystem access and the application password. Its temporary Google listener binds to loopback. | existing entity claims and local administration, identity security testing and hosted identity rollout |

Denials and assertions must use synthetic data. Captured logs/HTTP errors must
exclude email, authorization codes, cookies, provider tokens, invitation
credentials, connection secrets, SQL and bookkeeping contents. No live
provider credentials are required for CI. Provider-backed staging tests are
separate operator-authorized evidence, not mocked proof.

## Component responsibilities

| Component | Contract and implementation owner |
| --- | --- |
| Identity and sessions | [Authentication](AUTHENTICATION.md): immutable issuer/subject bindings, durable login transactions, session deadlines, cookies and revocation. |
| Server policy | [Authorization](AUTHORIZATION.md): fresh membership-derived context, declared action capabilities and uniform private-route denials. |
| Workspace lifecycle | [Workspaces](WORKSPACES.md): permitted switching, recipient-bound invitations, last-owner rules and idempotent ledger bootstrap. Bootstrap reuses `LedgerSetupService`; it does not copy default-ledger rules. |
| Connections and claims | [Connections](CONNECTIONS_AND_CLAIMS.md): scoped metadata and protected secret references, separate login/Google grants, and approved existing-entity ownership without remigration. |
| Security verification | [Security tests](SECURITY_TESTING.md): synthetic users/entities, real disposable PostgreSQL, HTTP/browser journeys and explicit failure/isolation assertions. |
| Hosted operation | [Rollout](HOSTED_ROLLOUT.md): provider configuration, approved callback/secrets, MFA/recovery/mail, real rehearsal and compatible rollback. |

All components use the same cookie, role and request-context model. Release evidence
must distinguish synthetic tests from provider-backed observations. The [original
implementation
handoff](https://github.com/Roklion/Project_Tabula_Rasa/issues/310#issuecomment-6304260292)
is retained as history; it does not define current features.

## Operating boundaries and evidence

Every private route registers a server action policy before composing services. Local
credential administration and privileged support retain independent operator boundaries;
a tenant role grants neither. Existing-entity ownership claims and live authority
activation remain separate approved operations.

The [hosted rollout procedure](HOSTED_ROLLOUT.md) defines provider setup and
verification. Keep release-specific fixture bindings, partial results, failed checks and
approvals in the [rollout evidence
record](https://github.com/Roklion/Project_Tabula_Rasa/issues/321). The [security
evidence record](https://github.com/Roklion/Project_Tabula_Rasa/issues/320) and [login
recovery record](https://github.com/Roklion/Project_Tabula_Rasa/issues/395) retain
observed acceptance and diagnosis. None substitutes for the requirements defined in
these guides.

The current organization-scoped implementation does not establish independent
business-role acceptance. Verify the selected business-access UI, tenant isolation,
visual quality and recovery for the intended release. External beta, legal/customer
policy and commercial recovery require their own human decisions.
