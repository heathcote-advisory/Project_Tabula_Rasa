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

### Existing NAS receipt copies

Owner-operated receipt protection continues through the existing Google Drive
Cloud Sync mappings to the NAS. Keeping receipt bytes in Drive does not require
a separate application downloader or duplicate staging capture jobs. Database
archives and receipt bytes remain separate recovery components; a SQL archive
alone does not contain receipts.

For each selected business, preserve its Drive-folder-to-NAS mapping and verify
copy freshness, source-deletion behavior, destination protection and recoverable
key/access custody against the chosen operating requirements. A Cloud Sync
"Up to date" status alone does not prove historical retention, decryptability or
a successful restore. Use the separate-target byte and metadata/link recovery
procedure below. Existing owner-operated evidence is scoped to its verified
sources; it does not establish unrelated-customer RPO/RTO or staging coverage.

## Production receipt capture runner

`python -m scripts.nas_receipt_backup` provides an opt-in, production-only NAS
runner over the existing `manage_document_storage` backup and archive verifier.
Its presence does not establish an installed schedule or operating protection.
Staging selectors and grants cannot be substituted for production bindings.
Existing Cloud Sync mappings remain separate protection; inventory them before
adding a second byte capture job. The PostgreSQL Wednesday/Sunday schedule is
unchanged by this runner.

Before activation, the responsible operator must retain a private decision
covering the exact current production source set, database identity, NAS mount,
public age recipients, separately recoverable decrypt identity, retention,
primary/alternate operator, cadence, resource/cost budget and alert recipient.
Selecting all currently deployed businesses means explicitly enrolling each
verified current binding; future businesses are not automatically enrolled.
Reconcile enrollment after onboarding or connection/folder changes. No automatic
grant discovery, scope expansion, deletion or resource provisioning occurs.

From an approved operator environment, inventory canonical metadata without
reading grants or contacting Drive. Inject the production read-only DB URL through
`TABULA_RASA_DATABASE_URL`; obtain its existing application database-identity
digest through the normal private operator inventory. This digest differs from
the NAS SQL runner's endpoint digest.

```sh
python -m scripts.nas_receipt_backup inventory --database-sha256 <approved-database-digest> --output /private/production-receipt-inventory.json
```

The output must be a new file in an existing private directory outside Git.
It contains business/org/profile/folder and connection references, required
connection scopes, canonical receipt counts/bytes and disconnected/unbound
businesses. It is private; console output is status only. It does not prove the
current provider inventory, actual grant scope, or decrypt-key availability.
Verify those privately, alongside NAS free capacity, Cloud Sync mappings, current
tasks, runtime/service boundaries and independent key custody. The existing
Drive adapter requires its supported `drive.file` scope and exact managed-folder
binding; do not request broad read scopes to work around an incompatible grant.

If an inventory fails with only a sanitized availability code, preserve its
output directory and obtain authorization for one bounded diagnostic inventory:

```sh
python -m scripts.diagnose_receipt_inventory --database-sha256 <approved-database-digest> --output /private/new-diagnostic-inventory.json
```

This reuses the same read-only inventory and private report format, adding one
read-only `SELECT 1` session check. Console output names only completed phases
and the failed boundary: private output configuration, database configuration,
source binding, read session, inventory query, serialization or report writing.
It does not expose credentials, provider error text or business data. Successful
execution produces the normal private report; an existing report is never
overwritten. It reads no Google grants or receipt bytes. Inspect the failed
boundary privately before choosing a fix; do not enable an automatic retry or
broaden permissions based on a generic availability result.

Prepare a protected JSON config outside Git with exactly these fields:

| Field | Contract |
| --- | --- |
| `environment` | Exactly `production`. |
| `database_sha256` | Approved application database-identity digest. |
| `entities` | Explicit list of objects containing exactly `organization_id`, `entity_id`, `profile_key`, `scope_key`; distinct entities and Drive folders, maximum 20. Use actual canonical IDs, including migrated text entity IDs. |
| `storage` | Existing absolute private NAS archive directory outside Git; no repository path. Only ciphertext and private metadata are persisted here. |
| `storage_marker` | 32 lowercase hex characters matching the private `.receipt-backup-marker` file in that directory. Create only after verifying the actual destination mount. |
| `recipients_file` | Absolute protected age public-recipient file. Decrypt identities remain in separate approved custody. |
| `approval_sha256` | Digest of the retained decision authorizing repeated capture of these bindings and changing canonical points within these limits. |
| `schedule` | Explicit `wednesday_sunday` (22:00 Eastern) or `twice_daily` (10:00/22:00 Eastern), receipt tasks only. No default selects an operating policy. |
| `minimum_free_bytes` | Positive persistent free-space reserve. Additional admission headroom is `max_capture_bytes` plus 64 MiB for metadata/tar overhead. |
| `temporary_directory` | Existing private absolute tmpfs directory, disjoint from archive storage. Disk-backed scratch and symlink aliases to disk are refused. Admit at least twice `max_capture_bytes` plus 64 MiB of free tmpfs space. |
| `max_capture_bytes`, `max_documents` | Positive combined canonical-byte/document limits per run, at most 1 TB and 100,000 documents. Set realistic small bounds for the DS220j after inventory. |
| `timeout_seconds`, `delay_seconds` | Positive integers: at most 7,200 seconds per network child/encryption command and 60 seconds between verified receipt downloads. Both dry-run and capture download bytes; budget for both. |

Run the reviewed package with application/API/PostgreSQL dependencies and age in
an approved Python 3.12 operator runtime. The existing SQL-only backup image has
no application dependencies and cannot perform receipt capture. Inject the exact
production connection-custody backend and key using the established connection
secret environment settings, preferably read-only credentials/mounts. One
database connection and one entity capture run at a time. Do not copy staging
grant envelopes, custody keys or database URLs into this runtime.

Build the application-capable image with the dedicated context allowlist. Reuse
the established receipt image recipe; the retired staging image on a NAS does
not contain the current production runner. Record the source commit, image ID
and transfer checksum, and pin the exact installed ID in DSM's wrapper:

```sh
docker build --platform linux/arm64 --build-arg RELEASE_ID=<reviewed-commit> -f deploy/nas/Dockerfile.receipts -t tabula-receipt-backup:<reviewed-commit> .
```

Before provider access, exercise `tests/tooling/nas_receipt_smoke.py` in this
image as UID 10001, with network disabled, a read-only root, all capabilities
dropped and a private 128 MiB `/work` tmpfs. Bind only that synthetic harness
at `/app/validation.py` and override the entrypoint with `python`. It imports
the capture dependencies, proves real tmpfs acceptance/disk rejection, encrypts
and decrypts synthetic bytes and checks safe missing-health behavior. This is
package validation, not actual production capture or scheduled-run evidence.

For the approved production wrapper, use a separate read-only protected config
mount, writable archive mount, private bounded tmpfs at `temporary_directory`,
no exposed ports, `--read-only --cap-drop ALL --security-opt no-new-privileges`,
a CPU limit and explicit memory/PID limits. For the 512 MiB DS220j, initially
budget at most 256 MiB for the entire receipt container (including its tmpfs),
0.25 CPU and 64 PIDs, then measure the actual inventory/workload before enabling
it. Begin with a small canonical-byte budget that fits twice within the tmpfs
after its 64 MiB overhead reserve. Never enlarge limits to evade a failed
capacity check without reviewing total host load. Keep status checks free of
database/custody credentials. Their configuration still needs a small private
scratch directory, but they never read plaintext or require a tmpfs capture.

Check the NAS kernel's resource-controller support before relying on Docker CPU
quotas. A host without the CFS quota controller rejects `--cpus` before container
startup; that outcome establishes no production inventory or capture. Do not
silently remove an approved limit. For a metadata-only inventory, an operator
may separately approve low process priority plus a finite process CPU-time
budget (`resource.RLIMIT_CPU`), retaining the memory/PID limits. Relative CPU
shares are a scheduling weight, not an equivalent hard rate quota. Receipt
capture needs its own measured resource decision and must not inherit a small
inventory-only CPU-time budget.

```sh
python -m scripts.nas_receipt_backup run --config /private/production-receipts.json
python -m scripts.nas_receipt_backup status --config /private/production-receipts.json
python -m scripts.nas_receipt_backup verify --config /private/production-receipts.json --generation <exact-generation>
```

