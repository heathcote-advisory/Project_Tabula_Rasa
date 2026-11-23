# Hosted synthetic receipt storage

This runbook binds the Google Drive byte adapter to the default HTTP runtime.
PostgreSQL remains ledger and receipt-metadata authority. It reuses the document
service and shared `create_app`/`open_runtime` composition. Grant installation and
hosted operations require explicit approval; this procedure does not activate a
migrated profile or configure commercial identity.
This explicit binding remains supported for restricted synthetic development/staging;
named-user storage and acceptance follow [hosted rollout](../identity/HOSTED_ROLLOUT.md).
[Release validation](../browser/RELEASE_VALIDATION.md) distinguishes automated journeys
from hosted evidence; the hosted synthetic acceptance runner is retired.

## Provision the independent staging grant

Use the existing shared Google Cloud OAuth project/client and only `drive.file`.
No additional Cloud project, subscription or provider is required. Recommend a
dedicated human test Google account: repeated authorization of the same account
and client is not a separate Google security boundary from that account's other
grants. Keep staging resources Restricted and separate from private/live profiles.
Record actual account/grant/folder custody privately, without posting identifiers.

Follow [Google Drive setup](../GOOGLE_DRIVE.md#connect-a-profile) in the existing
password-protected local operator CLI:

1. Create a dedicated synthetic profile (example key `stage-receipts`). Do not
   connect or modify an existing private business profile for this purpose.
2. Connect Google with the intended test account and the existing shared client.
   Retain that profile's independently authorized refresh token. Do not copy a
   production grant. Follow the existing consent/test-user/publishing rules;
   Testing grants can expire and require reconnection.
3. Create a new app-managed staging storage root and provision its Receipts and
   Ledger Backups children. The byte binding uses only Receipts; validating the
   existing complete managed-folder configuration does not enable cloud backups.
   The `google provision-folders` command can provision Drive storage without selecting a ledger.
   Do not create/select a private workbook solely for this API binding.
4. Validate the grant and folders with `google check PROFILE_KEY`. Do not revoke a shared account/application
   grant as part of staging teardown: that can invalidate other profile grants.

The local operator CLI handles Google consent, Picker and local profile
persistence. The synthetic API binding loads only the explicit server file
below. The operator provisions that file for the selected synthetic profile.

## Private server file contract

Create `tabula-rasa-alpha-documents-staging.toml` on a protected, non-synchronized
filesystem, preferably outside Git. Copy **only** the selected synthetic profile's
`google_drive` fields and the shared `google_drive_oauth` client settings from the
administrator's local files. Never export all business profiles. Verify the exact
profile key matches the managed children's `tabulaRasaProfile` metadata. These
are placeholders, not usable credentials:

```toml
environment = "staging"
public_origin = "https://EXACT-STAGING-ORIGIN"
entity_id = "alpha-synthetic"
profile_key = "stage-receipts"

[google_drive_oauth]
client_id = "SHARED_CLIENT_ID"
client_secret = "SHARED_CLIENT_SECRET"
redirect_uris = ["http://localhost:8501/"]

[google_drive]
refresh_token = "INDEPENDENT_STAGING_REFRESH_TOKEN"
token_uri = "https://oauth2.googleapis.com/token"
scopes = ["https://www.googleapis.com/auth/drive.file"]

[google_drive.storage_root]
id = "STAGING_ROOT_ID"
name = "Staging storage"

[google_drive.receipt_folder]
id = "STAGING_RECEIPTS_ID"
name = "Receipts"

[google_drive.backup_folder]
id = "STAGING_BACKUPS_ID"
name = "Ledger Backups"
```

Use an actually registered local redirect URI from the existing client. The API
only refreshes this grant; it does not need a new Render callback registration.
Optional `authorized_email`/`authorized_name` remain private. Picker key/project
number may be omitted together because this server runtime has no Picker.

Top-level keys must match the example exactly. File size is limited to 64 KiB.
Environment, exact public origin and entity must match HTTP settings. Profile keys
use the existing Drive pattern. Only the single narrow scope is accepted; all
three distinct configured folders must be present. Existing Drive validation
freshly checks folder access, parents, managed kinds, profile and root metadata.
A malformed file, wrong target, missing/revoked grant or mismatched child stops
startup with sanitized guidance. The loader cannot prove that a human chose an
independent grant/account/root; that custody decision remains operator evidence.

Absent `TABULA_RASA_ALPHA_DOCUMENTS_FILE` retains the explicit unavailable receipt
surface for deployments that have not configured it. An empty or invalid supplied
setting fails startup. Only hosted `development`/`staging` with fixed
`alpha-synthetic` may use it; production and loopback fixture contexts reject it.
Development needs its own file, grant/profile/root and matching origin, never the
staging file. Browser input cannot change any binding.

## Install in Render and verify

Coordinate an authorized staging configuration/deployment window and pause other
promotions/writers. Do not change Render or Google secrets/settings merely because
this runbook exists. Select an immutable image containing this implementation and
follow [the deployment procedure](../operations/DEPLOYMENT.md) with its current main
CI requirement. Existing retained staging records must not be reseeded/reset.

In the intended Render **staging service > Environment > Secret Files**, add the
file with the exact filename above and its private contents. Set this service's
environment variable:

```text
TABULA_RASA_ALPHA_DOCUMENTS_FILE=/etc/secrets/tabula-rasa-alpha-documents-staging.toml
```

Render [secret
files](https://render.com/docs/configure-environment-variables#secret-files) are mounted
at runtime. Adding/changing a secret file can redeploy the service; preserve the
selected immutable image and coordinate that deployment with deployment's promotion
window. Keep the binding service-specific, outside shared environment groups and GitHub
Actions/browser secrets. The API image installs `requests` and runs UID 10001 in group
1000 for [Render Docker secret-file
access](https://render.com/docs/docker-secrets#accessing-secret-files-at-runtime). No
credentials are image build arguments or image contents.

Use `python -m tabula_rasa.deployment.documents` with the same HTTP settings and
explicit absolute file path for a read-only preflight. With an authorized local
shell, install API/PostgreSQL requirements, privately export the environment,
origin, trusted entity, username/password **hash** and file path, then run:

```powershell
python -m tabula_rasa.deployment.documents
```

Or mount the file read-only into the exact selected image and pass those runtime
variables; no database URL or migration credential is needed:

```powershell
docker run --rm --mount "type=bind,source=$alphaBindingFile,target=/etc/secrets/alpha-documents.toml,readonly" -e TABULA_RASA_ENVIRONMENT -e TABULA_RASA_PUBLIC_ORIGIN -e TABULA_RASA_TRUSTED_ENTITY_ID -e TABULA_RASA_ALPHA_USERNAME -e TABULA_RASA_ALPHA_PASSWORD_HASH -e TABULA_RASA_ALPHA_DOCUMENTS_FILE=/etc/secrets/alpha-documents.toml $releaseImage python -m tabula_rasa.deployment.documents
```

Set `$alphaBindingFile` to the absolute protected file path and `$releaseImage` to
the selected immutable image. The command checks the same runtime binding/folders
and fresh scoped receipt inventory, including missing-integrity rejection. It
prints only `alpha_documents_readable` on success. Failure prints bounded guidance;
inspect privately, do not paste provider responses. It refreshes a token and reads
metadata; it uploads, deletes, links, seeds and migrates nothing. Preflight is not
proof of write permission, exact-byte persistence or final alpha acceptance.

After promotion, verify gated receipt availability, then perform the authorized
real-provider upload/attach/download/unlink/relink checks for the selected scope.
Record restart persistence and retained bytes. Unlink removes only PostgreSQL
relationships, never Drive bytes. Stable upload IDs and bounded uncertainty remain
owned by ReceiptService/Drive; do not add blind retries after failed alpha writes.

## Rotation, recovery and handoff

Reconnect only the synthetic profile through the existing administrator when its
grant expires/revokes. Replace the private file and restart/redeploy the approved
image. Retain the same profile/root/Receipts identity for existing receipts.
Changing folders does not move bytes or repair metadata; stop and inspect an
unexpected binding change rather than declaring old documents absent.
Startup refuses a binding that disagrees with retained PostgreSQL document rows,
including rows whose links were removed. Files uploaded but never registered in
PostgreSQL still require the operator to retain the original folder identity;
this check does not discover files in a replaced folder or migrate them.

Record actual Drive quotas/account capacity, shared-client blast radius, token expiry
and byte-recovery protections privately. PostgreSQL backup/export captures metadata and
links, not the bytes. Drive folder existence and these tests do not establish document
recovery, retention or full-service RTO; platform operations and commercial recovery
agreement own those agreements and evidence. Do not claim planned NAS protection is
active. Application rollback keeps the secret binding and files; select a compatible
prior image and re-promote/retest the candidate under the selected hosted rollout
procedure.

Remaining operator evidence: independent grant/folder custody, selected release's main
CI/image promotion, real-provider receipt/persistence checks, selected hosted workflow
checks, compatible rollback/re-promotion, manual devices/accessibility, actual provider limits and
sanitized acceptance handoff to live owner cutover. This implementation supplies no
hosted evidence, live owner migration or customer activation.

Reuse map: inspected `composition._document_store`, `MigratedProfileServices`,
`create_app.runtime_factory`, `EntityDocumentStore`, GoogleDriveClient/OAuth/folders,
ReceiptService and PostgreSQL composition/metadata, deployment packaging and
technical-alpha validation gates. Extracted the small existing Drive composition owner
for both legacy/migrated and hosted synthetic use. New logic is explicit target-bound
TOML loading and read-only preflight; no parallel credential, retry, state, storage or
authority framework.
