# Server authorization and protected operations

The server resolves durable named-user sessions and current memberships before
authorizing an entity operation. [ADR 0003](../adr/0003-commercial-identity-and-tenancy.md)
defines roles and immutable request context. This guide inventories protected
actions and their failure boundaries; [security testing](SECURITY_TESTING.md)
verifies them. Provider setup, live claims and release admission remain separate
operator decisions.

## Request boundary

Every registered private FastAPI operation receives the application-wide
`authorize_request` dependency. Entity operations resolve a freshly validated
opaque session through `NamedIdentity`, which obtains current active user,
organization, membership, role and canonical entity ownership from PostgreSQL.
The frozen `RequestContext` is reused only within that request. A revocation
can stop all later requests; an already authorized in-flight request may finish.

The server-held selection must agree with the route entity. Paths, headers,
cookies, query parameters and body fields never grant organization/entity or
role authority. Cross-organization and absent entity selectors return the same
403 envelope. Missing/expired/revoked identity returns 401; unavailable
authorization storage returns 503. An unknown record within a permitted entity
can return 404. No failure falls back to synthetic authority or cached rights.

Action checks run before handlers bind bookkeeping services, document stores,
profile secrets or uploads. Authorized unsafe methods also require the exact
configured Origin and session synchronizer token, including read-only allocation
previews. Loaded-baseline validation and accounting/domain invariants continue
independently after authorization. Private responses retain `Cache-Control:
no-store`. Structured authorization-denial signals contain fixed event/failure
codes, registered route templates and safe request/correlation IDs; they exclude
raw selectors, identity claims, credentials and bookkeeping/document content.

Production and `named_user` mode require the composed `NamedIdentity` resolver;
injecting a plausible `RequestContext` with `trusted_development=False` cannot
replace it. Restricted synthetic alpha remains available only under its existing
explicit loopback/hosted gate. It cannot open a migrated profile.

## Action and role matrix

`application/identity.py` owns the five roles. The immutable
`application/authorization.py` registry owns action requirements. Requirements
are cumulative: import needs read, write and import; complete export needs read
and export. Tenant capabilities alone never confer independent operator or
one-execution recovery approval.

| Action | Required capabilities beyond ordinary read | Tenant roles |
| --- | --- | --- |
| `read` | None | Owner, Admin, Editor, Accountant, Read-only |
| `write` | `bookkeeping:write` | Owner, Admin, Editor, Accountant |
| `period_close` | `bookkeeping:write`, `period:close` | Owner, Admin |
| `period_unlock` | `bookkeeping:write`, `period:unlock` | Owner |
| `entry_correct` | `bookkeeping:write`, `bookkeeping:correct` | Owner, Admin, Accountant |
| `import` | `bookkeeping:write`, `bookkeeping:import` | Owner, Admin, Accountant |
| `export` | `bookkeeping:export` | Owner, Admin, Accountant |
| `entity_manage` | `entity:manage` | Owner |
| `organization_manage` | `organization:manage` | Owner |
| `members_manage` | `members:manage` | Owner; Admin restricted to non-owner/non-admin targets according to the membership-administration contract |
| `ownership_manage` | `ownership:manage` | Owner; fresh authentication and last-owner rules also apply |
| `connections_manage` | `connections:manage` | Owner; fresh authentication also applies to connection changes |
| `backup_create`, `backup_list`, `backup_download` | Corresponding `backup:create`, `backup:list`, `backup:download` | Owner |
| `business_admin` | `business:admin` | Owner; never the legacy local administrator |
| `archive_recover`, `restore`, `migration`, `cutover`, `ledger_reset` | Corresponding `archive:recover`, `restore:execute`, `migration:execute`, `cutover:execute`, `ledger:reset`, plus `recovery:authorize`, `platform:operate`, `recovery:approved` | Owner approval is necessary; no tenant session can execute these alone |
| `audit_inspect` | `platform:operate`, `audit:inspect` (no ordinary read grant) | No tenant role; privileged support has a separate operator policy |
| `legacy_admin` | `local:admin` (no ordinary read grant) | No tenant role; separate loopback/operator boundary remains separate from tenant identity |