After a controlled approved run, register `scheduled --config ...` with DSM at
an approved poll interval/window, serialized with SQL, custody and other heavy
NAS tasks. Register nothing merely because code is available. A source/config
lock prevents overlap for the same enrollment. Each latest Eastern slot is
attempted once; failure/interruption consumes it. There is no catch-up backlog
or automatic whole-run retry. Provider reads keep existing bounded retry rules.
After inspecting a failure, `run` permits one deliberate new-generation attempt.
Inspect an abandoned lock and confirm the process stopped before removing only
that lock. Different configurations/tasks still require host-level serialization.

Each entity uses its current canonical production connection and custody
reference. Existing exact-plan, tenant/profile/folder, inventory and byte checks
apply. Canonical/connection changes stop publication; select a quiet window or
explicitly approved writer-free capture. The runner never stops application
services or freezes writers. Each entity's complete canonical export is bundled
with its verified bytes as `receipts/<enrollment-index>/`; the `binding.json`
identifies the entity and folder. Enrollment order is retained by the private
config. Captures across entities are sequential, not a global atomic point.

The bundle is encrypted to `receipts.tar.age`, fsynced and checksummed/read back
before a complete generation and durable last-success pointer are published.
Only this invocation's transient tmpfs workspace is removed after encryption/readback.
Failed/incomplete ciphertext remains quarantined on the restricted destination.
Temporary plaintext remains only in tmpfs until inspected or the container ends; a failed run never publishes it to persistent storage. No source file,
grant, retained generation or failed artifact is automatically pruned. Capacity
requires ongoing review under indefinite retention. Ciphertext checksum readback
proves local integrity, not off-device replication, decryptability or restoration.

`status` is local and requires no SQL/Drive credentials or decrypt identity.
Reuse approved DSM abnormal-termination email for nonzero exits. After approving
a dedicated Better Stack check-in and business recipient, inject its URL as
`TABULA_RASA_RECEIPT_BACKUP_HEARTBEAT_URL` and poll `status --config ... --notify`.
The shared payload-free heartbeat refuses redirects and sends failure for
unhealthy results. Check-in delivery failure also exits nonzero. Place missing-run
evaluation outside the NAS so a powered-off host cannot silently appear protected;
existing application/recurring-worker check-ins do not establish receipt coverage.
Confirm actual failure and missed-check-in delivery, remediation and recipient
handoff using approved controlled cases before relying on the route. `verify`
checks historical ciphertext only and cannot send a healthy freshness check-in.
Missing, stale, failed, overdue, unmatched-lock, low-capacity and corrupt states
exit nonzero. A running capture also cannot send a healthy check-in. Freshness
uses the existing two-hour slot grace; twice-daily mode additionally becomes
stale at 20 hours since capture start. Changing any enrollment/policy/approval
or recipient bytes starts a new health namespace and cannot inherit success.

Use DSM abnormal-termination email for immediate failures. After approving a
dedicated existing-provider check-in and actual recipient, inject
`TABULA_RASA_RECEIPT_BACKUP_HEARTBEAT_URL` and poll `status --notify` on the
approved interval. This reuses the payload-free, redirect-refusing Better Stack
sender. Test failure, missed-run/NAS-offline and recovery recipient delivery;
an exit code or configured URL alone is not delivery evidence. The primary
operator inspects protected task/config/grant/capacity state, preserves partial
copies, repairs the cause and performs one controlled retry; the alternate
operator must have the same handoff and independent key access.

For an approved recovery rehearsal, verify the exact ciphertext generation,
decrypt with the separately held identity into an isolated encrypted directory,
and extract the tar with a safe path filter (`tarfile`'s `filter='data'`). Open
each `DocumentArchive` and verify every byte against its canonical size/hash,
then use the existing isolated Drive/PostgreSQL restore procedure below. Select
or import the matching canonical bookkeeping point per entity; an unrelated SQL
archive is not necessarily that point. Keep production callbacks and writers
blocked on recovery targets. The runner does not restore or mutate production.

Retain actual controlled-run, decrypt/byte-integrity, two-business isolation,
isolated restore and notification results privately and sanitized evidence in the
owning operational discussion. External beta and commercial acceptance remain
separate: Wednesday/Sunday captures cannot meet a 24-hour receipt RPO; choosing
twice-daily receipts alone does not improve the database RPO. Neither policy
establishes a full-service RTO, off-site survival or coordinated SQL/receipt point.
Measure duration, CPU/memory, growth and restore time against actual approved
production configuration before claiming achieved coverage.

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
