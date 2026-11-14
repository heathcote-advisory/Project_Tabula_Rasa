# Identity and tenant security testing

This repeatable synthetic gate verifies React/FastAPI/PostgreSQL identity and
tenant boundaries. It does not provision a provider, migrate owner data or admit
external users. [Hosted rollout](HOSTED_ROLLOUT.md) supplies provider evidence;
operations, recovery acceptance and release approval remain separate decisions.

## Repeatable local and CI gate

Use Python 3.12, `requirements-dev.txt`, Node 24 and `npm --prefix web ci`.
Follow [PostgreSQL setup](../database/POSTGRESQL.md) for a **disposable local test
administrator** URL in `TABULA_RASA_TEST_DATABASE_URL`. Never use the application
database URL or a retained provider database. Each native test creates, migrates
and drops its own randomly named database; there is no SQLite/mock fallback.

```powershell
python -m pytest tests/http tests/test_oidc.py tests/test_local_profile_admin.py tests/tooling/test_ci_workflow.py
python -m pytest tests/postgres/test_security_acceptance.py tests/postgres/test_authorization_http.py tests/postgres/test_identity.py tests/postgres/test_identity_http.py tests/postgres/test_workspaces.py tests/postgres/test_tenant_connections.py tests/postgres/test_connection_provisioning.py tests/postgres/test_legacy_claims.py
npm --prefix web run build
python -m playwright install chromium
$env:TABULA_RASA_REQUIRE_BROWSER_TESTS = '1'
python -m pytest tests/postgres/test_identity_browser.py tests/postgres/test_workspace_browser.py
```

Run repository Ruff and `git diff --check`; regenerate OpenAPI and the TypeScript
client and require no contract drift. The pinned Starlette transport retains
compatibility with the declared HTTPX TestClient dependency after a fresh install.

