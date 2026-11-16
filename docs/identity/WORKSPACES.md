# Business switching, membership and onboarding

Business operations reuse durable named-user sessions, current
membership policy and the canonical PostgreSQL ledger. They create no Streamlit
profile, live migration or provider grant. This guide covers switching,
invitations, bootstrap and onboarding. [Connections and claims](CONNECTIONS_AND_CLAIMS.md)
owns external grants and existing entities; [hosted rollout](HOSTED_ROLLOUT.md)
owns provider verification.

## Switching and access changes

The labeled business selector lists only current server-authorized business names.
It is one flat list across independent backing organizations, with each business's
specific Owner, Admin, Editor, Accountant or Read-only role. **My Businesses**
also offers **Open business**, using the same discard confirmation and authorized
selection as the sidebar. There is no organization/workspace selector.
The business name and role come from server claims; backing organization names are
not product labels. Same-name choices in the selector and My Businesses append the
full stable business reference. Switch confirmation identifies its target reference;
the shell displays the selected reference even if the available list is refreshing.
References identify ledgers and confer no authority. My Businesses lists independently authorized business access
and offers Create another business. Read-only members can switch and sign out. Selection updates
the durable server session after Origin/CSRF and current membership validation;
an arbitrary URL, entity ID or role never grants access.

Confirming a named-user switch immediately unmounts private queries, drafts,
uploads and receipt previews. The existing scope boundary cancels queries and
receipt requests and revokes object URLs. Success loads the new scope and
returns navigation to Dashboard, dropping record-specific URLs. An uncertain
or denied switch shows a safe error with Reload/Sign out and retains no private
view. Restricted synthetic-alpha selection retains its existing failure/draft
behavior and keeps the current page after a confirmed switch. Other named-user
tabs receive a data-free scope-change signal and reload authority.

Visible named tabs also check on focus/pageshow/visibility and every 60 seconds.
Committed membership changes affect the next protected server request. Changed
authorization versions replace the browser boundary on its next check; revoked
selected entities fall back only to another currently permitted entity, or to
My Businesses (no active business). An already authorized in-flight request may finish.
There is no persistent private browser cache or client authorization fallback.

## Members and invitations

Open **Business & members**. Owner/Admin members see current membership and
invitation administration for the selected business. Newly created businesses each
have an independent backing organization enforced by the database. Ambiguous legacy
containers fail closed and block upgrade until separately approved reconciliation.
See [Business tenancy](BUSINESS_TENANCY.md) for persistent boundaries and rollout. This UI
does not split historical grants or change their scope. Admins can grant Editor, Accountant and Read-only,
demote or deactivate themselves, and manage non-Owner/non-Admin members. Owners can manage
all roles. Grant ownership is a separate explicitly confirmed Owner action;
Owner grants and removal/demotion require provider authentication within five
minutes. The database rejects removing the last active Owner, including races.
Loaded membership authorization versions reject stale changes.

The canonical settings route is `/business/members`; the old `/workspace` URL
redirects there. The header, invitation form and stable business reference identify
the selected business. Ownership actions have their own separated section.
Changes request a data-free authority check in peer tabs: only a changed identity,
selected business, authorization version or capabilities remount private state.
An unrelated membership change leaves the valid selected business intact.

After confirming a membership change or ownership grant, a live **Working**
message remains visible while both dialog actions are disabled. Wait for the
confirmed success notice or safe failure guidance. A rejected request restores
the dialog controls; reauthentication still requires signing in again. An
uncertain outcome disables resubmission: close the dialog and reload the member
list to inspect current state before another change. Commands never retry
automatically. The reported staging latency is a separate investigation; local
synthetic delayed-response coverage does not establish its cause.

For an authorized synthetic hosted latency investigation, identify the exact
release, route, browser, viewport, login age and safe request reference. Compare
the browser's confirm-to-status time, the mutation request's network duration,
the correlated sanitized HTTP `duration_ms`, and the subsequent member-list
refresh. Distinguish provider reauthentication, network/server/database waits
and list refresh; preserve rejection/uncertain stop guidance and never retry a
possibly completed mutation to gather timing. Retain only sanitized measurements
in the owning issue/release discussion. Reproducing a material delay requires a
focused follow-up with evidence and an explicit owner; local timing cannot close
that hosted investigation.

