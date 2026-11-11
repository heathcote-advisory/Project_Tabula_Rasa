# Customer Google Drive connections

## Customer workflow

Select the business, sign in again if more than five minutes have elapsed, then open
**Data & backup > Google Drive connection > Connect Google Drive**. Confirm the business,
choose your Google account and approve file access on Google's consent page. The app
creates or reuses a dedicated root with Receipts and Ledger Backups children, validates
managed-folder access, and returns with Connected and the account email. The backup child
belongs to the existing folder contract; creating it does not establish independent backup.
Customers never enter credentials or folder IDs. The deployment must enable self-service.

Reconnect requires the original Google account and exact stored managed folders, retaining
legacy profile identities. It validates access instead of creating replacement folders.
Account or folder loss cannot silently replace existing receipt storage: restore original
access or contact support for separately approved isolated recovery. Receipt IDs, locations
and ledger links remain intact. Disconnect stops receipt access and preserves grants/files.
It does not revoke consent; Owners can separately revoke access in Google Account permissions,
which may affect other uses of the same client. Unlink never deletes bytes.

Connections belong to the business, not the authorizing membership. A departing Owner's
grant remains the binding. Remaining Owners can manage local access and reconnect only
to that same account. Arrange Google-account custody before departure; never silently
substitute the successor's account. Lost-account recovery requires independently retained
bytes and the approved recovery procedure. Membership never grants access to tokens.

## Recover after loss of credential custody

If the credential database is lost, provision an empty supported custody store with
normal runtime access before reconnecting. Keep the bookkeeping database and the
configured Google OAuth client. An enrolled business can still sign in and use
its books, and receipt access returns unavailable until an Owner opens
**Data & backup > Google Drive connection > Reconnect Google Drive** and authorizes
the original account. The app checks the original Drive account identifier and
validates the existing root and both managed folders before storing a fresh grant.
Existing receipt files, links and ledger data stay in place.

Customer connections enroll on successful consent. For a connection established
before revision `0022_drive_recovery_binding`, an Owner must disconnect and
reconnect while the original encrypted grant is readable. Complete that step
for each business before relying on reconnect-only recovery. If an unenrolled
business has already lost its grant, use the separately approved custody recovery
procedure. Reconnection depends on the original account and Drive folders still
being available.

## Security and persistence

Google authorization is separate from login. Only active Owners can start or complete it.
The start POST requires Origin/CSRF, recent login and the reviewed connection revision.
Forward migration `0018_drive_oauth` stores hashed unpredictable state/session references,
actor/business binding, expiry, consumed status and an opaque encrypted-verifier reference.
State expires after five minutes and is consumed before code exchange, including denial.
PKCE S256 binds the exchange. Wrong business/session, membership removal, forged/expired/
replayed state and stale revisions deny access. Session selection/expiry and membership
are checked again after Google I/O before registration. Starting again supersedes the
prior attempt. Interrupted or expired attempts show incomplete setup and can be restarted.
Credentials use the existing [encrypted grant custody](CONNECTIONS_AND_CLAIMS.md).
The bookkeeping connection retains a hash of the verified Drive account identifier
and the exact managed folder binding. Treat that metadata as private. It stays in
full bookkeeping database backups and is excluded from portable customer exports.

## Google Cloud and deployment setup

Configuration requires separate exact-target operator approval; these instructions do
not authorize live project, consent, credential or hosted-environment changes.

1. Use an operator-owned Cloud project and **Web application** OAuth client. Enable Drive
   API. Customer receipts need no Sheets scope, Picker key or service account. Existing
   Sheets administration and login-provider client settings remain separate.
2. Configure **External** audience for customers outside the Workspace organization. Set
   app name, verified authorized domains, support/developer contacts and reviewed homepage,
   privacy and terms URLs. Register the exact HTTPS callback for each approved environment:
   `https://APP_HOST/api/v1/connections/google-drive/callback`. Route it to FastAPI separately
   from the login callback. Local development permits loopback HTTP only.
3. Declare/request only `https://www.googleapis.com/auth/drive.file`. The existing client
   creates app-owned folders/files, queries appProperties, downloads/verifies bytes and
   reuses those folders on reconnect. These operations fit per-file access; arbitrary
   browsing/migration is excluded. Keep an existing binding's OAuth client identity:
   per-file access/appProperties belong to that application. Do not broaden scopes to
   repair an incompatible client change.
