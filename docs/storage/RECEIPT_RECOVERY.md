# Drive receipt storage, backup and recovery

The current document path uses Google Drive for bytes and PostgreSQL for metadata
and links. It requires no AWS account, bucket, billing or S3 SDK.
[ADR 0004](../adr/0004-managed-receipt-storage.md) records the Drive-first decision.
[Optional object storage](OBJECT_STORAGE.md) remains deferred groundwork; adoption
requires separate review. Customer self-service uses the existing protected Drive binding.

## Contract and existing workflows

PostgreSQL `documents` owns canonical IDs, integrity metadata and current provider
locations; `document_links` owns bookkeeping relationships. Drive owns PDF/image bytes.
Normal runtime stays Drive-backed through the existing adapter and entity/profile
grant/folder binding. The browser uses authenticated same-origin receipt APIs, not
provider credentials. Existing narrow Google scopes, server authorization and external
connections membership/connection checks, alpha binding and owner-operated paths remain.
Unlink changes references only. Failed SQL linking retains uploaded receipts.

The [customer connection flow](../identity/GOOGLE_DRIVE.md) supports browser consent and
managed-folder setup when enabled, retaining operator-provisioned activation otherwise.
Both produce the same encrypted grant and DocumentStore binding used by recovery tooling.
Internal administrator OAuth stays loopback-only. Recovery commands still require an
approved private connection envelope; onboarding does not establish scheduled byte backup.

## Byte protection versus database recovery

DB snapshots/PITR (managed database recovery), SQL NAS archives (NAS backup and restore)
and canonical exports preserve metadata/links, not receipt bytes. Drive availability,
trash or file versions must not be assumed to supply a tested independent backup.
Inspect effective account storage/quota, shared-client/grant access, retention and
recovery controls for the intended stage; retain private evidence of the actual settings
and limitations.

The explicit byte backup command creates a private generation containing:

- `bookkeeping.json`: the existing validated v1 complete 26-collection canonical
  export, including exact receipt metadata/link ownership at the selected point.
- `binding.json`: format/version, entity, source provider/folder and the canonical
  export content checksum. It contains no credentials or parallel receipt model.
- `<canonical-document-id>.bin`: exact verified receipt bytes, with size/hash
  expectations from the canonical export. Provider filenames are not paths.

`bookkeeping.json` is published last. A failed generation without it is incomplete
and cannot restore. Repeating backup for the same metadata point verifies and
reuses complete byte files; a changed point requires a new generation. Corrupt
existing files stop rather than being overwritten. Source inventory is reconciled
with all retained canonical documents, including unlinked rows. Missing, duplicate,
unverified, mismatched or unregistered files block completion until explicitly
inspected/backfilled/registered through existing owners. No orphan is auto-deleted.

Archives are **private recovery artifacts**, never a live ledger/document backend.
They include private bookkeeping data and receipts: store outside Git in an
operator-restricted, encrypted volume/backup destination. Windows inherits the
private directory ACL; POSIX generation/files use owner-only permissions. The
command does not encrypt an unprotected destination for you. Retain the source
grant envelopes and private binding records separately under approved custody.

An explicit generation command is not an operational backup schedule. Scheduled byte
copying, NAS replication, independent/off-account copies, missing-run alerts, capacity
monitoring and achieved recovery objectives remain pending until actually configured and
verified. NAS backup and restore's SQL schedule/indefinite retention is unchanged.

## Private binding and verification

Use the existing external connections operator connection JSON format from [external
connections](../identity/CONNECTIONS_AND_CLAIMS.md), with exactly `environment`,
`provider` (`google_drive`), `organization_id`, `entity_id`, `profile_key`, `scope_key`
(receipt folder ID), `google_drive_oauth` and `google_drive`. Client/grant values stay
in this private file, not command-line arguments, browser DTOs, logs or Git. The
selected profile may differ from the canonical entity; the existing entity adapter
preserves that mapping.

Install normal API/PostgreSQL dependencies. Drive needs no
`requirements-object-storage.txt`. Apply the forward schema normally;
`0014_document_locations` retains prior locators without SQL receipt blobs.
Supply the approved private DB URL/TLS and connection-secret settings.

```powershell
python -m scripts.manage_document_storage verify --environment staging --input C:\private\drive-source.json
```

This verifies retained canonical metadata and exact provider bytes. It fails on
wrong ownership/scope, missing or disconnected connection, unavailable grant or
integrity errors. It creates no folders, grants, bookkeeping authority or files.

## Explicit byte backup

Approve the exact entity/source folder, DB binding, destination, private custody
and retention. Freeze entity bookkeeping/job/receipt writers while selecting and
copying the recovery point. Preserve original receipts and grants. The output
generation's parent directory must already exist and be private.

```powershell
python -m scripts.manage_document_storage backup --environment staging --input C:\private\drive-source.json --archive C:\private\receipt-generations\generation-001
```