The approved early restricted-beta delivery is **operator-distributed**, not
outbound email. An authorized administrator enters the recipient email and role.
The creation view displays a cryptographically random 256-bit credential once.
The administrator shares it privately with that recipient, then dismisses it.
It is absent from listing responses, URLs, browser storage, logs and exports.
No sender/domain setup or paid mail provider is required for this delivery mode.
After an uncertain creation response, refresh the invitation inventory and
revoke the pending invitation whose credential was lost before issuing a new
one. The browser refreshes affected lists after confirmed or uncertain commands
and never automatically retries invitation or membership writes.
Automated mail requires the separate sender/domain/provider checkpoint in
[Commercial identity](PROVIDER_SETUP.md).

The recipient signs in with the intended account and pastes the credential into
**Respond to an invitation**, available in Business & members and the no-business My Businesses screen. Acceptance/decline requires verified provider login within five
minutes, with the same bounded 60-second future clock tolerance as verified
OIDC login. The session retains that login's verified email server-side; a browser
email or another session's later email change cannot substitute for it. Email
comparison trims and ignores case without plus-tag or dot rewriting. Stable
issuer/subject mapping remains the identity key; matching emails never link users.

**Review invitation** first shows the offered business name, stable reference and
effective role and exact expiration deadline to the verified recipient. Preview is read-only and grants no
membership or discovery of sibling businesses. Editing the credential discards
that preview. Accept/decline revalidates all guards and consumes the credential
once; preview cannot reserve or extend it. Confirmed acceptance selects that
business through the existing server selection boundary. If selection fails
after confirmed acceptance, reload My Businesses to select the granted business;
do not resubmit the consumed invitation.

Only the hash is stored with invitation lifecycle/recipient metadata. The native
transaction rechecks recipient, current inviter grant authority, expiry and
pending state, consumes once and creates/reactivates unique membership. An
existing active membership keeps its role. Concurrent/replayed acceptance grants
access once; later requests report already used. Invitations last at most 48 hours;
beta offers also expire at their approval deadline. Expired, revoked,
declined, invalid-recipient and reauthentication-required states offer safe
guidance. On required reauthentication, the form clears the credential; paste it
again after signing in. Pending invitations confer no discovery or ledger access.

## Backend bootstrap and retry contract

Bootstrap is available in the native PostgreSQL runtime. The migrated-profile
runtime rejects new-business creation before any records
are written, because it serves only the operator-configured entity/profile
bindings established at startup. Existing bound ledgers, memberships and
invitations remain available there; bootstrap status remains a read-only lookup.

Authenticated named users, including users without memberships and Read-only
members of another business, can call `POST /api/v1/businesses` with `request_id`
(16–128 ASCII letters, digits, underscores or hyphens, one recoverable URL segment)
and `business_name` (required, trimmed, at most 160 characters), plus country/currency
(default US/USD; Canada/CAD creates independent CAD books). After required external admission approval, the server creates
a new private backing organization, grants the acting user Owner and creates one
canonical business. There is no workspace-name field, existing-organization selector,
caller-supplied owner/entity ID or implicit copy of other members. The result exposes
only `state` and `entity_id`. Books reuse `LedgerSetupService`'s standard chart and
Income/Expense categories; opening balances start at zero. No new tax or
default-accounting logic is introduced.

`POST /api/v1/workspaces/bootstrap` remains deprecated compatibility for older
callers with `workspace_name` and `business_name`; it applies the same admission guard
and always uses a new container once approved.
The deprecated `/api/v1/workspaces/{organization_id}/businesses` rejects new additions,
even for Owners, while permitting authorized exact replay of historical receipts.
Internal organization/schema names remain implementation details. Existing
multi-business containers block the forward constraint upgrade and require explicit
role, invitation, provider and claim reconciliation through the
[non-destructive tenancy rollout](BUSINESS_TENANCY.md). New creation does not convert those records. Do not silently
convert existing staging or owner data or infer independent historical grants.

One database transaction creates organization, Owner membership, canonical
business entity, ownership binding, default records and an idempotency receipt.
Failures roll everything back. Concurrent submissions with the same acting user
and request ID serialize and return the same result. Reusing that ID with a
different normalized payload returns conflict; separately approved distinct IDs create
independent businesses. Preserve the request ID when retrying an uncertain
response. `GET /api/v1/businesses/creation/{request_id}` (legacy status remains compatible) returns `ready` with
the authorized business ID, the current private admission state when no ledger exists,
or `not_started` when neither exists. Another user
cannot inspect the receipt. There is no half-created/incomplete durable state:
resume means retrying the same request. Receipts and ledger defaults survive
pool/worker restart. Session inspection then discovers the first available entity.