Repository CI's application Python job discovers the route audit; its PostgreSQL
integration job discovers the native matrix. Its native browser jobs explicitly run `test_identity_browser.py` and
`test_workspace_browser.py` after compiling the SPA with PostgreSQL and
`TABULA_RASA_REQUIRE_BROWSER_TESTS=1`; missing builds/databases fail that gate.
[Development](../DEVELOPMENT.md#authoritative-ci-and-required-check) owns the
current-revision **Full test suite** requirement. Affected local checks do not
replace the hosted merge gate.

## Coverage and limits

Tests reuse `NamedIdentity`, the session/control-plane/workspace repositories,
action registry, application services, receipt workflow and `ScopeBoundary`.
No parallel identity/authorization system is introduced. The signed fake-OIDC
adapter replaces only the external provider navigation and transport. App
login/callback, opaque cookies, database sessions, HTTP, ledger persistence and
document metadata remain real. Entity-scoped fake byte stores prove application
isolation, not managed-provider durability or grant configuration.

| Threat cases from [the identity contract](PROVIDER_SETUP.md#threat-model-and-required-acceptance-cases) | Executable evidence |
| --- | --- |
| T01/T05/T10: anonymous, tampered, expired/revoked/disabled credentials and authorization outage | `test_security_acceptance.py` exercises every centrally session-protected operation, including identity/bootstrap/membership, plus docs/OpenAPI, and asserts denial before service binding. `test_authorization_http.py` also covers suspended organizations, inactive entities and removed membership. |
| T02/T07/T09: signed-provider validation, callback replay/fixation and failure | `test_oidc.py`, `test_identity.py`, `test_identity_http.py`; the acceptance matrix also verifies failed provider exchange creates no session and cannot be replayed. |
| T03/T04/T06: forged scope/roles, every API family, document keys | The acceptance matrix applies an independently stated five-role policy to every entity route; all deny a foreign entity. `test_authorization_http.py` covers route/body/header/cookie forgery, permitted/stale writes and colliding document IDs. `test_tenant_connections.py` and `test_connection_provisioning.py` verify qualified grants, refusal before secret loading and revocation. |
| T05/T11/T12/T13: overlapping memberships, concurrent changes, invites/last owner and bootstrap | The matrix checks two sessions through selection, simultaneous committed changes and user-wide revocation, HTTP invitation replay, escalation and foreign bootstrap. `test_workspaces.py` covers concurrent last-owner protection, expired/revoked/wrong-recipient/stale-auth invitations and atomic failure/retry/bootstrap with two users. |
| T08: Origin/CSRF | The matrix rejects missing/null/foreign Origin and missing/wrong CSRF on every centrally session-protected mutation before composition. Logout's separate outage-clearing/CSRF contract remains in `test_identity_http.py`. |
| T14: private browser state and login as another user | `test_identity_browser.py` signs in, switches workspace/business, creates a transaction, uploads/links/downloads a receipt, signs out and signs into the same browser context as another user at desktop/mobile widths. Foreign metadata/bytes deny; ledger, history and persistent storage retain no first-user content. Workspace browser tests cover demotion/removal and cross-tab logout; React ContextBoundary/ScopeBoundary tests cover delayed responses, drafts and cache cancellation. |
| T15/T18: stage configuration and rotation/recovery revocation | Production requires explicit `named_user`; named mode rejects alpha credentials/context. The inventory tests reject environment mistakes and missing provider settings. Existing identity/native HTTP tests cover configuration rotation and user/session revocation. Hosted restore/recovery evidence remains downstream below. |
| T16: private route or transport drift | `test_security_inventory.py` compares the complete schema with policies and rejects missing dependencies, non-API private paths and arbitrary mounted apps. Runtime authorization also denies unregistered FastAPI handlers outside `/api`. Existing authorization tests cover duplicate/method/scope/prefix defects. Authorized stale-write/domain checks remain independent. |
| T17/T19: backup/recovery/operator separation and local admin | The explicit privilege matrix tests Owner versus Admin/Accountant/Editor/Read-only, independent platform execution and recovery approval. Native tests verify unavailable remote backup/restore/migration/profile-admin paths for all roles. `test_local_profile_admin.py` and `test_legacy_claims.py` retain loopback/operator/password restrictions and approved two-profile mapping. |

Canonical roles are `owner`, `admin`, `accountant`, `editor` and `read_only`;
“bookkeeper”/“viewer” mean Editor/Read-only. Named membership never becomes a
platform operator. The privilege test supplies independent server capabilities
to the policy only; it does not invent an operator login or public recovery
endpoint. Current CSV downloads retain their supported `read` policy; complete
bookkeeping export/backup and destructive recovery retain distinct capabilities.

An already authorized in-flight request may finish; revocation governs requests
after the change commits. Tests do not claim a transaction spanning provider,
control plane, ledger and browser, or protection from an administrator modifying
the running application. All data is synthetic.

## Sanitized evidence and unresolved gap register

Independent-business acceptance extends the existing matrix rather than replacing
it. `test_business_access.py` covers the business-only list, foreign member/invite
IDs, recipient preview without discovery or consumption, wrong recipient/stale
authentication/expiry/revocation/suspension, independent roles and preserved B
session/receipt bytes after revoking A. The workspace native-browser module adds
Owner A/Admin B, two independent Owners and one shared Read-only invitee through
review/acceptance, selection, refresh and isolated revocation on all three engines
at 1440/390/320px. Existing identity, security and scope tests continue to own
forged ledger/document/export selectors, session expiry/logout, slow-response
cancellation and draft/upload/receipt cleanup. Keep final business-first evidence
with the selected release's external-beta admission record; older workspace
tests and synthetic local passes do not establish hosted recovery or beta approval.

Local evidence identifies the tested Git revision/diff, PostgreSQL version, commands and
pass/fail totals. Hosted evidence also identifies release, schema and current-head CI
run. Retain synthetic test names, safe statuses, request/check IDs only. Never attach
cookies, invite credentials, emails, provider responses, SQL, secrets or customer
bookkeeping/downloads. Native tests assert no-store error envelopes and correlated,
redacted `identity_denied`/`authorization_denied` signals for revoked/forbidden
requests. These hooks do not establish hosted telemetry delivery or alert acceptance.

Reviewed implementation, remediation and validation results are recorded in [Identity security evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/320) and [PR
#374](https://github.com/Roklion/Project_Tabula_Rasa/pull/374). Historical local counts
and the pre-partition browser timeout are not current CI configuration; [CI
ownership](../development/CI.md) defines the required independent partitions.

The route audit's documentation exemption accepts only concrete Starlette HTTP
routes at the framework documentation/OpenAPI paths. Mounted applications at
either path are rejected, including child routes that would otherwise evade
the exact-path documentation authentication middleware.
The factory captures the generated endpoint identities before registering
application routes; only those endpoints with GET/HEAD methods qualify.
Arbitrary added handlers and replaced endpoint identities are rejected.

Current acceptance gaps and their dispositions belong in [Identity security evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/320), not a duplicated status table here. Its outstanding
hosted lifecycle/recovery requirement consumes [hosted identity
rollout](HOSTED_ROLLOUT.md)'s provider, MFA/mail/recovery, logout/revoke/outage and
rollback evidence. Approved fixture bindings and partial observations do not establish a
complete hosted pass.

[operational observability](../operations/OBSERVABILITY.md) owns delivered security
telemetry/retention/alerts; [operational readiness
verification](../operations/AUDIT_RECOVERY.md) consumes stage-specific DB/document
recovery evidence. [external-beta acceptance](../browser/RELEASE_VALIDATION.md) owns
external-beta admission and the selected-release business-centric access requirements
from business access policy and business-first navigation. [live owner
cutover](../accounting/CUTOVER.md) retains independent owner execution; external release
and customer lifecycle and commercial recovery agreement retain applicable release and
commercial approval. Keep each result associated with its actual tested revision and
supported scope; synthetic success does not waive these issue-owned gates.