`platform:operate`, `recovery:approved`, `audit:inspect` and `local:admin` are
deny-by-default requirements, absent from all tenant roles. This registry does not
provision platform operators, mint approval credentials or expose support actions.
Privileged support, recovery and live cutover require independent scoped approval and
execution procedures before any future adapter can execute recovery. Recovery approval
must identify the actor, affected entity, source/target, operation, operator and
permitted execution; revalidate active Owner authority at execution. Preserve
maintenance/source-freeze/evidence, current archive/checksum validation, blank restore
targets and live-target refusal. Never pass arbitrary client capability strings to
`authorize_action`.

Internal control-plane repository methods remain trusted building blocks. [Workspace
lifecycle](WORKSPACES.md) now supplies membership administration's HTTP
membership/ownership administration with target-role restrictions, recent authentication
for ownership changes and native last-active-owner constraints. Unclaimed entities have
no membership-derived access, and canonical ownership constraints prevent ambiguous
cross-organization associations.

## Exposed operation inventory

`http/authorization.py::ROUTE_POLICIES` enumerates each operation ID and exact
HTTP method. No unsafe-method-to-write fallback exists for registered routes.

| Current entity resources/commands | Action |
| --- | --- |
| Accounts, categories, payees, tags, transactions, journals: list/read | `read` |
| Accounts/reference and transaction/journal create, update, delete, deactivate, merge | `write` |
| Mileage list/detail; ledger and report summary | `read` |
| Mileage create/update/delete | `write` |
| Unreferenced document inventory, transaction receipts, receipt bytes | `read` |
| Receipt upload/link/unlink | `write` |
| Mixed-use list/detail; POST allocation preview | `read` |
| Mixed-use create/update/delete | `write` |
| Recurring list/detail | `read` |
| Recurring create/update, explicit reconciliation and state transition | `write` |
| Accounting period state inspection | `read` |
| Monthly close / unlock | `period_close` / `period_unlock` |
| Explicit linked reversing journal | `entry_correct` |
| Asset types/list/detail | `read` |
| Assets create/update/delete, explicit depreciation posting and override changes | `write` |
| Transaction/mixed-use copy draft preparation | `read` |
| Transaction bulk update/delete and Expense-to-recurring conversion | `write` |
| Transaction CSV, QuickBooks transactions and mileage preparation/preview/import | `import` |

Login and callback are the only public authentication protocol operations.
Period controls also revalidate active role/entity authority inside their
mutation transaction. Operator activation is independent and never granted by
a tenant role. See [accounting periods](../accounting/PERIODS_AND_CORRECTIONS.md) for the policy,
loaded version/baseline requirements and safe conflict contract.

Session inspection, permitted-entity discovery/selection, activity and
user-wide revocation receive central session/CSRF checks before their handlers,
using the same validator and immutable request-local session snapshot. They do
not require bookkeeping-write permission. Needs-setup
sessions can inspect identity and sign out without composing an unscoped ledger.
Workspace/bootstrap/invitation operations also receive central named-session
and Origin/CSRF checks without requiring an active selected business. Their
application/repository boundary checks the target organization's current
membership and role before binding a ledger or changing control-plane records.
Member/invitation listing requires Owner/Admin; membership edits use a loaded
authorization version; ownership grants are separate Owner-only commands with
fresh authentication. Bootstrap fixes new ownership to the acting user and
requires `entity:manage` for an additional business in an existing organization.
Authenticated invitation acceptance/decline uses the session's freshly verified
recipient email plus current inviter grant authority, expiry and single use.
The logout-context endpoint returns only the HttpOnly logout synchronizer copy,
never identity, membership or bookkeeping authority. Logout retains its
documented local-clearing behavior during a storage outage
without claiming confirmed server revocation. Framework API docs/OpenAPI keep
their existing protected middleware; health/version and the public browser
shell contain no private bookkeeping data.

