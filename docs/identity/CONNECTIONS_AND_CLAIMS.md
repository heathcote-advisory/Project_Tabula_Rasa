# External connections and existing entity claims

Connections preserve accepted PostgreSQL entity and ledger IDs, separate named
login from Google grants and preserve the separate loopback-only operator workflow
for local Google grants and Sheets source/recovery administration.
Operator grant installation, existing-entity claims and live authority activation
require explicit operator approval. Customers can use the [Owner consent flow](GOOGLE_DRIVE.md)
when self-service is enabled. [Hosted rollout](HOSTED_ROLLOUT.md),
[cutover](../accounting/CUTOVER.md) and
[receipt recovery](../storage/RECEIPT_RECOVERY.md) define the separate procedures.

## Ownership, secrets and operations

Forward revision `0009_external_connections` adds `external_connections`
(organization/entity, provider, original profile key/receipt scope, required
scopes, status/revision and opaque secret reference) and `legacy_entity_claims`
(approved source mapping, named owner/identity binding, approval/plan digests).
Composite foreign keys bind these records to persisted entity ownership.
Provider/profile and provider/receipt-scope uniqueness prevent two entities
from sharing one original Drive document namespace.
Neither table contains tokens or bytes. Canonical export v1 still contains
26 bookkeeping collections; control-plane/secret records are excluded.

Google grants use the same encrypted Fernet envelopes in private files or an
explicit dedicated credential database, outside the checkout, image and bookkeeping
database/exports. Deployment injects custody independently of ZITADEL login secrets:

| Setting | Operator responsibility |
| --- | --- |
| `TABULA_RASA_CONNECTION_SECRETS_DIR` | Absolute private directory holding encrypted `<opaque-reference>.grant` files. Operator grants support read-only mounts; customer OAuth needs durable private writable storage shared by API replicas. |
| `TABULA_RASA_CONNECTION_SECRETS_DATABASE_URL` | Optional PostgreSQL credential database instead of the directory. All replicas use the same target/key. Remote connections require TLS; inject a restricted runtime role. Never use the bookkeeping database or enable both backends. See shared custody below. |
| `TABULA_RASA_CONNECTION_SECRETS_KEY` | Separate environment-specific Fernet key from the deployment secret manager. Never a browser variable, command argument or committed file. |

Generate keys using the approved private secret-management process. File provisioning
uses exclusive creation and mode 0600; on Windows restrict the directory/file ACL to the
provisioning operator and service account. Protect ciphertext/key custody separately
under platform operations: a database restore alone does not recover provider access.
Rotate by staging grants in a new key/directory, switching configuration coherently and
having Owners reconnect. Retention or deletion of old recovery material requires
separate approval.

New opaque references start with a letter so their filenames can be uploaded
as Render secret files; existing 32-hex references remain readable. Runtime
reads also support mounted symlinks into versioned child directories beneath
the configured secrets root. The resolved target must remain inside that
operator-controlled root and be a regular file. Links escaping the root,
missing targets and non-file targets fail closed. Runtime never writes through
mounted links; operator provisioning still uses exclusive regular-file creation.
The root and its child directories must not be writable by application users
or other untrusted actors. Grant envelope checks still bind each read to its
exact environment, organization, business and provider.

Decrypted envelopes must match environment, organization, entity and provider.
No default profile, fallback grant, browser-selected secret path or identity
provider token supplies Google authority. Access tokens remain in the existing
Drive client's request lifetime. Missing keys/files, wrong envelopes and provider
failures deny bytes with safe unavailable errors. Ordinary ledger reads can
continue when a configured receipt connection is unavailable/disconnected.

## Shared encrypted custody

For stateless API workers, the optional PostgreSQL backend retains the existing
opaque references, Fernet encryption, exact environment/business/provider envelope
checks and append-only `put`/`get` contract. No plaintext tokens or business identifiers
are stored as SQL columns. It opens a short-lived connection per operation, with
five-second connection/statement timeouts and no idle pool. Budget active custody
connections alongside ledger/provider work; no workload capacity is implied. Failure
denies access without falling back to a directory or another grant. Runtime does no DDL,
overwrite, deletion, automatic transfer or key rotation.

