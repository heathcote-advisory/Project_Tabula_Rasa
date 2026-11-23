# Sheets migration and reconciliation

The migration framework reads the frozen format-13 source and loads dependency-
complete PostgreSQL units using canonical domain validators. Loaders preserve
historical recurring identities and source provenance. Whole-profile acceptance
and authority activation follow [cutover](CUTOVER.md); complete recovery output
uses [entity export](EXPORT.md). The framework does not replay jobs or activate
a profile merely because loading succeeded.

## Reuse and ownership

- Source schema: `storage/google_sheets/schema.py` owns exact headers. The
  existing source table validators own hierarchy, references, journals,
  recurring provenance, mixed pairs and depreciation history.
- Domain normalization: existing reference, transaction, recurring, asset and
  accounting-posting rules retain ownership; migration does not replay jobs.
- Target writes: the shared PostgreSQL Database, reference value builders and
  accounting line writer own SQL boundaries and canonical posting construction.
- New logic: read-only extraction, private frozen snapshots, deterministic
  mappings, dependency-complete units, run metadata and checkpoint orchestration.
  Specialized loader registration is the extension point for core migration loaders and secondary migration loaders.
- Core/secondary extension: existing asset writers and override normalization,
  receipt metadata validation and links, recurring projections, and report
  calculations remain owners. Complete-unit loaders supply existing records;
  the migration comparison shares repository projections with restart checks.
  The reporting adapter exposes its existing projection on the caller's coherent
  read snapshot. No separate report, allocation or generation engine is added.
- No parallel runtime recovery or accounting state machine is introduced.

## Target schema