Complete bookkeeping export, backup/list/download,
archive recovery/restore, migration/cutover/reset, deployment credentials,
legacy profile administration and audit/support are **not exposed HTTP
operations in this revision**. Existing operator CLIs remain internal and keep
their current independent target, maintenance, evidence and approval boundaries.
No role check turns them into public endpoints. Later HTTP/worker/support slices
must register the distinct action, authorize before resource/secret binding,
and apply their domain and operator preconditions. Login grants no access to
the local operator CLI and its protected profile store.

Tenant-owned Google connection status/connect/disconnect are exposed under
`connections_manage`; [Connections and claims](CONNECTIONS_AND_CLAIMS.md)
owns their approved provisioning and secret boundary. They never expose raw
credentials or confer deployment/local administration.

## Customer lifecycle boundary

`GET/POST /api/v1/customer-lifecycle` and
`POST /api/v1/customer-lifecycle/{request_id}` use the central named-session and
Origin/CSRF guards independently of selected-ledger access. The lifecycle repository
rechecks active identity and current business Owner membership at mutation time;
Admin and foreign-business requests are denied. Personal requests belong to the
authenticated actor. Confirm/cancel uses recent authentication and a loaded version.
Login closure rechecks last-active-Owner safety and atomically disables identity,
revokes sessions and records its transition. It never deletes a provider identity.

Confirmed business closure masks write capabilities, changes private cache scope
and fences canonical SQL mutations for workers/imports as well as HTTP. Owner
export and scoped connection recovery remain available during 60 days. Ordinary
business discovery/selection ends at expiry; lifecycle status remains available
to active Owners through the independent identity boundary. Disabled/suspended
access does not bypass authorization for export or recovery; use private support.
See [the lifecycle policy and recovery procedure](../operations/CUSTOMER_LIFECYCLE.md).

## Adding an operation and verification

Register an explicit operation ID, exact method, action and entity/identity scope
before adding a private route. Unknown/mismatched actions deny at request time;
the build-time route audit rejects missing policies, duplicate IDs, mismatched
scopes/methods, missing shared guards and non-FastAPI private routes. It examines
effective included-router metadata, including inherited dependencies/prefixes.
The audit covers all paths, including private routes outside `/api` and mounted
subapplications. Only exact health/version/browser-shell routes, protected
framework docs and the hashed static asset mount have explicit exceptions.
Unregistered FastAPI handlers deny at runtime even outside `/api`; the unused
framework OAuth Swagger redirect is disabled.
Do not assign a read action to bulk export or reuse a sibling operation ID.

The route inventory test independently compares effective entity routes with
generated OpenAPI and exercises each route for anonymous, foreign and absent
capability denials, plus read-only denial of every mutation. Tests also attach
a future handler without a context parameter and verify the central guard
stops it. Synthetic test-only bulk adapters verify distinct import/export roles;
they are absent from the shipped schema.

Native PostgreSQL/HTTP coverage exercises two users/organizations, fresh issued session
role changes/removal/disable/suspension/expiry/revocation, permitted reads and writes,
stale baselines, allocation previews, colliding document identities, forged
discovery/selection and unavailable authorization. Migrated-runtime tests verify that
the identity and ledger database/environment agree before binding profile secrets and
that accepted cutover/document bindings survive named-session restart without crossing
profiles. These synthetic tests do not provide hosted identity rollout's hosted ZITADEL
evidence or complete identity security testing and external release and customer
lifecycle acceptance.

[Security acceptance](SECURITY_TESTING.md) extends this evidence across the
complete private schema, native role/session matrix and same-browser
transaction/receipt/logout/other-user journey, with exact downstream gap owners.