After an explicitly approved operator tenancy reconciliation, a historical
receipt retains its request ID and original payload hash but can reference the
business's new private container. The original result/mapping remains in the
operator's private recovery/provenance record. Exact replay/status authorizes
the receipt's current business and returns that current container; ongoing
access to its former container is neither necessary nor sufficient. Legacy
organization-bearing POST replay still requires `entity:manage` in the current
business. Status retains its existing read-access contract. Revoked/inactive
membership denies access; an altered original payload conflicts, and replay
never creates another business or default records. This is explicit maintenance,
not an automatic conversion on login, retry or startup.

## Browser onboarding

Sign in (or register with the configured hosted identity provider). With no active
business, **My Businesses** offers **Create your first business**, **Join an invited
business**, or **Move existing Sheets books**. Existing members enter their selected
ledger and can open **My Businesses → Create another business** regardless of their
role in the currently selected business. Each deliberate creation uses a new private
permission container, without copying other memberships. The membership settings
page no longer offers Add business within an existing container.

Fresh setup asks only for a business name, then reviews standard bookkeeping defaults.
Back preserves the name before submission. **Create business** uses the existing
atomic bootstrap, selects through server-authorized session selection, signals peer
tabs to discard stale scope and opens Dashboard with account review, first transaction
and ledger actions. Visiting, returning or opening an invitation never creates records.
Shared section spacing distinguishes heading-to-content gaps from independent sections,
including the section following Review defaults. Feedback and focus use shared controls.

The tab stores only an opaque request ID keyed by authenticated user and creation
context (first use or the currently selected business), never names,
invitation credentials, sessions or ledger contents. Completed first-use and other
business-context handles cannot become the next creation. After refresh, reopen creation:
a committed receipt selects its authorized business; otherwise re-enter the original
business name. After an uncertain response, one bounded status readback may confirm
completion. Explicit **Retry same setup** retains the same request and payload; no
automatic create retry occurs. Browser storage must be available before creation.
Closing the tab ends the browser resume handle; server receipts and completed businesses
remain durable and discoverable at login. An old first-use receipt can still resume
through the new business status endpoint without provisioning again. An additional-business
receipt from the preceding workspace interface is also resumed from its original
user/organization handle and retired only after authorized selection. If a completed
receipt cannot select its business, retry repeats only authorized selection, never
creation.

Privately distributed invitations are supported now: share the app's `/invitations`
route separately from the credential. That route returns to acceptance after login for
both new and existing members, without creating an organization. Credentials must be
pasted after login and never enter URLs. Confirmed acceptance uses the same peer
scope-change signal as fresh setup, so other tabs clear stale setup or ledger views and
reload their authorized business. Internal connections remain optional through Data & backup. Beta receipts require
customer-owned Google Drive; collaborator invitations require individual program
approval and may be sent later. Existing live Sheets
migration remains the explicit authority cutover and live owner cutover operator
process, with no browser import or authority switch during setup.

Apply forward migration `0011_control_plane_join` before this browser revision. The
canonical chain is `0009_external_connections` -> `0010_workspace_lifecycle`
-> `0010_onboarding_routes` -> `0011_control_plane_join`. The published connection
and workspace revisions are unchanged; onboarding extends their chain. The onboarding
migration extends the exact server/database login return-path allowlist with
`/invitations`, preserving all existing sessions, login attempts and bookkeeping
records. External URLs and query-bearing return paths remain forbidden. Native browser
tests cover desktop/mobile setup, failures, lost responses, refresh, repeat visits,
invitations, two-business switching and foreign-entity denial. This is local/CI
evidence; identity security testing and hosted identity rollout and platform operations
and external release and customer lifecycle still own adversarial hosted acceptance,
operations/legal and external-beta admission within the identity layer.

## Migration and validation

The browser uses `GET /api/v1/businesses` for flat business choices (ID, name,
role only), `/api/v1/businesses/{business_id}/members` and
`/api/v1/businesses/{business_id}/invitations` for administration, plus
`POST /api/v1/invitations/preview` for recipient review. Backing organizations
resolve server-side from fresh authorized business access; body/header selectors
grant no authority. These adapters reuse the original role policy, lock order,
membership versions, recent authentication and invitation transactions.
The `/api/v1/workspaces/*` routes are deprecated compatibility, including receipt
status and membership APIs. Existing session/entity DTO organization claims remain
compatibility/internal context for old clients and creation-receipt recovery;
they are neither product navigation nor authorization supplied by a browser.
Per-business pseudonymous HTTP telemetry uses the resolved target business,
even when it differs from the selected ledger; no invitation credential or
recipient email enters telemetry. The existing privileged accounting-audit
boundary remains unchanged and is not exposed as member-readable history.