Use the [supported PostgreSQL migration chain](../database/POSTGRESQL.md#initial-baseline-and-sheets-migration)
and verify the target revision before loading. Apply schema changes through the
normal forward migration procedure. Preserve an unexpected retained database
and investigate its revision before proceeding. Whole-profile acceptance and
authority activation follow [controlled cutover](CUTOVER.md).

## Explicit source preparation tools

The profile/OAuth and Drive-only receipt-association preparation converters are
retired. Local configuration accepts only version 2; service-account profiles are
unsupported. Preserve an old configuration privately and configure the intended
profile using [local Google setup](../SETUP.md). Current consent, grant replacement,
Picker authorization and folder administration remain supported.

Migration accepts only the frozen format-13 source with its existing explicit receipt
relationships. It never infers associations from old Drive metadata or converts an
older workbook/archive. Validate the selected source before extraction. A missing or
incorrect relationship requires an approved source correction before the final freeze
and fresh acceptance; after PostgreSQL divergence, use PostgreSQL/document recovery
or supported in-app corrections. Never reload or mutate a frozen source to repair live
PostgreSQL books. Preserve ledgers, receipt bytes, grants, exports and prior backups.


## Operator procedure

Install the development/PostgreSQL dependencies. Use the existing approved
profile configuration and human Google OAuth grant; do not copy credentials
into commands or repository files. Pause source edits and recurring/asset jobs
for the extraction/load window. Sheets provides no cross-worksheet transaction;
the operator-maintained source freeze is required for a coherent snapshot.

Run from the repository root:

```powershell
python scripts/migrate_sheets_to_postgres.py --profile alpha --entity rehearsal-alpha
```

Dry-run is the default. It opens the selected final-format live workbook
through `from_secrets_read_only`, consumes the shared exact-header validator,
validates domain/reference/provenance relationships, creates a private in-memory
frozen snapshot and plans every source row. When the selected profile has a
configured receipt folder, it also reads that folder's app-managed receipt
metadata through the same profile's human OAuth grant. This includes unreferenced
files. It never downloads, uploads, deletes, backfills, or moves receipt bytes.
It never initializes Sheets, creates
a PostgreSQL pool or writes run metadata. Backup archives are not accepted.
Unsupported/retired schemas fail before either source-ledger or target writes.
Record and reference identifiers must be unpadded; fix surrounding whitespace
in the source privately before extracting a new snapshot.

The JSON report contains versions, opaque run/digest identifiers, unit counts,
missing loaders and explicit provisional acceptance/cutover=false fields. It
contains no amounts, balances, descriptions, names, receipt content or secrets.
Errors expose fixed diagnostics and worksheet/row coordinates only. Snapshots
and unit reprs hide their contents. No private snapshot is automatically written
to disk, logged or committed.

Use `--secrets <approved-path>` only to select a different approved configuration
file. Profile selection is required; the tool has no selected-profile fallback.
Source workbook identity must match that explicit binding. Target entity scope
is mandatory on every SQL operation and relationship. These operator settings
do not grant commercial tenant membership or permission.

After dry-run, provision/inspect an inactive, empty target and initialize its
schema separately with the PostgreSQL operator tools. Supply
`TABULA_RASA_DATABASE_URL` through approved secret injection. Then explicitly load:

```powershell
python scripts/migrate_sheets_to_postgres.py --profile alpha --entity rehearsal-alpha --load
```

The tool refuses a populated entity or one bound to a different profile,
workbook, snapshot, mapping or manifest. It can provision an entity root in an
already initialized database; it never upgrades schema, seeds defaults, resets
data, selects the live backend or dual-writes traffic. A new entity currently
uses the selected profile's display name and the canonical USD default.
An existing empty entity must match that frozen display identity and currency.
Source default accounts/categories are preserved as ordinary reference rows;
additional defaults and balancing opening entries are never invented.

## Determinism and unit extensions

Manifest/mapping version `1` is independent of Sheets format `13` and the PostgreSQL
revision. A run ID derives from versions + explicit profile/workbook/entity binding +
normalized source digest. Header order, worksheet row order and extraction time do not
change it. Financial values use existing Decimal/domain normalization. Source creation
times stay on canonical records; missing category metadata uses the documented UTC epoch
`1970-01-01T00:00:00Z`. Original update times stay in
`migration_id_mappings.source_updated_at`. PostgreSQL aggregate `updated_at` continues
to record actual target mutations; its version/history triggers are never disabled or
bypassed. This is an explicit normalization difference for whole-profile acceptance to
reconcile.

Preserve original IDs within their valid target namespaces. When a simple entry
and journal share an ID, preserve the simple entry and deterministically remap
the journal to an unused UUID, recording the reason. Journal posting IDs stay in
their separate namespace; simple posting IDs use the existing shared expansion.
Historical ordinary generated occurrences may have UUIDv4 identities: the
original recurring caller calculated a UUIDv5 but did not pass the explicit
transaction identity into preparation. The application-service path corrected
that caller in `84893b0`. Migration preserves those existing UUIDv4 IDs rather
than rewriting the source or regenerating history. Schedule references, complete
provenance and unique occurrence dates remain mandatory; other generated-ID
mismatches remain rejected. This allowance belongs only to one-time extraction.

Mapping rows and unit hashes are persisted before loading and compared on every
restart. No historical batch/actor facts are invented.

Unit planning covers entity/references, ordinary entries, complete journals,
schedule foundations, recurring entries, mixed owners/projections/occurrences,
asset/acquisition/depreciation history and mileage. Each source row belongs to
exactly one unit. References precede owners; schedule precedes occurrence; a
complete acquisition unit precedes its asset/history unit. Missing dependencies
or cycles stop planning. Existing projections/events are preserved in each
unit, never regenerated by a scheduler or asset-posting service.

The CLI registers all implemented loaders: foundation, ordinary/simple entries,
complete journals, schedules, ordinary recurring occurrences, mixed pairs and
their source-owned occurrences, asset/override/depreciation history, mileage,
and receipt/document metadata. Existing recurring and depreciation entries retain
their original IDs; future occurrences/quarters stay unposted. Acquisition units
precede their asset owner/history. Mileage remains separate from accounting.

The private snapshot context freezes the profile display identity, selected
receipt-folder binding, normalized inventory metadata, available Drive creation/
modification instants, and inventory coverage. These facts participate in the
source digest and run identity. A historical document without an independent ID
receives the existing entity/store/locator-derived identity; portable IDs remain
preserved. Document mappings are persisted with worksheet mappings. Unknown
creation instants use the documented UTC epoch; known original update instants
remain in mapping provenance because target aggregate timestamps are target-owned.
Neither provider locators nor financial/receipt metadata appear in the JSON report.

Referenced inventory metadata must match the frozen ledger reference exactly. Legacy
references without checksums preserve their available metadata and remain unverified;
migration never manufactures missing integrity. Wrong profile/folder, missing or
conflicting receipts, duplicate locators/identities, and receipts with multiple
bookkeeping owners fail before target writes. Mixed receipts have one Source-owned link
set, read by both views. Unreferenced documents have no link. Document bytes and OAuth
credentials stay provider-owned, outside PostgreSQL.

Malformed/missing/mismatched mixed projections and orphan owners fail shared source
preflight. A nonempty historical acquisition ID absent from Transactions is explicitly
reported before any target preparation: the current canonical FK cannot represent that
dangling reference. Preserve the source and stop for an explicit provenance decision;
this implementation neither clears that field nor invents the missing entry. Such
anomalies and their inspection/import policy remain completion work for core migration
loaders and secondary migration loaders and must not be counted as accepted profiles.

The programmatic runner keeps its original core-only default for framework
consumers; pass `secondary=True` for the complete loader set. Missing loaders still
block load before mutation. Receipt-bearing units require their document
dependencies and cannot silently use a receipt-free path.

Register additional infrastructure loaders through
`PostgresMigrationRunner(database, loaders={unit_kind: loader})`. Each loader
implements `load(connection, plan, unit)` and
`verify(connection, plan, unit)`. The runner owns the outer transaction and
checkpoint; loaders must use the supplied connection, explicit entity scope and
`plan.target_id(...)`, preserve original owners/history, and never commit,
initialize schema, generate new occurrences or move document bytes.
`verify` compares committed target meaning with the frozen unit on an explicit
restart, including its references/details. A success response is authoritative;
the runner does not add post-success readbacks.

## Failure and restart

Run preparation and each complete unit commit atomically. A unit's bookkeeping rows and
`loaded` checkpoint share one transaction. A failed uncommitted unit rolls back; prior
units survive. Status records contain no financial payload. The run remains `loading`
while all units are loaded; this is framework evidence, not the `validated` status
reserved for the acceptance process in whole-profile acceptance.

After a failure, keep source edits paused and rerun the same explicit command
against the same source/entity. Matching loaded units are verified and skipped;
pending units load once. A different source digest, identity map, checkpoint
manifest or altered/deleted committed target stops the run. There is no automatic
retry. An uncertain commit may already have its atomic checkpoint; explicit
restart inspects it instead of inserting a second entry.

If a source changed or target cannot be trusted, preserve the source and stop. An
operator may explicitly abandon/reset only a confirmed inactive rehearsal target, then
create a new run from a newly frozen source. The tool provides no destructive reset
command and never touches the configured live workbook. After PostgreSQL becomes active,
divergent-write recovery belongs to authority cutover and managed database recovery, not
this restart procedure.

## Explicit reconciliation

After loading, compare the same frozen source against the inactive target:

```powershell
python scripts/migrate_sheets_to_postgres.py --profile alpha --entity rehearsal-alpha --reconcile
```

`--reconcile` alone reads PostgreSQL without running load or changing run status.
`--load --reconcile` explicitly performs both stages. Plain dry-run still creates
no PostgreSQL pool. Source edits/jobs must remain paused so the extracted digest
matches the loaded run. Mismatches return nonzero CLI status and a safe JSON
pass/fail report with domain, field/check names and opaque record keys; the report
contains no private values or source record IDs. Operators can locate a record
privately by hashing `[worksheet_title, source_id]` with the migration digest helper.

Reconciliation uses one read-only REPEATABLE READ target snapshot. It checks:

- Entity identity/defaults, exact record counts and stable/mapped IDs, original
  update provenance, run binding, and every loaded unit checkpoint.
- Every current source field through the shipped repository projections, ordered
  complete journal postings, exact Decimal posting amounts/targets/payees, and
  balance per entry. Source creation instants normalize to UTC; tags compare as
  sets; receipt order remains meaningful; overrides compare by period.
- Recurring occurrence ownership, complete mixed pairs, asset overrides and
  existing depreciation event/history relationships, mileage and tag references,
  document metadata, and exact ordered bookkeeping links.
- Existing report outputs for all activity and each source calendar month, using
  the shipped PostgreSQL reporting adapter and shared calculations: account
  balances, classified balance sheet/current earnings/accounting equation, P&L,
  monthly performance, and direct/family Income and Expense category rollups.
  Programmatic callers may add representative inclusive date periods. Exact
  Decimal posting comparison precedes the report owners' float presentation.

Current Sheets records contain no complete historical import-batch attribution.
Migration preserves their recorded fields and extraction mappings, creates no
invented import batches, and states that normalization limit in the report.

`core_passed` and `secondary_passed` are separate evidence. `evidence_scope`
distinguishes core-only profiles from profiles containing secondary records. All results
remain `provisional_reconciliation`, with `whole_profile_accepted=false` and
`cutover_authorized=false`; even matching totals do not mark a run `validated`. The
cutover procedure enforces whole-profile acceptance and the independent activation
requirements. Use `--accept` for the new final whole-profile report; `--reconcile`
retains these explicitly provisional semantics. The standalone acceptance command is
read-only; cutover preparation records a successful matching run as `validated`. See
[Bookkeeping acceptance and cutover](CUTOVER.md) for its exact checks, output formats
and authority boundaries.

## Persistent staging checkpoint

Use a durable approved non-production target for a source-owned load/restart
rehearsal, with explicit secret injection and separate disposable test databases.
Verify current schema initialization/connectivity and retain sanitized target,
source/run and acceptance references. Local UUID fixtures do not establish hosted
acceptance, and a synthetic framework drill does not establish private migration.

Use [controlled cutover](CUTOVER.md) for private source execution and whole-profile
acceptance. Use [audit-aware recovery](../operations/AUDIT_RECOVERY.md) for recovery
and capacity evidence, and [managed database recovery](../operations/DATABASE_RECOVERY.md)
for commercial activation requirements. Preserve retained source and recovery targets.
The [original persistent UAT rehearsal](https://github.com/heathcote-advisory/Project_Tabula_Rasa/issues/254)
records the historical framework evidence.

## Migration tests

Run scoped source/CLI and native migration checks from the repository root:

```powershell
python -m pytest tests/test_sheets_migration_source.py tests/test_sheets_migration_cli.py tests/postgres/test_sheets_migration.py tests/postgres/test_migration_domains.py
python -m ruff check .
git diff --check
```

Native checks require an explicitly disposable `TABULA_RASA_TEST_DATABASE_URL`.
They cover deterministic source identities, complete/disjoint projections,
unit atomicity and uncertain-commit restart, changed-source/target refusal,
provenance drift and two-profile isolation. They neither read private Sheets
sources nor authorize loading retained staging or production. Historical local
counts and branch-integration chronology are evidence in the owning issues/PRs,
not a substitute for current-revision CI.