For scheduled-protection rehearsals, use a private selector with the exact
environment, organization, entity, provider, profile and folder fields and
empty JSON objects for both `google_drive_oauth` and `google_drive`. Inject the
approved connection custody backend and key through the existing environment
configuration. Backup resolves the ledger's current reference through custody,
checks its scope before contacting Drive, and rechecks the connection and
canonical point before publishing the archive manifest. Freeze writers for the
coordinated point, including OAuth callbacks and direct Drive changes. Run
backup and archive verification for each explicitly enrolled business.

Dry-run verifies the source inventory and bytes and returns a safe plan hash and
counts without creating a directory. Review the exact private targets/limits,
then apply that plan with a retained operator decision hash:

```powershell
python -m scripts.manage_document_storage backup --environment staging --input C:\private\drive-source.json --archive C:\private\receipt-generations\generation-001 --apply --expect-plan <reviewed-plan> --approval-sha256 <private-decision-sha256>
```

Plans bind the actual DB fingerprint, private binding, connection revision,
canonical content and output directory. Changed inputs invalidate approval. Safe
progress counts identify verified copies. No receipt names/bytes or credentials
are printed. Partial files remain inspectable; nothing is pruned or rolled back.

```powershell
python -m scripts.manage_document_storage verify-archive --environment staging --input C:\private\drive-source.json --archive C:\private\receipt-generations\generation-001
```

Archive verification uses the canonical checksum, entity/provider/folder binding,
expected DB point and every byte's size/hash. It makes no Google calls, so an
unavailable/revoked source grant does not prevent verifying retained recovery
bytes. Missing/corrupt bytes, unsafe paths, wrong entities and incomplete manifests
block success. A checksum establishes integrity, not authenticity or custody.
For this offline command only, the binding's nonsecret environment/org/entity/
profile/folder fields may be reconstructed from the retained connection record
with empty `google_drive_oauth` and `google_drive` objects. Original tokens are
not required. Live verification and the recovery destination require valid grants.

## Isolated Drive and PostgreSQL restore

