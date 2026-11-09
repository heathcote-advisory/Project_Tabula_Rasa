# NAS PostgreSQL backup operations

The standalone runner uses the selected database's direct TLS connection and
requires neither Windows, the application nor a paid provider scheduler.
The runner, image and synthetic drill are implementation artifacts. They do not
establish a deployed NAS task, protected storage, actual provider usage or an
accepted recovery drill. Complete the operational checklist below before
accepting this recovery route. The [managed recovery contract](DATABASE_RECOVERY.md)
remains authoritative for managed snapshots, PITR, recovery objectives and
return-to-service authorization. Keep deployment and drill results in restricted
operator records rather than adding them to this runbook.

## Host compatibility

This runbook targets **Synology DS220j with DSM and Container Manager**. Its Realtek RTD1296
is 64-bit ARM with 512 MB RAM ([hardware specification](https://global.download.synology.com/download/Document/Hardware/ProductSpec/DiskStation/20-year/DS220j/enu/Product_Spec_DS220j_enu.pdf)).
Synology's [Container Manager release notes](https://www.synology.com/en-us/releaseNote/ContainerManager?model=DS120j&os=DSM&version=7_x_series)
explicitly include ARMv8 DS220j/DS120j support. This establishes a documented
installation route, not execution proof on this device. Confirm Package Center
offers a supported Container Manager version for this exact model/DSM. Record
`uname -m`, package version, installed image digest, available memory/disk and
the commands' sanitized results privately. Do not install an unsupported package
or reuse DSM's internal PostgreSQL binaries. If unavailable or resource-limited,
record the concrete blocker and obtain an operator decision on an alternative.

The client-only image in `deploy/nas/Dockerfile` uses PostgreSQL 18.6, Python,
the distribution's `python3-psycopg` (using system libpq), age and timezone/CA data.
The pinned upstream manifest supports ARM64. The
runner also works with a separately maintained Python 3.9+ installation and
matching client tools and Psycopg 3, but installing a new native toolchain is not automatic.
Python compatibility here is separate from the application's Python 3.12 dev
environment. Do not install application dependencies on the NAS. Confirm all
three PostgreSQL clients match the actual server major version; the runner
refuses mismatches. Rebuild the reviewed image for a server-major change.

From a clean checkout, build on the NAS, or build ARM64 on an authorized build
host and import the image. No database server is started by this image's
entrypoint. Its dedicated Docker ignore file sends only the runner and private metadata helper to the
builder; never put credentials or backups in a build context.

```sh
docker build --platform linux/arm64 -f deploy/nas/Dockerfile -t tabula-nas-backup:reviewed .
```

Retain the built image ID/digest and use that immutable ID in the task command.
The apt packages receive current distribution security updates at build time;
retain/review the resulting image, rather than treating a tag as immutable.

## Protected configuration and inventory

Before enabling any recurring task, record the authorized primary/alternate
operator, exact production project/branch/direct endpoint and every intended
database, protected NAS destination, encrypted-share mount procedure and
recovery-key custodians in private operator records. Each invocation captures
**one database**. An archive containing multiple entities is a restricted
operator artifact, never an ordinary tenant/profile download.

1. Create a dedicated encrypted DSM shared folder with sufficient capacity,
   restricted to the backup OS account and authorized recovery administrators.
   Confirm effective DSM ACLs and encryption, and test remount after reboot.
   Keep its recovery material off the NAS. In that mounted share create an
   owner-only storage directory (`0700`) and a `.tabula-nas-storage` file
   containing a newly generated 32-character hex ID. Copy that ID into the
   private config. The runner requires an existing directory and matching
   marker; it never creates a fallback destination when a share is absent.
   This guard cannot prove encryption or detect a copied marker. The operator
   must verify the actual mount/ACL/encryption before enablement.
2. Create a separate private configuration directory outside the source tree
   for **each environment/database**. Copy `backup.example.json` and
   `pg_service.example.conf` from `deploy/nas/`. Replace all placeholders
   privately. All config, service, password and recipient files must be owned
   by the task UID and mode `0600`; the storage directory must be owned by that
   UID and mode `0700`. The runner rejects group/world permissions on POSIX.
   Inspect parent-directory access and DSM ACLs separately; mode bits alone
   do not attest those controls. Do not embed passwords in service files.
3. The single `[backup]` service selects the **direct/unpooled** endpoint,
   database and backup role, `sslmode=verify-full`, a trusted CA bundle,
   `channel_binding=require` and `connect_timeout=15`. Use the system CA bundle
   in the image. A non-Neon pooler alias still requires operator detection;
   the runner rejects Neon's `-pooler` hostname form. Inventory effective
   grants; the role must read all intended tables, sequences and schemas,
   including data subject to RLS, and must not silently dump a tenant subset.
   `pg_dump` retains its normal row-security protection and errors if full
   coverage is unavailable. Never enable row-security filtering for this job.
4. Store the password in the protected `pgpass` file using libpq's exact
   `host:port:database:user:password` format (escape `:` and `\` in values).
   Keep a distinct file/configuration for UAT. There is no application-secret,
   environment-variable password, `.env.local` or default-service fallback.
   The scheduled command contains paths only. Never print/copy files or
   connection strings into logs, GitHub, screenshots or error reports.
5. `source_sha256` is SHA-256 of UTF-8
   `lowercase-host:decimal-port/database`, excluding credentials. Compute it
   privately from the selected service values. This NAS namespace fingerprint
   has its own encoding; it is **not** the application recovery verifier's endpoint fingerprint.
   Check Neon branch lineage independently. Never substitute the application
   verifier's hash for this value. Production and UAT use separate configs,
   secrets, and `environment/source_sha256` archive/status/lock namespaces.
6. Generate an age identity on the authorized recovery operator's protected
   system. Place only the public recipient in `recipients.txt` on the NAS.
   Keep the private identity in approved off-NAS custody accessible to the
   alternate operator, and prove decryption before scheduling. The backup task
   needs no decryption key. Record the recipient-file digest and custody/access
   evidence privately. Changing recipients affects future generations only;
   keep every identity required by retained generations. Each capture encrypts
   with a private per-run copy of the public recipient bytes and records the
   digest of those same bytes, so rotating the configured file during encryption
   cannot mislabel the generation's key custody. A failed run may retain this
   public-only copy with its other quarantined evidence.

The source role/ownership/grants, required extensions and provider-managed
roles need an independent recovery inventory. `pg_dump` captures database-local
objects and ACLs but does not recreate cluster roles or provider grants.
Retain a private reviewed role/grant recreation procedure without password
material. Do not publish `pg_dumpall --globals-only` output or automate replay
of provider roles. Inventory future identity/job/audit/control-plane objects too.

## NAS command and schedule

Use a fixed reviewed wrapper outside the source tree. Example for a configured
production database (replace UID/GID, host paths and image ID privately):

```sh
#!/bin/sh
exec docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user 1026:100 \
  --mount type=bind,source=/volume1/PrivateBackupConfig/production,target=/secrets,readonly \
  --mount type=bind,source=/volume1/EncryptedRecovery/postgres,target=/backup \
  sha256:REPLACE_WITH_REVIEWED_IMAGE_ID "$@" --config /secrets/backup.json
```

Use `--mount`, which fails for a missing host source, rather than `-v`'s
automatic directory creation. Ensure the Docker execution account is authorized;
daemon access is privileged even though the process uses a restricted UID. Do
not grant ordinary application users access. The entire encrypted share must
protect scratch data: plaintext exists under `incomplete/` during the dump and
may remain there after failure/interruption. Never place that share on Cloud
Sync or expose it through application static/download routes.

Run `wrapper preflight` on the NAS. It checks real TLS, schema revision, client
major versions, age availability and reports free bytes, without publishing
connection details. It performs read-only source queries, not a dump or restore.
Then authorize and execute a controlled `wrapper run` and isolated restore.

### Client TLS and Neon proxy troubleshooting

Neon may terminate TLS at its proxy: a verified client connection can coexist
with `ssl = false` in backend `pg_stat_ssl`. That backend view describes the
proxy-to-PostgreSQL link and cannot validate the NAS-to-proxy TLS session.
The runner uses a private Psycopg subprocess with the same sanitized libpq
service/password environment as the dump. It reads `connection.pgconn.ssl_in_use`
(libpq `PQsslInUse()`) on the **same connection** that reads server/schema
metadata in a read-only transaction. It refuses a non-TLS connection. Both
configuration validation and the metadata connection require
`sslmode=verify-full` and `channel_binding=require`; certificate/hostname and
authentication failures remain fatal. The dump continues using those required
service settings. No production credentials or endpoint are printed.

Successful preflight exits zero and emits JSON with this shape (version,
schema and free-space values depend on the selected database and NAS):

```json
{"free_bytes": 123456789, "schema_version": "0005_bookkeeping_cutover", "server_version_num": 180006, "status": "compatible", "tls": true, "tools": {"pg_dump": "18.6", "pg_restore": "18.6", "psql": "18.6"}}
```

`compatible` means client TLS, required schema-revision syntax, all three
client/server majors, age availability and minimum free space passed. It does
not establish archive or restore acceptance. The schema need not equal the
example: compare it privately to the intended source release.

If `code` is `source`, privately check the selected direct endpoint, CA bundle,
hostname, role/password, channel-binding support and `public.alembic_version`.
Do not weaken TLS/channel binding or substitute backend `pg_stat_ssl` for the
client check. Missing/inaccessible Alembic metadata or malformed/empty revisions
fail. For `client`, check all three client versions against the server major.
For a newly imported image, verify its immutable ID and helper hashes below;
reusing an old wrapper image ID still runs the old check. Missing Psycopg or
helper files also fail closed as `source`; rebuild the complete image. Provider
diagnostics stay private; the runner intentionally returns only fixed guidance.

### Build, import and update the ARM64 image

Use a development machine or CI runner with Docker Buildx and ARM64 execution
support (native or emulated), avoiding a build on the 512 MB NAS. From the
reviewed release checkout, these PowerShell commands produce a single-platform
Docker archive; record the commit, image ID and both hashes privately:

```powershell
git rev-parse HEAD
docker buildx build --platform linux/arm64 --load -f deploy/nas/Dockerfile -t tabula-nas-backup:reviewed .
if ($LASTEXITCODE -ne 0) { throw 'Image build failed' }
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' tabula-nas-backup:reviewed
Get-FileHash scripts/nas_postgres_backup.py,scripts/nas_postgres_source.py -Algorithm SHA256
docker image save --output tabula-nas-backup-arm64.tar tabula-nas-backup:reviewed
if ($LASTEXITCODE -ne 0) { throw 'Image export failed' }
Get-FileHash tabula-nas-backup-arm64.tar -Algorithm SHA256
```

The inspect result must say `linux/arm64`. Package security updates are resolved
at build time, so subsequent rebuilds can produce different IDs. Transfer this
exact archive to an approved directory on a NAS data volume using the authorized
file transfer route; keep it outside the source tree and backup-generation
namespace. Replace the example volume/share with the actual approved location;
do not store the image archive on DSM's system partition.
Compare `sha256sum /volume1/approved-image-import/tabula-nas-backup-arm64.tar`
with the recorded archive hash. In Synology **Container Manager → Image → Add →
Import**, select that archive and wait for import to finish. Do not create a
database container or alter scheduled tasks.

In the authorized NAS shell, retrieve the imported immutable image ID, verify
the architecture and check the shipped scripts against the recorded hashes:

```sh
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' tabula-nas-backup:reviewed
IMAGE_ID=$(docker image inspect --format '{{.Id}}' tabula-nas-backup:reviewed) || exit 1
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user REPLACE_UID:REPLACE_GID --entrypoint sha256sum "$IMAGE_ID" \
  /opt/tabula/nas_postgres_backup.py /opt/tabula/nas_postgres_source.py
docker run --rm --read-only --user REPLACE_UID:REPLACE_GID --entrypoint python3 "$IMAGE_ID" \
  -c 'import psycopg; from psycopg import pq; print("psycopg", psycopg.__version__, "libpq", pq.version())'
```

The image ID must match the build-host ID, both script hashes must match the
reviewed checkout, and libpq must support PostgreSQL 18. These inspection
containers have no credentials or archive mounts.

Replace the inspection UID/GID placeholders with the configured backup account.
For each environment authorized for the update, preserve an owner-only copy of
its wrapper and replace only its existing image reference with the verified
`IMAGE_ID`. Preserve its UID/GID, hardened flags and environment-specific config
and archive mounts, including the container targets used by `backup.json`.
Check the image reference privately, then run that environment's wrapper:

```sh
wrapper preflight
```

Require exit zero, `status: compatible`, `tls: true`, the intended schema
revision and matching major-18 tool/server values. This is a read-only source
check, not a backup. Complete a controlled backup and retained-archive restore
when accepting a new image; scheduling, retention and failure drills remain
separate operational checks.

Configure **DSM Task Scheduler** with a known `America/New_York` local timezone
and working NTP; verify the displayed timezone and next-run dates. Do not
silently change a shared NAS timezone. Register the production `wrapper scheduled`
task for **Sunday and Wednesday at 22:00 America/New_York**, following DST.
On the 512 MB DS220j, stagger UAT's
scheduled capture to **22:30 Eastern** to avoid concurrent backup jobs; the
runner's due-slot calculation for both environments remains 22:00 Eastern.
If DSM cannot express both weekdays, use separate day-specific tasks. Register
separate `wrapper scheduled` catch-up and `wrapper status` health-check jobs
for each environment. The current operator-selected cadence is **every 3 hours
for production** and **every 6 hours for UAT** to limit container-launch
pressure on the DS220j; validate their actual execution history.

Give the recurring tasks explicit, distinct local-time phases. For example:

| Environment | Catch-up (`wrapper scheduled`, daily) | Health (`wrapper status`, daily) |
| --- | --- | --- |
| Production (3-hour) | 00:05, 03:05, 06:05, 09:05, 12:05, 15:05, 18:05, 21:05 | 00:15, 03:15, 06:15, 09:15, 12:15, 15:15, 18:15, 21:15 |
| UAT (6-hour) | 02:35, 08:35, 14:35, 20:35 | 02:45, 08:45, 14:45, 20:45 |

These are registration examples, not evidence of deployed minute offsets.
Do not place a UAT catch-up tick between 22:00 and its 22:30 scheduled capture:
it would consume the same due slot early and defeat the intended stagger.
Inspect DSM next-run dates and execution durations for all tasks together,
including across DST; distinct start times reduce launch contention but do not
guarantee nonoverlap if a job runs long. The runner's lock is per
environment/database, not a shared NAS lock. If observed durations overlap,
review phases and capacity with the operator before accepting the schedule.

These are slower-than-hourly checks, so detection/notification can be delayed by up to
roughly the check interval plus execution/runtime latency. Neither cadence
changes the runner's 24-hour catch-up limit nor retries an already attempted
failed slot. Configure nonzero-exit notification to the authorized operator.
Healthy and in-progress-within-grace results exit zero. A `no_attempt` result
exits zero only when its `health` is healthy or running; failed, missing, stale,
capacity and overdue health still exit nonzero. Configure notification on abnormal
termination, not on every successful tick. In DSM Control Panel → Notification →
Email, configure the separate **Sender email** service and authorized recipient
profiles; Synology Account's successful test email alone does not establish
Task Scheduler delivery. Test a synthetic scheduled successful task with success
notification enabled and an `exit 1` task with abnormal-only notification,
verify actual emailed results, and remove the test
task. Do not test delivery by deliberately breaking real backup jobs. The archive task alone
cannot detect its own missing registration or a powered-off NAS; use an
independent external availability/check-in monitor or a recorded operator
health-check responsibility for that failure boundary. Coordinate future
central alert integration with the [observability guide](OBSERVABILITY.md);
application readiness does not replace backup monitoring.

The runner computes Sunday/Wednesday 22:00 in `America/New_York` from timezone
data, independently of container local time. UTC capture times are recorded in
manifests. It attempts the most recent due slot once, including a missed slot
up to **24 hours** late. It never replays a backlog. The attempt and its first-run
namespace parents are persisted before any remote call; failure/interruption consumes that slot. Further
attempts require `run` by an operator after diagnosis or the next due slot.
The local `.lock` directory excludes overlapping invocations of the same
environment/database. It has no automatic expiry. A kill/power loss may leave
it behind: confirm the old process/container has stopped, record the failure,
then remove **only that exact empty `.lock` directory** before one manual run.
Never delete a retained generation or incomplete directory to clear a lock.

### Opt-in staging recovery cadence

In the protected configuration for an approved staging source, set
`"environment": "uat"` and `"schedule": "twice_daily"`. Slots are 10:00 and
22:00 America/New_York, with 11–13 elapsed hours across DST. The existing
`scheduled` command attempts only the latest slot, once. An interrupted or
failed attempt consumes it. An unattempted slot can be caught until the next
slot supersedes it. Keep the two-hour slot grace and use `status` for a separate
20-hour capture-age alert, including while another attempt is running. Omit
`schedule` to use the Wednesday/Sunday policy. Production accepts only that
default policy.

Use distinct protected configs, direct source fingerprints, and read-only roles
for the staging ledger and dedicated custody databases. In the custody config
also set `"source_kind": "connection_custody"`. Its TLS preflight checks the
dedicated grants table and refuses a ledger with an Alembic version table. The
dump includes only the custody schema and its manifest records the source kind.
Restore into a dedicated isolated database, preserving exact ciphertext and
references. Keep the Fernet key separately recoverable.

Before enabling this cadence, approve exact DSM windows and an independent
monitor outside the NAS. Serialize both staging captures and reserve windows
away from production tasks. Measure elapsed time, peak memory, database
connections, and storage growth with a single heavy PostgreSQL client at a time.
Budget for full and incomplete generations on the 512 MB host. A per-source
lock cannot serialize distinct databases. Use the
[coordinated recovery procedure](DATABASE_RECOVERY.md#coordinated-staging-recovery-point)
for composite generation status and rehearsal.


### Coordinated point publication and health

`python -m scripts.recovery_point_health` adds **offline, operator-attested**
complete-point health to the component runners. It does not capture data,
suspend services, restore, install tasks or contact SQL/Drive. Keep the approved
Wednesday/Sunday cadence unless a separate operator decision changes it.
Functional readiness is not an operating schedule or a 24-hour RPO guarantee.

The authorized operator first follows the
[coordinated capture procedure](DATABASE_RECOVERY.md#coordinated-staging-recovery-point),
including writer quiescence, reference verification and durable encrypted
readback. Component timestamps/checksums cannot independently prove that a writer
was stopped, that references were checked at the chosen point, or that encrypted
receipt bytes correspond to a plaintext archive. Publication explicitly records
`operator_attested_quiescence`: those facts remain an operator attestation.
SQL ciphertext/manifests and every canonical receipt byte are machine-verified.
An age header establishes a file type, not encryption provenance or key custody.

Prepare a protected JSON configuration, outside Git and readable only by its
operator, with these fields:

| Field | Contract |
| --- | --- |
| `ledger_config`, `custody_config` | Absolute paths to existing NAS runner configs. Distinct UAT sources; custody requires `source_kind: connection_custody`. Existing TLS/source/storage checks apply. |
| `state_directory` | Existing private directory outside Git, used only for this selected enrollment's points and status. |
| `entities` | Nonempty list of `{"entity_id": "<canonical UUID>", "scope_key": "<Drive receipt folder>"}`; distinct business IDs and folders. No implicit enrollment or fallback grant. |
| `schedule` | Optional `wednesday_sunday` default or explicit future `twice_daily`. Both component configs must use the same policy. Merely choosing this value installs no schedule. |

Use NAS source fingerprints from the protected NAS configs, not application
database-identity digests: the encodings differ. Publication pins the source
identities, enrollment and cadence; changes require a newly verified point.

The private point specification has exactly these fields:

- `quiesced_from`, `quiesced_until`: timezone-aware ISO instants of the
  attested writer-free window; use its start as the conservative point time.
  Both SQL generations must have started and completed inside it.
- `approval_sha256`: digest of the retained operator decision.
- `references_sha256`, `verified_references`: the attested unchanged successful
  reference-check digest/count. Supplied values are not independent proof that
  the check ran; retain the original private execution evidence.
- `ledger_generation`, `custody_generation`: exact existing completed NAS
  generation IDs, never “latest”.
- `receipts`: exactly one `{"entity_id": "...", "archive": "<private canonical
  archive directory>", "ciphertext": "<durable age file>"}` per enrolled business.
  Publication opens each `DocumentArchive`, checks the selected folder and
  every retained byte's size/hash, then pins the durable ciphertext size/hash.
  One encrypted bundle may contain multiple enrolled archives; the operator
  explicitly attests that correspondence after verified durable readback.

With the private files prepared, publish from an app-capable operator
environment with the reviewed application package and canonical receipt
archives available:

```sh
python -m scripts.recovery_point_health publish --config /private/coordinated.json --point /private/point.json --attest-quiesced-point
```

Publication uses the existing local lock and atomic JSON publication. A failed
component verification records a failed attempt and preserves the previous
complete point and all partial artifacts. Setup/argument failures and concurrent
lock rejection stop before starting an attempt. An abandoned lock/running state
requires operator inspection; there is no automatic lock removal or retry.
The new tool never relabels an existing one-time archive as recurring protection.

For NAS inspection, use the existing reviewed client-only image. Stage
`recovery_point_health.py`, `nas_postgres_backup.py` and
`operational_heartbeat.py` from the same reviewed revision in a dedicated host
directory. Bind-mount it read-only at `/opt/coordinated/scripts` as the Python
`scripts` package. Set the container working directory to `/opt/coordinated`
and override the entrypoint with `python3` for this invocation. Mount the
protected configuration, coordinated state and retained artifacts read-only
at the paths referenced by the configuration and point.

```sh
python3 -m scripts.recovery_point_health status --config /private/coordinated.json
```

Status checks the pinned SQL generations and receipt ciphertexts, component
failure/capacity state, exact enrollment and point chronology. Later individual
component successes cannot silently advance the coordinated point. A running
publication with its lock may keep reporting a verified, fresh prior point
within the existing two-hour grace. A missing or stale point, failed or overdue
attempt, unmatched lock/running marker or integrity failure is still unhealthy.
Status reuses the NAS slot/grace rules: a Sunday point is not stale merely because Monday has
arrived. Only explicit twice-daily mode also applies the 20-hour point-age
threshold. Status needs no plaintext receipt directory, grant, decrypt identity
or application credentials after publication. It reports sanitized status/codes;
the private point files contain the component paths and digests.

Only after approving an independent Better Stack check-in/recipient and poll
schedule, inject its dedicated URL as `TABULA_RASA_BACKUP_HEARTBEAT_URL` and run:

```sh
python -m scripts.recovery_point_health status --config /private/coordinated.json --notify
```

Healthy point inspection sends the payload-free check-in; unhealthy/unconfirmed
inspection sends failure. A failed signal exits nonzero. Configure the external
missing-check-in deadline to detect NAS/reporter loss; reporter liveness alone
must not imply backup success. Retain actual failure/absence delivery evidence
before claiming operating monitoring. Notification is off by default; no live
check-in is part of installation or local tests.

## Archive and health contract

The runner executes `pg_dump -Fc`, traverses/decompresses the entire archive
with `pg_restore --file /dev/null`, encrypts it with age, hashes the ciphertext,
flushes the files and renames the generation from `incomplete/` to `complete/`.
On the POSIX NAS host it syncs the manifest, publication and namespace directory
entries before persisting the last-success pointer, and syncs status replacement
before returning success. A directory-sync failure remains an actionable failed
run, even if the complete archive is already present. Filesystem/device durability
still depends on the NAS storage honoring sync requests; this is not a substitute
for the actual device power/recovery and protected-storage checks.
Only a complete directory containing `database.dump.age` and `manifest.json`
belongs to the usable set. The manifest records UTC capture-start/completion,
environment, source fingerprint, server/schema/client versions, TLS preflight,
recipient digest, archive size/checksum and completion status. Dump parsing
and checksum checks do not prove successful SQL restore or complete business
coverage; the drill below does. Do not deploy schema changes during a capture;
the revision observation precedes the dump snapshot. Capture start is a
conservative backup-age bound, not an exact database commit timestamp.

`wrapper status` checks the last successful generation's ciphertext checksum,
capacity and current slot. A previous generation becomes stale two hours after
the latest due slot, allowing execution time. It reports capture-based age in
seconds and last-success completion; the 2-hour grace is **not** an RPO promise
or a whole-job timeout. Each long client operation has a 2-hour limit. `running`
becomes `overdue` after two hours, which may indicate interruption. A failed attempt stays
visible while the previous verified generation remains available. A failure
after publication but before status update leaves a complete recoverable
directory with conservative running/failed health; inspect it with
`verify --generation GENERATION_NAME` before recording private evidence or
requesting another run. Do not manually report success from a partial directory.

[Customer lifecycle](CUSTOMER_LIFECYCLE.md) proposes 90 days from creation for
routine generations, subject to legal, recovery and tooling review. That proposal
does not change the effective indefinite policy or authorize pruning. Holds,
frozen-source obligations and required recovery evidence need explicit treatment.

Each successful generation is retained **indefinitely**. There is no expiry,
pruning, automatic rotation or deletion of retained artifacts. Only the current
run's transient plaintext is removed after encryption succeeds. Incomplete
artifacts are quarantined and need separately approved exact-target cleanup.
Set `minimum_free_bytes` to an operator-approved reserve sufficient for an
uncompressed working dump, encrypted output and growth. The example 1 GiB is
only an initial placeholder, not capacity certification; mid-run exhaustion
still fails safely. Inspect `status` free bytes and DSM storage trends and set
an actionable capacity notification. Unlimited retention does not mean unlimited
capacity. Preserve recovery records and verified copies before considering any
explicitly approved future retention-policy change.

Inspect the selected project's compute, storage and public-transfer allowances
in its current provider console/plan before enabling recurrence; do not hardcode
Free-plan limits. Production and UAT branches in one project share its budget.
Record the dashboard scope, billing period, units and reporting delay. If only
an organization aggregate is available, label it as an aggregate: it can bound
an included project's contribution for the same metric and period, but cannot
attribute consumption to a project or backup. Branch views need reconciliation
before treating them as a complete project total.

Where available, take mature before/after usage readings around a representative
backup, allowing for provider reporting delay and noting other activity. Record
duration, compute size, archive bytes, cold-start/idle overhead, production/UAT
demand, manual/drill runs and NAS free space. Unchanged readings immediately
after a run do not establish zero usage. Archive size is not network transfer;
dump compression happens client side. Budget up to ten scheduled captures per
calendar month **per selected database**, plus controlled/manual runs and restore
drills. Record any attribution or headroom uncertainty, and stop enablement if
available capacity is inadequate.

Measure backup age using the verified manifest's capture start at a recorded UTC
observation time; `last_success` is completion time, not the recovery point.
Sunday/Wednesday due slots have nominal 72-hour and 96-hour spacing in ordinary
weeks, with a one-hour DST variation when the interval crosses a clock change.
Failed/missed captures increase the gap. A 24-hour catch-up window and frequent
health polling do not make this a daily backup or a 24-hour RPO.

## Failed or interrupted backup recovery

1. Identify the exact environment/database namespace and use its own wrapper to
   run `status`. Record the current `status`, last verified success and capacity.
   Treat an old `code` field in `status.json` as diagnostic history when its
   `result` describes a later running attempt; do not infer the current state
   from that field alone. Keep private state and source details out of logs.
2. Confirm whether the task/container is still active. `running` within the
   two-hour grace is not a completed backup; `overdue` requires investigation.
   A stop may remove `.lock` or leave it behind. Absence of a lock does not
   establish success. If it remains, confirm the old process stopped before
   removing only the exact empty lock directory as described above.
3. Verify the prior complete generation with `verify --generation GENERATION_NAME`.
   Keep `incomplete/` output quarantined: it may contain a plaintext dump and
   must remain inside the protected encrypted share. Do not publish it, use it
   as a completed backup or delete it to clear a failure.
4. Diagnose storage reserve, mount/permissions, client/source compatibility or
   task interruption without weakening TLS or changing the live source. A
   failed/interrupted attempt already consumed its slot; another `scheduled`
   tick does not retry it. After correcting the cause and obtaining target
   authorization, perform one explicit `wrapper run` in that namespace.
5. Require `complete`, then check `status` is healthy and verify the new
   generation. Retain the old successful generation and quarantined partials;
   cleanup requires separate exact-target approval. If recovery fails, preserve
   the failure state and stop for diagnosis rather than repeatedly rerunning.

## Controlled failure and catch-up drills

Use a disposable protected configuration and archive namespace with a new
storage marker and its own environment/source status and lock path. Verify that
neither live wrapper/task nor live archive namespace is selected. Use an
authorized read-only source connection or synthetic database; even read-only
dumps consume provider/NAS resources. Do not deliberately break production/UAT
tasks or alter the NAS clock.

- Establish a complete baseline generation, record `status`, and verify its
  ciphertext checksum. For a capacity failure, set `minimum_free_bytes` above
  available space **only in the disposable config**; do not fill the disk.
  Require a nonzero storage failure and preserved prior verified generation.
  Another `scheduled` tick for that attempted slot must be `no_attempt` and
  still report unhealthy health, not a fresh dump.
- For interruption, use the disposable fixture below to pause a client stage and
  stop only the named drill container after confirming an attempt began.
  Verify it stopped, inspect whether a lock remains, and retain unpublished
  `incomplete/` output and the previous verified complete generation. Do not
  assume every stop leaves a stale lock. Inspect running/overdue status and
  perform the manual recovery procedure above after removing the test fixture.
- To test overdue classification without waiting, evaluate the runner's health
  function with a time beyond `started_at + 2 hours` against a read-only mounted
  drill namespace. Label this as simulated classification; it does not test
  actual elapsed-time scheduling, CLI exit status or emailed delivery. Verify
  those boundaries separately using the scheduler notification checks.
- For missed-slot catch-up, use a **fresh** disposable namespace with no prior
  attempt, invoke `scheduled` after a due slot but within 24 hours, and require
  a complete generation and healthy status. A second invocation for the same
  slot must be `no_attempt` with healthy health. This simulates eligibility and
  duplicate prevention; it does not prove a live DSM task was missed or that
  NAS-offline detection works.

Keep baseline, failed/interrupted and recovered artifacts until separately
authorized cleanup. Record each drill's tested boundary and limitations in
restricted operator records. Test an external missing-NAS check-in independently;
a task running on the NAS cannot report its own power or network outage.

### Disposable interruption fixture

Use only the isolated drill config/storage prepared above, with a verified
baseline complete generation. This fixture overrides `age` in one named
container; it does not change the reviewed image or live wrappers. Replace all
example paths and uppercase values with the approved drill targets. As the
backup OS account, create an owner-only tools directory on the protected data
volume and write this executable `age` shim there (LF line endings, mode `0700`):

```sh
#!/bin/sh
if [ "$1" = "--version" ]; then
  exec /usr/bin/age "$@"
fi
touch /backup/.drill-age-paused || exit 1
sleep 600
exit 1
```

The marker appears only after the runner has dumped and traversed the archive
and entered encryption. The shim creates no encrypted output and fails after
ten minutes if the operator does not interrupt it. Use a fresh marker path;
if one already exists, stop and inspect the previous drill rather than deleting
it or treating it as evidence of a new attempt.

Set `DRILL_CONFIG` to the isolated host config directory, `DRILL_STORAGE` to
its protected host storage root (configured as `/backup` inside the container),
`DRILL_TOOLS` to the shim directory, `DRILL_UID`/`DRILL_GID` to the backup account,
and `IMAGE_ID` to the reviewed immutable image ID. Verify these are the drill
paths, not live production/UAT paths. Then launch just this container:

```sh
test ! -e "$DRILL_STORAGE/.drill-age-paused" || exit 1
docker run -d --name tabula-backup-interruption-drill \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$DRILL_UID:$DRILL_GID" \
  --env PATH=/drill-tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  --mount type=bind,source="$DRILL_CONFIG",target=/secrets,readonly \
  --mount type=bind,source="$DRILL_STORAGE",target=/backup \
  --mount type=bind,source="$DRILL_TOOLS",target=/drill-tools,readonly \
  "$IMAGE_ID" run --config /secrets/backup.json
```

Confirm that exact container is running and the marker exists on the host
before the ten-minute pause expires. If either check fails, inspect only this
drill; do not launch replacements or stop other containers. Once both checks
pass, the authorized interruption is:

```sh
docker kill --signal KILL tabula-backup-interruption-drill
docker inspect --format '{{.State.Running}}' tabula-backup-interruption-drill
```

Require `false` from inspection before lock recovery. Use the ordinary isolated
wrapper, without the shim mount or PATH override, to inspect status and recover
as described above. Preserve the stopped container, marker and partial/complete
artifacts for inspection until exact-target cleanup is authorized. A reused
container name deliberately fails instead of replacing an earlier drill.

## Isolated retained-archive restore drill

1. Record authorization, incident/drill declaration time, exact new isolated
   database target, image/application release and source lineage. Never restore
   onto production or UAT. Inventory all expected entities/databases and objects.
   Pause source writes for the representative controlled capture and retain
   independent private canonical exports for **every** expected bookkeeping
   entity, including two entities with overlapping local IDs when available.
   Resume writers only after the controlled capture finishes.
2. Select a retained complete generation. Run the runner's `verify` action with
   `--generation GENERATION_NAME` to check source/environment, size and SHA-256.
   This verifies ciphertext integrity only. In a restricted recovery workspace,
   decrypt with the off-NAS key and validate the archive before restoring:

   ```sh
   age --decrypt --identity /protected/identity --output /protected/restore.dump /protected/database.dump.age
   pg_restore --file /dev/null /protected/restore.dump
   ```

3. Provision only the approved **new empty isolated database**, compatible
   server/extensions and reviewed roles. Inject its own explicit libpq
   service/password file using protected operator storage. Independently verify
   the destination's project/branch/database and TLS before `pg_restore`.
   Do not reuse the source backup service or default connection. Run:

   ```sh
   pg_restore --no-password --single-transaction --exit-on-error --dbname 'service=isolated_recovery' /protected/restore.dump
   ```

   Use explicit `PGSERVICEFILE`/`PGPASSFILE` paths for this recovery process.
   There is deliberately no automated live restore command. Do not use
   `--clean`, `--create` or source overwrite to force success. If the target's
   role/provider boundaries prevent ownership or privilege replay, use the
   explicit staged procedure below; suppression alone is not security recovery.
   Investigate failures and retain the isolated target.
4. Compare the complete database/schema/security inventory: schemas, tables,
   sequences, all entities, constraints, functions/triggers, extensions,
   ownership, effective grants, authority, identity/job/audit/control records
   and release/Alembic revision. Restore uses the historical capture's compatible
   release; do not stamp/upgrade the target before verifying it.
5. Use `scripts/verify_postgres_recovery.py inspect` and `verify`
   with the isolated target's explicit `TABULA_RASA_RECOVERY_DATABASE_URL` and
   independently retained source exports. Follow its distinct source/target
   fingerprint contract. Run it for every expected bookkeeping entity. Verify
   overlapping local IDs stay isolated, relationships/content are complete,
   transfers/mileage retain their semantics, and balance/accounting integrity
   holds. Separately verify objects outside its `entity_bookkeeping` scope.
   Post-capture exports are corroboration, not an independent at-capture oracle.
   Investigate each mismatch against capture timing and authorized changes;
   do not dismiss differences merely because receipts or sessions can change.
6. Record elapsed declaration-to-verified recovery time including operator/key
   access, target creation, decrypt/restore and all validation; source capture
   time, actual backup age/point gap, included/excluded controlled commits and
   limitations. Retain private reports, source/target inventory and the tested
   archive in restricted operator records. SQL execution duration alone is not
   declaration-to-verified database RTO. Have the alternate operator repeat the
   documented procedure with their approved access and key custody.

### Staged ownership and privilege recovery

Use this only as an explicitly reviewed isolated-target procedure when ordinary
restore cannot recreate source ownership or grants. Never escalate, impersonate
or recreate managed-provider roles to force replay to succeed.

1. Inventory source object owners, application roles, object ACLs, default
   privileges and provider-managed roles before restoring. If an initial
   data/schema restore uses `--no-owner --no-privileges`, record those exclusions
   and run it with `--single-transaction --exit-on-error` against the same
   independently verified new empty target. Exit zero proves only the selected
   restore scope; ownership and privilege parity remain unverified.
2. Compare capture-time source and target schema-only inventories from matching
   client versions. A current source dump is comparable only after accounting
   for post-capture schema/security changes; retain the archive's own schema
   output as the captured reference.
   First use `--no-owner --no-privileges` to inspect structural parity, then
   compare full schema-only output including ownership and grants. Normalize
   only non-semantic dump comments, random psql restriction tokens and blank
   formatting; never filter SQL, owner, ACL, RLS or default-privilege differences
   out of the security comparison. Preserve the restriction commands when
   executing SQL; normalization applies only to comparison copies. Retain
   differences privately.
3. Inspect the retained archive's `pg_restore --list` output. Make an explicit
   reviewed selection of object `ACL` entries and a separate selection of
   `DEFAULT ACL` entries by owning role. Presence in the archive proves
   recoverability material exists, not that it can be applied. Do not select by
   expected counts or loosely matching role names alone.
4. Replace uppercase placeholders with reviewed private paths. Preview each
   selected list offline with `pg_restore --use-list LIST_FILE
   --file PRIVATE_PREVIEW_SQL /protected/restore.dump`. Review every statement,
   recipient role and affected object. Keep provider-managed defaults separate
   from application/owner-controlled defaults, and reject unexpected changes.
5. Reconfirm target identity, then apply only the approved list using
   `pg_restore --no-password --use-list LIST_FILE --single-transaction
   --exit-on-error --no-owner --dbname 'service=isolated_recovery'
   /protected/restore.dump`. A failed transactional replay is not partial
   success; inspect target state before continuing. Do not fall back to replay
   without transactional/error-stop safeguards.
6. Repeat grant-aware schema comparison after each approved group. Reconcile
   provider-managed defaults (for example Neon `cloud_admin` defaults) against
   the target provider's supported setup; do not try to replay them as an
   ordinary recovery role. Record any remaining difference, impact and required
   operator/provider decision. Separately reconcile ownership where suppressed.
7. Verify effective access using the intended runtime/recovery roles, including
   denied cross-business access and privileges on future objects governed by
   default grants. Matching SQL inventories do not establish effective-role,
   identity or authority parity. Do not reopen service until the managed
   recovery contract's security and return-to-service checks pass.

Sunday/Wednesday backups are approximately three/four days apart, longer after
failures; they do not meet the proposed 24-hour fallback or 5-minute PITR RPO.
They are logical database recovery points, not Neon managed snapshots or PITR.
Receipt/document bytes remain the separate DocumentStore recovery responsibility
described in [document storage and recovery](../storage/RECEIPT_RECOVERY.md).
Filesystem versioning protects only already captured
files. Neither this tooling nor a successful database drill supplies commercial
readiness, a live authority switch or full-service/document recovery.

## Selected-NAS verification and acceptance ownership

Keep observed passes, failures and outstanding setup work in the
[NAS verification record](https://github.com/Roklion/Project_Tabula_Rasa/issues/294). Use this runbook's installation,
scheduled-run/health, key-custody and isolated restore procedures for each approved
target. Verify DSM/ARM64/client/TLS versions, private destination/database bindings,
separate key custody, Windows-independent schedule/health alerts,
failed/interrupted/missed-run recovery, no-prune capacity, actual compute/transfer and
backup-age gaps, and retained-archive restore with entity/accounting/content checks.
Record alternate-operator execution and observed limitations in that issue; keep managed
database recovery and operational readiness verification recovery evidence linked there.

These are repeatable verification requirements, not a second stale checklist of
which operator steps have completed. A code merge or one manual backup does not
prove schedule/alert/restore acceptance.
## Operational acceptance checklist

Apply this checklist for the selected deployment; it records required checks,
not the completion state of a particular installation or drill.

- [ ] Direct ARM64 execution, exact DSM/package/image versions, compatible TLS
  PostgreSQL clients and resource capacity are proven on DS220j.
- [ ] Private destination/database inventory, owners, alternate operator,
  secret/ACL/encryption/remount checks and both key-custody plans are recorded.
- [ ] Production/UAT bindings are independently checked; all intended databases
  have distinct protected configuration and complete coverage.
- [ ] Sunday/Wednesday 22:00 Eastern production and 22:30 Eastern UAT registration,
  DST next-run dates, controlled scheduled runs, **production 3-hour and UAT
  6-hour** catch-up/health ticks, and independently delivered health alerts work
  with Windows/application offline.
- [ ] Failed/interrupted/missed-run tests preserve the last usable archive;
  partials remain quarantined. Capacity visibility and no-prune retention hold.
- [ ] Provider usage scope/period/reporting delay, capacity headroom and actual
  backup ages/point gaps are recorded; per-run attribution limits are explicit.
- [ ] A retained NAS archive decrypts/restores into isolation and passes complete
  inventory, entity isolation, accounting integrity/content and alternate-operator
  checks. Ownership/grant exceptions, effective-role checks and database/full-service
  RTO distinctions are recorded alongside backup age and recovery limitations.

## Developer validation

For audit-aware acceptance, retain an independent per-entity audit oracle while the
source and archive capture are frozen, then run [`audit-verify`](AUDIT_RECOVERY.md) on
the isolated archive restore before any upgrade. This checks full retained event
identity/content, activation/baseline, ledger consistency and restored protections. NAS
backup and restore's completed owner-operated technical drill did not execute that
comparison and does not by itself satisfy atomic audit history. Keep any unavailable
old-point audit tail explicit; never synthesize it.

Focused unit tests are in `tests/test_nas_postgres_backup.py`. The self-contained
synthetic client drill starts a new private PostgreSQL cluster inside the image,
uses verified TLS/SCRAM channel binding, backs up two entities with overlapping
IDs, decrypts/restores to a new database, compares content and broader objects,
then checks real certificate/hostname/authentication failures and ciphertext corruption. It publishes
no port and cleans only its own fresh synthetic fixture. It does not substitute
for canonical application bookkeeping verification or real NAS/provider evidence.

```sh
docker build -f deploy/nas/Dockerfile -t tabula-nas-backup:test .
docker run --rm --user postgres --entrypoint python3 \
  --mount type=bind,source="$PWD/tests/tooling/nas_backup_smoke.py",target=/smoke.py,readonly \
  tabula-nas-backup:test /smoke.py
```

Primary tool references: [pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html),
[libpq service file](https://www.postgresql.org/docs/current/libpq-pgservice.html),
[password file](https://www.postgresql.org/docs/current/libpq-pgpass.html),
[Neon logical backups](https://neon.com/docs/manage/backup-pg-dump), and
[age](https://github.com/FiloSottile/age).