An operator must approve the exact environment, database, roles, private transport,
capacity/cost and custody before provisioning. An existing approved PostgreSQL provider
may host a separate database if its plan/security boundaries permit it; no new provider
or paid upgrade is implied. Each environment needs its own database, role and key.

1. Provision a **dedicated empty credential database** and separate non-owner,
   non-superuser login role through the approved provider process. Keep administrative
   credentials out of the application. Apply
   [`connection-custody.sql`](../../deploy/commercial/connection-custody.sql) as the
   schema owner only on that approved database. It refuses nonempty databases and is
   separate from application Alembic migrations. Revoke unwanted default database/public
   schema privileges and grant the selected runtime role only database CONNECT,
   schema USAGE and table SELECT/INSERT on `connection_custody.grants`. Do not grant
   UPDATE/DELETE/DDL, role administration or ledger access.
2. Inject `TABULA_RASA_CONNECTION_SECRETS_DATABASE_URL` with that runtime role and
   secure transport (prefer `sslmode=verify-full` with the provider's trusted root).
   Preserve the existing environment-specific Fernet key. Do not expose URLs/keys in
   command arguments, console output, browser state or public evidence.
3. Before switching an existing deployment, freeze connection changes and preserve all
   current encrypted files and their original key independently. From an approved private
   source directory, run the operator-only copy tool:

   ```text
   python -m scripts.copy_connection_custody --source ABSOLUTE_PRIVATE_DIRECTORY --environment staging
   python -m scripts.copy_connection_custody --source ABSOLUTE_PRIVATE_DIRECTORY --environment staging --apply --expect-plan REVIEWED_SHA256
   ```

   Dry-run is default and performs only SELECTs on the destination. Review its target-
   and ciphertext-bound plan digest and counts, obtain exact-target approval, then apply.
   The copy validates the environment and decryption key, retains original ciphertext and
   references, uses one destination transaction and refuses conflicting existing records.
   Identical existing records are accepted on rerun; source files and application metadata
   are never changed. Preserve all retained references, including incomplete OAuth attempts;
   do not generate new references or rebind folders as a transfer shortcut.
4. Confirm destination counts and authorized resolution of every active connection.
   Coherently switch all workers to the same database/key and remove the directory setting
   from runtime configuration; configuring both fails closed. Retain the private source
   copy for recovery. Never roll back to an old file snapshot after new grants have been
   committed without separately reconciling those new references.
5. Before enabling customer OAuth, verify two synthetic businesses across worker recreation,
   same-account reconnect and exact-byte receipt access on the selected deployed revision.
   Local database tests are implementation evidence, not a hosted persistence drill.

Credential ciphertext needs its own independent backup/restore and alternate-operator
custody. Keep the key separately recoverable, preserve opaque references and coordinate
its recovery point with bookkeeping connection metadata and independent receipt bytes.
A ledger-only restore, customer export or customer-owned Drive cannot reconstruct grants.
Include this credential database in the operations recovery/access inventory and legal
retention matrix; neither the SQL setup nor a copied grant proves schedules, alerts or
RPO/RTO. No automatic pruning of retained grants or expired verifier envelopes is supplied.

## Operator-assisted Google connect and reconnect

When self-service is disabled, the retained operator path reuses [Setup](../SETUP.md)'s existing loopback-only Google OAuth
and managed-folder tooling. This local procedure does not use the customer callback. ZITADEL named
login and its cookie/session remain separate; Sheets/Drive use only
`https://www.googleapis.com/auth/drive.file`.

1. Obtain explicit owner/operator approval for environment, org/entity, original
   profile, Google account, client, receipt folder and secret custody. Verify
   exact immutable named user/identity binding privately; browser IDs, names
   and emails are selectors, not proof of ownership.
2. In the approved Google Cloud project enable Drive and Sheets APIs, configure
   consent/audience/test users and publishing/verification requirements. Verify
   current provider grant/lifetime restrictions before hosted use. Register the
   exact approved loopback redirect for existing local Google setup, independently
   of the ZITADEL login callback. Use separate staging synthetic resources,
   client secrets/grants/keys and production configuration; no live setup occurs
   from this implementation.
3. Authorize the intended human account through the restricted administrator
   with offline access and `drive.file`. Select/validate the original managed
   storage root, receipt folder and backup folder. Preserve appProperties/profile
   labels, file IDs and accepted receipt scope. Do not retag/move existing bytes.
4. Prepare private JSON with exactly `environment`, `provider`
   (`google_drive`), `organization_id`, `entity_id`, `profile_key`,
   `scope_key` (receipt folder ID), `google_drive_oauth` and `google_drive`.
   The last two use existing validated client/grant formats including the
   refresh token and managed folders. Never send this file to a browser/API,
   log or GitHub. Remove plaintext preparation material through the operator's
   approved custody process after provisioning; retain legacy config as required.
5. Select the exact target database through existing protected environment
   injection. Run the read-only preflight:

   `python -m scripts.manage_identity_bindings connection --input PRIVATE_FILE --environment staging --revision 0`

   It checks active ownership, current revision, retained metadata/cutover
   scope and fresh Google folder/profile ownership. It creates no secret/DB row.
6. Review its private plan and retain owner/operator approval. Repeat with
   `--apply --expect-plan PLAN_DIGEST --approval-sha256 APPROVAL_DIGEST`.
   A new encrypted file is created before the connection is staged as Pending.
   The approval digest identifies a retained private approval record; it is
   not authentication or infrastructure authority. A DB staging failure may
   leave unreferenced ciphertext for operator inspection; receipt bytes are
   never deleted as rollback.
7. The active Owner signs in within five minutes, opens **Data & backup >
   Google Drive connection**, reviews the business and confirms Connect.
   Backend checks current membership/capability, loaded revision, exact
   envelope, scopes and fresh managed-folder ownership before recording success.

For replaced/revoked/missing grants repeat staging with the current revision and
new approved grant, then confirm Connect. An existing valid disconnected grant
can be re-enabled with Reconnect. A stale revision fails without applying to a
replacement grant; commands are not automatically retried. Admin, Editor,
Accountant and Read-only cannot inspect/manage connections; permitted document
readers can still read receipts when connected. All unsafe requests retain
Origin/CSRF checks. Logged-out, removed and foreign members deny before secret
binding. Already-composed byte stores recheck current membership/connection.

Status describes local lifecycle, not a live Google health probe: Not configured,
Pending, Connected, Disconnected or Reconnect required. An observed Google authorization
failure during byte access records Reconnect required and returns a safe unavailable
error. Existing Drive scope/integrity validation remains. A future managed DocumentStore
can replace provider composition without changing user identity/sessions; document
provider configuration owns its selection.

### Disconnect versus Google revocation

A freshly authenticated Owner can confirm Disconnect. This durably blocks later
application access while preserving encrypted grants, files, ledger links and
metadata. It does not claim Google consent was revoked.

Provider revocation is a separate approved operator/Google-account action via
Google account permissions or the existing `GoogleDriveClient.revoke`. First
disconnect, confirm the exact grant's sharing/use and get impact approval.
Copied legacy refresh grants or shared Google client consent can also serve
retained Sheets/backup operations: revocation may interrupt them. Never
automatically revoke or delete files as rollback. If Google cannot confirm,
local disconnection remains effective but provider revocation is unconfirmed;
verify Google's controls before claiming completion.

## Claim accepted existing entities without remigration

Inventory owner-operated runtime and live owner cutover's private profile/entity, source
workbook/run, accepted document folder and target DB bindings first. Claims create no
ledger entity, import, authority activation or backup copy.

1. Verify independent operator infrastructure authority and the intended human's
   exact immutable identity binding. The user must already have verified named
   login and active Owner membership in the active target organization.
   Organization provisioning remains identity control plane and ledger bootstrap; there is no browser claim route.
2. Prepare private JSON matching `LegacyClaim`: `entity_id`, `profile_key`,
   `workbook_key`, `organization_id`, `owner_id`, `identity_binding_id`,
   `database_sha256`, `run_id`, `acceptance_sha256`, `document_scope`.
   Read exact values from approved private config and accepted cutover evidence;
   the existing endpoint digest contains no password. Prepared or active
   acceptance, source IDs and original receipt scope must agree.
3. Run `python -m scripts.manage_identity_bindings claim --input PRIVATE_FILE`.
   Read-only preflight verifies target, active Owner/identity and conflicting
   ownership. It grants no access.
4. After exact mapping review and explicit approval, repeat with `--apply
   --expect-plan PLAN_DIGEST --approval-sha256 APPROVAL_DIGEST`. Fresh checks
   and one transaction create org/entity binding and approved claim. The same
   approved apply is idempotent; mismatches and inactive/conflicting ownership
   fail closed instead of inferring an alternate target.
5. Associate the existing document grant through the connection procedure.
   Retained metadata and accepted cutover scope must match. Named migrated
   runtime requires the persisted approved claim, server profile/entity config
   and active PostgreSQL authority. Bytes use tenant connections with no
   legacy-token fallback. Internal legacy composition keeps its profile grant.
6. Under live owner cutover and hosted identity rollout's separately authorized
execution, restart and verify only
   approved named entity/document access, preserved reports/export and backup/
   cutover records for every selected profile. Tests here use two synthetic
   profiles; live owner cutover's actual three-profile owner acceptance remains outstanding.

Exceptions before transaction commit roll back claim and binding together;
dry-run needs no rollback. After a committed claim, do not automatically delete
ownership/replay imports. Suspend access using existing org/member/session
controls, retain evidence and arrange explicitly reviewed correction. This
does not roll back ledger authority: after divergence, the existing controller
still refuses stale Sheets reactivation. Use PostgreSQL/document recovery.

## Retained local administration inventory

The new commercial runtime needs named business access, approved provider grants
and separate database/document recovery. Sheets profile registration, workbook
Picker/bootstrap, local backup/Cloud Sync settings serve retained operator/source/recovery workflows only; the two preparation
converters are retired. These workflows are not new
commercial feature requirements. Streamlit Cloud Secrets handoff is retired.
Retaining an internal tool does not require porting it into the new application.

Local administration remains supported for those retained actions. Commercial
business creation, membership management and connection activation cover only part of its responsibilities.
The following matrix defines the supported replacement boundary; implementation
coverage does not attest to a selected deployment or authorize retirement.

| Action and current implementation owner | In-app coverage and disposition | Remaining operator / verification responsibility |
| --- | --- | --- |
| Authenticate and select a business (`http/identity.py`, React session and scope boundary) | **Verified replacement for commercial access:** named login and My Businesses resolve independent server-authorized memberships. **Retain** the local application password/unlock for local administration. | [Authentication](AUTHENTICATION.md), [authorization](AUTHORIZATION.md) and [business access](BUSINESS_TENANCY.md) own session, removal, foreign-business and cache isolation. Membership never unlocks local configuration. |
| Register a new business/profile (`local_profile_admin_forms.py`, `local_profile_store.py`) | **Verified replacement for new PostgreSQL businesses:** Create business calls `POST /api/v1/businesses`; `WorkspaceService.create_business` reuses idempotent ledger bootstrap and creates a private backing organization. **Retain** local profile configuration for Google grant/folder and source/recovery administration. | [Business access](BUSINESS_TENANCY.md) owns creation/isolation. Creation must not claim existing books, create a new Google grant or rewrite an accepted entity binding. Existing books use the separately approved claim procedure above. |
| Edit business name and local profile settings (`render_local_profile_edits`) | **Retain.** Business creation/member controls are not a profile-settings editor. The commercial business routes do not provide a general business rename or local workbook/path editor. | Local setup owns immutable profile keys, name validation, workbook ID and backup-path preservation. Identity/control-plane maintainers must verify any future edit replacement independently of creation. |
| Manage members, roles and invitations (business routes and `WorkspaceService.business_operation`) | **Verified commercial capability**, not local configuration authority. Business-specific Owner/Admin controls reuse server role rules; My Businesses lists only authorized businesses. | [Business access](BUSINESS_TENANCY.md) owns last-Owner, role-change, invitation and cross-business checks. Member removal is not deletion of a local profile, ledger or provider grant. |
| Configure Google client/consent and acquire or replace offline grants (`google_drive_admin.py`, Google OAuth helpers) | **Extend.** Configured [customer consent](GOOGLE_DRIVE.md) acquires the business grant. Retain operator client/consent configuration and loopback/source tooling. | Follow operator-assisted setup above and [Google setup](../GOOGLE_DRIVE.md). Verify exact account/client, approved loopback callback, `drive.file`, intended profile and folder; protect the shared client and each profile's grant separately. |
| Create/validate managed root, receipt and backup folders (`google_drive_admin.py`) | **Extend.** Customer consent creates/reuses dedicated managed folders and validates them. Retain local selection/provisioning for existing source and recovery use. | Operator verifies profile labels, parentage and accepted receipt scope. Preserve existing file/folder IDs and bytes. Do not infer ownership from a folder name or reuse another business's grant. |
| Picker workbook selection, initial workbook creation and recovery-workbook authorization (`google_drive_picker.py`, Google setup) | **Retain.** Native PostgreSQL bootstrap replaces only the new commercial ledger path, not Sheets provisioning or granting access to a restore target. | Verify selected profile/grant and exact workbook before saving. Existing ledgers are selected, not recreated. Recovery targets remain separate from the active workbook; [Setup](../SETUP.md) owns these actions. |
| Stage encrypted provider credentials / bind an accepted entity (`scripts/manage_identity_bindings.py`) | **Retain operator CLI.** There is no browser secret-upload or legacy-claim endpoint. Customer consent writes a new encrypted grant through the same custody; it does not expose secret upload or legacy claims. | Separate infrastructure authority, exact private mapping, dry-run plan and approved apply are required. Verify environment/org/entity/profile/folder envelope, revision and key/ciphertext custody as above. |
| Inspect connection status; Connect/Reconnect/Disconnect (`http/routes/connections.py`, `TenantConnections`) | **Verified in-app equivalent for application connection lifecycle:** Data & backup > Google Drive connection. **Extend** with configured customer consent; retain operator client configuration and separate Google revocation. | Active Owner only; changes require authentication within five minutes, Origin/CSRF and current revision. Foreign/removed/non-Owner requests deny before secret access. Disconnect preserves grants/bytes and does not revoke Google consent. |
| Export hosted Streamlit Secrets (`local_profile_admin_security.py`, former export CLI) | **Approved retirement.** The Streamlit Cloud handoff is no longer used. The UI action, export CLI and export-only tests are removed. Commercial provisioning already uses the encrypted operator path above. | Never replace this with a hosted credential export. Preserve protected existing configuration/recovery material; no secret file or provider grant is deleted by retiring the source code. |
| Configure local/Drive backup destinations and Synology readiness (`local_profile_admin_synology.py`, readiness helpers) | **Retain.** Commercial ledger export and scoped support diagnostics do not configure these paths or prove NAS delivery. | [Sheets backup/recovery](../BACKUP_RECOVERY.md) owns profile-scoped paths and readiness. [NAS database backup](../operations/NAS_BACKUP.md) and [document recovery](../storage/RECEIPT_RECOVERY.md) own the separate new-stack recovery paths; accepting one does not verify the other. |
| Restore/recover, change authority or perform privileged support actions | **Retain the separate operator procedures.** Support containment/diagnostics are not a browser clone of local restore or credential controls. | [Support](../operations/SUPPORT.md) requires independent operator grants and business authorization. [Cutover](../accounting/CUTOVER.md) and recovery owners retain exact-target approval, blank-target/live-workbook refusal, checksum/version checks, source freeze and post-divergence prohibition on stale Sheets fallback. |

### Local authority and credential custody

Local UI actions require `TABULA_LOCAL_PROFILE_ADMIN=1`, loopback-only Streamlit
binding, application-password authentication and administrator reauthentication.
The renderer rechecks the boundary before configuration access. Unlock is bound
to the current password hash so stale/misbound booleans do not survive rotation.
Commercial membership, claimed IDs and forwarded headers grant no local
authority. Keep the listener out of public proxies and use
`scripts/run_local_profile_admin.ps1`. The legacy application password/shared
OAuth client intentionally remain deployment-wide. Standalone operator CLIs
require protected OS/configuration access and the approvals in their own
procedures; they do not inherit a browser unlock.

Local profile JSON and previously generated Secrets files contain plaintext
grants and require restricted filesystem custody; source retirement does not
delete them. Commercial grants use the encrypted store described above, with independent key custody. Neither browser sessions,
DTOs, logs nor bookkeeping exports may carry these secrets. Verify two distinct
businesses even when the same human Google account serves both: workbook,
receipt namespace, grant and cached authorization must never cross scopes.

### Retirement decision and verification owners

**Retain** local actions marked above except the explicitly retired Streamlit
Secrets export. The profile/OAuth and receipt-association converters are retired. The
maintainers' direction to stop using local administration is conditional on full needed functional coverage. A
completed commercial business-access implementation is not proof of that
condition. A future removal needs a per-action verified replacement or an
explicit maintainer decision that the action is no longer needed, including
remaining operator access and credential/recovery custody.

[Connections and claims](https://github.com/Roklion/Project_Tabula_Rasa/issues/318)
and [existing-entity administration](https://github.com/Roklion/Project_Tabula_Rasa/issues/319)
own the retained provider/local boundaries;
[server authorization](https://github.com/Roklion/Project_Tabula_Rasa/issues/313)
and [support operations](https://github.com/Roklion/Project_Tabula_Rasa/issues/341)
own their distinct permission checks. Business-access owners verify only their
implemented creation/member/switching functions. Live selected-business acceptance
belongs to [owner cutover](https://github.com/Roklion/Project_Tabula_Rasa/issues/303);
[identity coordination](https://github.com/Roklion/Project_Tabula_Rasa/issues/222)
records the per-action retirement decision. This is a maintenance boundary,
not a new alpha/cutover gate or external-beta admission approval.

Source verification lives in `tests/test_legacy_admin_boundary.py`,
`tests/test_local_profile_admin.py`, `tests/test_sheets_oauth.py`,
`tests/postgres/test_business_access.py`, `tests/postgres/test_workspaces.py`
and `tests/postgres/test_tenant_connections.py`. These cover local boundary and
password rotation, removal of credential export controls, safe readiness
failures, selected-profile workbook operations, business roles/bootstrap and foreign/revoked/stale grant denials.
They do not certify live provider setup, NAS restoration or operator retirement.
[Explicit source preparation](../accounting/SHEETS_MIGRATION.md#explicit-source-preparation-tools)
owns current source-preparation and retired-format guidance.

## Reuse and verification

Reuse: shared membership/action policy, named sessions/freshness/CSRF, existing
cutover acceptance/authority/divergence, Google OAuth/managed folders,
EntityDocumentStore/profile labels and PostgreSQL receipt metadata.
New logic: connection lifecycle/secret resolution and operator claims.
There is no parallel ledger, login, OAuth migration engine or retry framework.

Regressions exercise real PostgreSQL claims, unchanged 26-domain exports and
cutover records, two-org grants, missing/revoked secrets, reconnect, revision/
freshness/CSRF, logged-out bytes and legacy remote/misbound authority. Browser
tests exercise confirmation, safe states, uncertain failure and cancellation.
Hosted provider setup, credentials, live mapping and release acceptance remain
explicit operator checkpoints, not evidence supplied by mocks.