1. Obtain exact recovery-target/access/cost approval. Select the retained byte
   generation and corresponding DB point. Confirm the authorized primary and
   alternate recovery operators in the private handoff using the [operator handoff
   procedure](../operations/INCIDENT_RESPONSE.md#ownership-severity-and-alert-intake).
   Preserve the original grants, folders, exports and archives. Source deletion
   requires separate approval.
2. Restore DB into a separate staging target using
   [the canonical DB recovery guide](../operations/DATABASE_RECOVERY.md). Keep app/job writers
   disabled and production callbacks/browser access blocked. Verify schema/audit
   recovery and actual target isolation. No bookkeeping authority is activated.
3. Prepare a separate approved managed Drive recovery folder/grant for the same
   entity/profile/org using existing restricted setup tooling. Grant read/write
   only to that target. Folder creation/consent remains an explicit operator step;
   the recovery command does not provision it. Retain original grant envelopes.
4. Supply that target's private connection JSON and recorded source/target DB
   fingerprints. The selected target DB must match its fingerprint and differ
   from the source. Production mode and the original Drive folder are refused.

```powershell
python -m scripts.manage_document_storage restore --environment staging --input C:\private\drive-recovery-target.json --archive C:\private\receipt-generations\generation-001 --source-database-sha256 <source-fingerprint> --target-database-sha256 <isolated-target-fingerprint> --revision <restored-drive-connection-revision>
```

The restored DB must exactly match the archive's complete canonical point before
copying. Fingerprints check bindings, not provider restore lineage; operator
verification must establish isolation. Dry-run checks archive integrity without
uploading or changing SQL. Original Drive files/grant are not read or required.

5. Apply with exact reviewed plan and approval hashes. Existing Drive upload
   identity/retry handling copies each canonical ID with preserved stored/original
   filenames and bounded uncertainty. Conflicting names/identities stop. Explicit
   recovery does not suffix names, overwrite files, delete duplicates or remove
   originals. Metadata, checksum/size and exact target bytes are verified before
   one SQL transaction promotes all locations and the restored Drive connection.
   Financial history and link rows stay intact. Old locators remain in
   `document_locations`; old encrypted grant files are retained.
6. A failed copy leaves original SQL locations authoritative and verified target
   files recoverable. Resume the same point/IDs; inspect changed metadata/grants
   rather than forcing approval. A lost promotion result is resolved by repeating
   dry-run: it verifies the installed target without recopying. Subsequent point
   checks permit only the expected document location/version changes; changed
   financial data/link ownership is a discrepancy.
7. Run `verify` against the recovered binding. Confirm IDs, links, exact bytes,
   two-entity isolation and authorized receipt workflows before considering
   application rebind/service promotion. commercial recovery agreement and live owner cutover retain their authority and
   human approval boundaries. Do not rewrite old cutover evidence or silently
   reactivate a stale Sheets source.

## Retention, evidence and stage handoff

No automatic expiry/pruning of archive generations, Drive originals, provider versions
or grant envelopes is introduced. Coordinate external release and customer lifecycle and
audit retention policy policy and exact target approval before irreversible cleanup.
Document actual loss/time bounds, remaining capacity/retention/access risks,
secret/encryption-key custody and the alternate operator's ability to execute the
runbook. Publish only sanitized status/counts, version/release and private evidence
hashes.

Native tests execute real isolated PostgreSQL snapshot clones with fake Drive API
boundaries, two entities and differing profile keys, linked/unlinked receipts,
offline source-grant failure, partial copy/restart, mismatched DB points and corrupt
or unsafe archives. Real Drive client tests cover exact names and bounded retries.

For approved real-Drive staging verification, provide a private JSON object with
`alpha` and `beta`; each has `source` and `target` objects containing `profile_key`,
`google_drive_oauth` and `google_drive` in the existing validated formats. The four
approved synthetic managed receipt folders must be distinct and initially empty.
Each entity's source/target profile key must match and start with `stage-`.
The grants must have permission to create/read synthetic receipts in those exact
folders. The harness constructs disposable org/entity bindings, uploads synthetic
receipts, backs them up, clones a disposable DB and restores into separate Drive
folders. It retains original/target files and prints sanitized results only.

```powershell
$env:TABULA_RASA_DRIVE_RECOVERY_STAGING_BINDING = 'C:\private\drive-recovery-drill.json'
$env:TABULA_RASA_ALLOW_DRIVE_RECOVERY_STAGING_DRILL = 'approved'
# Set TABULA_RASA_TEST_DATABASE_URL only to a disposable test database service.

python -m pytest tests/postgres/test_drive_document_recovery.py::test_real_drive_staging_exact_byte_backup_and_isolated_restore -q -s
```

Run from the approved hosted staging environment to establish hosted evidence. Fresh
synthetic folders are required for another run; retained artifacts need separate exact
cleanup approval. [receipt recovery
evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/338) records approved
hosted drill results; [operational readiness
verification](../operations/AUDIT_RECOVERY.md) tracks stage-specific remaining evidence.
A previous drill never authorizes a new run. Synthetic tests do not establish an actual
NAS schedule, managed PITR, continuous RPO, full-service RTO or commercial operator
acceptance.

### Dedicated hosted recovery runner

The manual `Isolated hosted Drive recovery drill` Actions workflow runs the same
test on a GitHub-hosted Linux runner with a disposable PostgreSQL 18 service on
the job's private container network. It does not deploy an application or inherit
development/staging/production database credentials. The selected full source
SHA must belong to `main` history; workflow publication on the default branch is
required before manual dispatch. Approval/publication alone is not drill evidence.

Provision a separate GitHub environment named `receipt-recovery-drill`, restricted
to the default branch `main` (not tags). Only this environment receives
`DRIVE_RECOVERY_STAGING_BINDING`, the approved private JSON for four fresh empty
synthetic receipt folders, and `DRIVE_RECOVERY_ARCHIVE_KEY`, a Fernet key whose
independent copy remains in approved operator custody. Existing environment
secrets, saved profile bindings and Google grants must not be changed. GitHub
becomes an additional custodian of these copied credentials; folder selection
does not narrow the underlying Google grant's provider-enforced permissions.

The helper freshly checks actual account identity, private folder ownership,
write capability, managed profile bindings and complete folder emptiness before
running the exact opt-in test. It retains generated grant-envelope keys privately,
verifies archived bytes offline after success, and emits sanitized status only.
Provider/test tracebacks and private inputs are not printed. Failures retain
available private artifacts encrypted and do not delete source or target files.
Python optimization is refused so validation assertions remain active. The test
has an eight-minute subprocess timeout within the twenty-minute job budget,
leaving time to encrypt available evidence and upload it on test failure. Private
diagnostics retain only exception categories and frame locations; exception
messages, locals and raw pytest output are excluded. Runner termination or a
provider/preflight failure can still prevent complete evidence capture.

Only sanitized evidence and a Fernet-authenticated encrypted archive are uploaded.
The archive includes private binding/grant material, canonical manifests and
receipt bytes; its encryption key is never an artifact. Verify downloaded
ciphertext and recovered archive hashes before relying on this copy. Actions
artifact retention is requested for 30 days, subject to repository limits; retrieve
the encrypted generation to approved durable custody before expiration. Ephemeral
runner storage and expiring Actions artifacts are not long-term retention,
scheduled protection or proof of alternate-operator/off-device recoverability.

Current delivery results and unresolved work belong to the owning issues:

- [receipt recovery evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/338): selected-Drive
  backup/offline verification and isolated hosted DB-plus-byte drill evidence.
- [operational readiness verification](../operations/AUDIT_RECOVERY.md): actual
  provider capacity/access/retention, scheduled/off-device/alternate-operator
  protection and stage-specific recovery limitations; NAS controls remain NAS backup and restore.
- [live owner cutover](../accounting/CUTOVER.md): exact live
  owner bindings, accepted protection limitations and selected execution.
- [document-provider adoption review](OBJECT_STORAGE.md): provider
  decision/customer connectivity; external-beta acceptance and commercial admission consume stage-appropriate results.
- [commercial recovery agreement](../operations/DATABASE_RECOVERY.md): agreed
  recovery guarantees, human handoff and commercial activation.

Drive engineering acceptance needs no unused S3 provisioning. A completed
representative drill is not an operational backup schedule or comprehensive
recovery guarantee; keep observations, gaps and approvals in the owning issue.