Apply forward revision `0010_workspace_lifecycle` using the existing explicit deployment
migration command. It adds invitation/idempotency tables and the Server-only verified
session email; existing sessions may keep bookkeeping access but must sign in again for
invitation acceptance/ownership changes. It does not rewrite ledger records or export
format v1. Identity/session/invitation/bootstrap records are excluded from entity
bookkeeping exports and recovery comparisons. Restored sessions still require platform
operations's existing revocation procedure.

`tests/postgres/test_workspaces.py` covers native concurrency, rollback after
default seeding, retry/restart, two-org isolation, independently owned businesses,
verified-recipient/freshness/replay, lifecycle, role escalation, stale edits and
HTTP/CSRF/secret-safe denials. `test_workspace_browser.py` exercises two users at
desktop/mobile widths, switching, membership administration, revocation, logout
and accessibility using the compiled app and disposable PostgreSQL. These are
synthetic local/CI checks, not hosted-provider or external-beta approval.

Business tenancy requires forward revision `0016_business_tenancy`. Read the
[business tenancy rollout](BUSINESS_TENANCY.md) before deploying; an ambiguous
legacy container blocks migration without automatic conversion.

The session's `business_creation_available` flag reports the composed bootstrap
service's availability, independently of the selected business role. Both first-use
and existing-member views offer creation only when it is true. Migrated-profile
compositions keep bootstrap disabled; invitation and operator migration paths
remain available, and the service continues to reject unsupported creation.

## Program admission

[Invitation-only beta admission](../operations/BETA_ADMISSION.md) adds server-side
program approval to the existing creation/invitation transactions. A new external
Create Business request may remain pending without a ledger; retry its exact
request after operator approval. Independent roles and existing access/export
remain authoritative during enrollment freezes.

## Invited users without a provider account

Open Tabula Rasa and select **Create account**. Register with the invited email
on ZITADEL, verify it and enroll the required authentication factors. Existing
users select **Sign in**. Registration returns to **Join an invited business** at
`/invitations`: paste the credential shared privately by the administrator,
review the specific business/reference and role, then accept. Never put the
credential in a link or create a replacement business to accept an invitation.
Registration grants no business access or beta approval.

Without an invitation, My Businesses retains **Join an invited business**,
operator migration guidance and **Create your first business** only when the
server enables that path. Creation still requires admission approval and respects
caps and pauses. Registered but not yet invited/approved users should contact the
administrator or operator through their private channel. If registration, email
verification or factor enrollment fails, use the signed-out guidance or existing
Sign in. [Provider setup](PROVIDER_SETUP.md#hosted-end-user-self-registration) owns
operator troubleshooting and recovery.

## Business currency settings

In **Business & members**, Owner/Admin can choose **Business currency** (USD or
CAD), select **Save currency**, and explicitly confirm **Change currency label**.
The confirmation names the business/reference and explains that 100.00 USD
becomes 100.00 CAD (or the reverse) with no exchange rate or FX conversion.
Existing and future amounts keep their exact numeric values, precision, signs,
rounding, recurrence and mixed-use rules. Country, cohort admission, ownership
and billing remain separate. The business root owns the sole current currency
setting. New-business setup still requires US/USD or
Canada/CAD with its existing admission approval; editing a label grants no access.

`GET /api/v1/businesses/{business_id}/settings` loads currency and version.
`PATCH /api/v1/businesses/{business_id}/settings/currency` requires
`currency_code`, the loaded `version`, and `confirm_label_change: true`, plus
Origin/CSRF protection. Fresh active Owner/Admin membership is rechecked at the
mutation boundary. Other roles, foreign businesses and closed businesses are
denied. Concurrent/stale edits conflict, including a change away and back.
A no-op does not advance the version. One transaction changes only root currency,
version and timestamp and records actor, before/after codes and timestamp in
private audit history. No financial or receipt rows are rewritten.

Session cache scope includes the settings version. Confirmed or uncertain commands
request local and peer authority refresh; changed scope clears private queries
and drafts. Other visible sessions also refresh on focus/pageshow/visibility and
every 60 seconds. Server requests resolve current metadata. Current exports and
explicit import-currency validation use the new unit; archived exports remain
unchanged. After a rejected or uncertain save, reload Business & members to
inspect the current setting before another deliberate edit. Commands never retry
automatically. Apply forward revision `0023_business_currency` before serving
this workflow; it replaces the original immutable currency-update guard.