4. Complete applicable Google brand/domain verification and publishing requirements.
   Google's [scope guidance](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
   classifies drive.file as non-sensitive, which does not waive all brand/production checks.
   Inspect actual console requirements. Google's
   [audience rules](https://support.google.com/cloud/answer/15549945?hl=en) limit Testing to
   listed test users (up to 100); Drive authorizations and offline refresh tokens expire
   after seven days. Testing suits synthetic checks, not persistent beta access.
5. Inject an absolute private JSON file via `TABULA_RASA_CUSTOMER_DRIVE_OAUTH_FILE`:

   ```json
   {"google_drive_oauth":{"client_id":"OPERATOR_VALUE","client_secret":"OPERATOR_VALUE","redirect_uris":["https://APP_HOST/api/v1/connections/google-drive/callback"]}}
   ```

   Never commit the filled file. `TABULA_RASA_PUBLIC_ORIGIN` must match the registered
   origin. The secret stays server-side; incomplete/mismatched configuration fails closed.
6. Inject the environment-specific `TABULA_RASA_CONNECTION_SECRETS_KEY` and choose either
   `TABULA_RASA_CONNECTION_SECRETS_DIR` on a durable private **writable shared volume**,
   or `TABULA_RASA_CONNECTION_SECRETS_DATABASE_URL` for the
   [dedicated shared ciphertext backend](CONNECTIONS_AND_CLAIMS.md#shared-encrypted-custody).
   All replicas need the same selected store/key. Read-only Render secret-file mounts alone
   cannot persist grants. Preserve existing references/key through the approved copy procedure
   before switching a configured environment. Establish approved custody before enabling the
   feature; no infrastructure purchase is implied. Retain key/ciphertext backups separately.
   Failed/interrupted registration can leave encrypted unreferenced records for inspection; there is
   no automatic deletion. OAuth records/grants are excluded from bookkeeping exports.
7. Apply forward migrations through the approved deployment procedure before startup.
   Omitting this optional config retains operator-assisted activation and existing grants.
   A deployment without self-service is not ready for customer onboarding.
8. Inspect effective account storage, client/project quotas and Workspace/Advanced Protection
   restrictions. External audience permits Google accounts generally, but validate actual
   selected U.S. and Canadian personal/Workspace accounts: administrator policy or service
   availability may deny access. A Google account is required for this beta provider.
   Customer geography does not guarantee regional receipt processing/storage.

## Troubleshooting and hosted acceptance

Denied consent, missing offline/file access, folder validation failure, quota and provider
outage leave setup incomplete, consume the attempt and preserve the prior connection.
Retry deliberately after resolving quota/permissions/provider availability. Stable managed
folder discovery and bounded uncertain-create reconciliation remain owned by the existing
folder client. Duplicate identities require inspection, never silent choice or deletion.
Expired/revoked refresh grants encountered during receipt operations require reconnect.
Connected is the last confirmed binding, not a continuous Google health probe. Invalid
app sessions require sign-in; stale connection revisions require reloading.

On approved synthetic staging record the exact release/environment, console scope/publishing
state and sanitized pass/fail/counts for two businesses/grants, one Owner/Admin multi-business
user, denial/interruption/retry, wrong/replayed/expired callbacks, logout/session expiry,
membership removal during consent, folder failure/retry, same-account reconnect, wrong-account
refusal, grant revocation, quota/outage, upload/view/download exact-byte checks, link/unlink/
relink, disconnect preserving references, and restart with retained ciphertext. Keep codes,
state, query strings, tokens, emails and receipt bytes out of public evidence and ingress logs.
App access logs are disabled; operators must separately inspect proxy/provider diagnostics.
Use Google's [web-server OAuth contract](https://developers.google.com/identity/protocols/oauth2/web-server)
to verify redirect/offline behavior. Mocks do not satisfy hosted acceptance.

Independent daily-or-better database and receipt-byte protection, failure/staleness alerts,
coordinated restoration and alternate-operator custody belong to
[operations readiness](https://github.com/Roklion/Project_Tabula_Rasa/issues/342). Targets
remain independent DB/byte RPO ≤24h and full-service RTO ≤48h. Customer Drive alone is not
independent backup. Existing [archive/recovery formats](../storage/RECEIPT_RECOVERY.md) remain
unchanged; verification recognizes the new schema. Recovery commands still need an approved
private connection envelope; the customer UI does not export grants. Schedule/custody
integration and actual coordinated restore remain release blockers.
[Customer exports](https://github.com/Roklion/Project_Tabula_Rasa/issues/345),
[retention](https://github.com/Roklion/Project_Tabula_Rasa/issues/346) and
[legal/privacy](https://github.com/Roklion/Project_Tabula_Rasa/issues/347) must cover account
email, encrypted grants, provider processing and retained receipt/backup copies.
[Beta acceptance](https://github.com/Roklion/Project_Tabula_Rasa/issues/348) needs current
hosted evidence and explicit admission approval; implementation/merge is not GO.
