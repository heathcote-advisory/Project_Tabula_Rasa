# Complete PostgreSQL bookkeeping export

Revision `0005_bookkeeping_cutover` adds operator authority/evidence controls outside
the portable bookkeeping inventory. Revisions `0006_identity_control_plane` and
`0007_identity_sessions` add separate identity and durable authentication tables, also
outside this inventory. Revision `0008_owner_lock_scope` changes only identity trigger
scope, and `0010_workspace_lifecycle` adds only control-plane lifecycle records; neither
changes the bookkeeping inventory. `0010_onboarding_routes` only extends the login
return-path constraint; the same v1 inventory remains complete. Revision
`0009_external_connections` adds external-connection and legacy-claim control records,
also excluded from the same complete v1 bookkeeping inventory. Revision
`0011_control_plane_join` marks the integrated forward head without changing data.
Revision `0012_operations` adds private job/audit history, excluded from ordinary v1
exports. Revision `0013_accounting_controls` adds restricted recurring/period policy and
correction relationships/reasons outside v1. Reversing journals remain ordinary portable
entries/postings. A v1 snapshot alone does not restore period or correction immutability
controls; use full database recovery for those guarantees. Customer delivery
wraps this exporter without changing its format; its [handoff contract](../browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md#customer-bookkeeping-and-accountant-handoff)
explicitly excludes audit history. Revision `0017_customer_exports` adds only
content-free delivery evidence outside canonical v1. Revision `0018_drive_oauth`
adds customer authorization state, also excluded from v1. Existing v1 exports from
`0004_mileage_order` remain valid; the exporter reports the observed supported revision
and validates the same exact collection/field coverage. Migrated document ID mappings
are included in provenance validation. The [cutover guide](CUTOVER.md) owns verification
of this export before authority switching; this export does not replace managed
backup/PITR.

Revision `0021_customer_lifecycle` adds customer closure requests and their
minimized transition history outside canonical v1. The 26 financial collections
and export encoding remain unchanged. During the 60-day closure window an Owner
can still obtain a handoff; closure requires acknowledgement that needed exports
were saved. The [lifecycle guide](../operations/CUSTOMER_LIFECYCLE.md) defines access
expiry and retention; customer exports do not include lifecycle or audit history.

Revision `0022_drive_recovery_binding` retains private Drive recovery metadata
on the connection record outside canonical v1.

The application-owned export service reads one explicitly selected entity
through a PostgreSQL adapter, without Sheets access, page initialization or
document downloads. It does not activate PostgreSQL as a live ledger.

## Operator procedure

Install the development or PostgreSQL dependencies and use the project's active
Python environment. Supply the intended database through the existing
`TABULA_RASA_DATABASE_URL` process environment or protected `.env.local`; see
[PostgreSQL configuration](../database/POSTGRESQL.md). Use a role with read access to the
canonical tables, Alembic revision and schema catalog.

Create a private output directory **outside all Git checkouts**, with access
restricted to the operator. On Windows, set the directory's ACL before export:
the temporary and final files inherit it. On POSIX, artifact files use mode 0600.
Use a neutral filename, a protected nonsynchronized filesystem, and appropriate
encrypted storage. Do not paste contents into logs, tickets, chat, or the
repository. Keep real exports separate from synthetic test artifacts.

```powershell
python scripts/export_postgres_entity.py --entity ENTITY_ID --output 'C:\PrivateExports\ledger.bookkeeping.json'
python scripts/export_postgres_entity.py --entity ENTITY_ID --validate 'C:\PrivateExports\ledger.bookkeeping.json'
python scripts/export_postgres_entity.py --schema
```

`--entity` is mandatory for export and validation; there is no remembered or
fallback profile. The operator must already be authorized to access that entity.
A database URL and CLI entity selector are not membership authorization.
Validation and the public schema command are offline and do not construct a DB
pool or read Sheets/DocumentStore configuration.

Export reads use one independent, read-only REPEATABLE READ transaction, including
revision/catalog checks, entity identity and every domain. Nested database
operations are refused, including nested read-only operations, so uncommitted
records cannot be presented as a committed export. The artifact is validated
before writing. Output refuses a Git checkout (including managed worktrees),
existing files, and missing directories. A fully written, flushed temporary file
is atomically published without overwriting the destination. The filesystem must
support same-directory hard links. A cleanup failure after publication does not
change successful delivery. Interrupted exports or cleanup failures can leave a private
`.bookkeeping-*.tmp` file; inspect/remove that specific file locally.

The command prints only a status, format version and diagnostic count. On
failure it prints a fixed error code and exits 1, with no row contents, paths,
URLs or provider errors. `complete` and `complete_with_diagnostics` exit 0:
the latter means all supported records were preserved, with inspectable problems
identified in the **private artifact**. It does not certify a healthy ledger or
authorize activation. Unknown schema/fields, missing domains, invalid values,
broken required references or unrepresentable accounting records fail before
publication. Fix the cause or inspect the source locally; never edit validation
requirements to obtain a success result.

## Fixed book currency and compatibility

`business_entities.currency_code` identifies every amount in the selected
business's artifact. CAD and USD preserve the same exact decimal strings,
signs, scales, identities and 26-domain inventory; no FX is performed. Forward
schema `0020_book_currency` changes the allowed currency, not the export field
shape or encoding, so canonical format remains version 1. The validator accepts
both currencies and the new database revision; older supported USD revisions
remain valid. Older validators may reject CAD or the newer database revision;
upgrade the validator rather than relabeling currency or bypassing checksums.
Unknown currency codes fail validation. The customer ZIP embeds this same
validated canonical metadata and checksum unchanged. Its version 1 manifest and
document-integrity checks remain unchanged; the canonical root owns currency.

Financial CSV columns and numeric cells remain compatible. Delivery labels and
filenames identify the selected book currency; API CSV responses also carry
`X-Book-Currency`. Currency-free CSVs must be kept with that delivery context;
they do not independently prove their source currency.

## Version 1 format

The artifact is UTF-8 JSON with two objects: `manifest` and `domains`. It uses
the canonical [ADR 0001](../adr/0001-canonical-accounting-schema.md) concepts, not
worksheet layouts. `--schema` publishes its machine-readable JSON Schema;
`tabula_rasa/bookkeeping_export/schema.py` owns the field, identity and
relationship inventory. The validator additionally checks entity scope,
completeness, relationships and accounting meaning beyond JSON Schema.

| Manifest field | Meaning |
| --- | --- |
| `format`, `format_version` | `tabula-rasa-bookkeeping`, integer `1`; independent of Sheets/archive format 13 |
| `database_schema_version` | Observed supported PostgreSQL revision (`0004_mileage_order` through `0013_accounting_controls`; all have the same v1 financial inventory, excluding identity/session/connection/claim/lifecycle/job/audit and period/correction control records) |
| `entity_id` | Explicit selected business entity; also present on every entity-local record |
| `snapshot_at` | Database transaction-start instant in UTC; all reads use that transaction's coherent snapshot |
| `counts` | Count for every included canonical collection, including zero counts |
| `content_sha256` | SHA-256 of canonical normalized `domains` JSON |
| `status`, `diagnostics` | `complete` or `complete_with_diagnostics`; diagnostics give collection, zero-based record index, and fixed code |
| `document_bytes` | Always `external`; metadata and links are included, document bytes are not |

Every listed collection is required, even when empty:

| Included collections | Preserved content |
| --- | --- |
| `business_entities` | Ledger identity, name and immutable book currency (USD or CAD) |
| `accounts`, `categories`, `payees`, `tags` | Hierarchies, signed opening balances, classification/kind, names and inactive historical references |
| `entries`, `postings`, `entry_tags` | Simple and advanced entries, complete ordered canonical postings, payee references, source ownership and tags |
| `mixed_use_sources`, `mixed_use_tags` | Full costs, exact percentages/allocations, expected projection identities, relationship states and source tags |
| `recurring_schedules`, `schedule_tags`, `recurring_occurrences` | Current templates/status/attention, historical occurrence dates/types and typed owners |
| `fixed_assets`, `asset_tags`, `asset_period_overrides`, `depreciation_events` | Acquisition/retirement terms and references, historical overrides, committed periods and source-owned entries including zero periods |
| `mileage_records`, `mileage_tags` | Exact one-decimal trips, insertion ordering and tags; no financial postings |
| `documents`, `document_links` | Linked and unreferenced document identities/metadata, external addresses, logical owners and explicit receipt positions |
| `import_batches`, `import_records` | Persisted source/format/status, fingerprints and accepted record attribution |
| `migration_runs`, `migration_units`, `migration_id_mappings` | Retained source binding/digests/checkpoints, deterministic ID mappings/reasons and original source update instants |

No additional settings, audit or authentication records are invented when the
current schema does not persist them. Persisted identities, creation/update
instants, incarnation IDs and aggregate versions are retained; these versions
are history metadata, not authorization or reusable commercial form baselines.

Money is a signed/exact decimal string with two places, percentages have five
places, and mileage one place. No amount passes through float. Calendar dates
use `YYYY-MM-DD`; instants use UTC with six fractional digits and `Z`. Absent
optional fields are JSON null. Booleans and integer versions/positions retain
their JSON types. Collections sort by their declared canonical primary identity,
using Python string/integer ordering; object keys sort lexically in serialized
JSON. Thus unchanged content has identical normalized bytes/digest, apart from
the manifest's snapshot instant.

Simple and journal posting ID namespaces remain separate through
`projection_kind`. Opening balances are retained as signed account values,
without synthesizing entries. Transfer postings do not create income/expenses.
Allocations use the existing domain calculation owner; historical schedules and
depreciation events are exported as persisted, never regenerated. Canonical
projection/comparison helpers in `bookkeeping_export/accounting.py` reconstruct
the existing reporting read models and compare exact posting/opening facts;
the existing reporting service still owns report calculations.

Declared `missing_projection` and `projection_mismatch` mixed-use states are
preserved with diagnostics, without automatic repair. Retained migration mappings
whose target was deleted are preserved with `historical_target_absent`, since
migration attribution intentionally outlives its target. Unknown mapping kinds
fail explicitly. Other missing required references, scope violations,
unbalanced/invalid entry shapes or undeclared source relationship problems fail.
Completeness describes coverage of persisted supported data, not whether an
incomplete migration has loaded all of its source units.

The format pins its supported database revision for both export and offline
validation. The adapter also checks every supported
table's exact field inventory before reading data. A later schema revision must
review export coverage and update its compatibility decision; supported new
bookkeeping fields/domains must not be silently ignored. The format is an
application portability contract, not a raw SQL dump or a schema generator.
The checksum detects accidental content changes; it does not authenticate an
artifact's origin or prove completeness after malicious rewriting.

## External documents and private-data boundary

Document metadata retains canonical `id`, `store_kind`, `store_scope_key`,
`storage_key`, `open_url`, names, MIME type and available size/checksum.
Links reference canonical document IDs, rather than equating an external file
key with ledger identity. Use the matching authorized DocumentStore and selected
entity to interpret/recover the locator. Unreferenced metadata is included so
previous unlink/delete operations do not hide recoverable receipts.

Export does not fetch, copy, verify byte availability or delete external files.
The operator separately retains/verifies the external document store and its
access configuration through approved private procedures. Credentials, OAuth
tokens, password material, users/memberships and other entities are excluded by
the explicit bookkeeping allowlist. Raw import files and document bytes are
excluded. Descriptions, receipt metadata and source workbook identifiers inside
the artifact remain private bookkeeping data and must be protected accordingly.

## Initiative handoff and activation evidence

[external release and customer lifecycle](../browser/RELEASE_VALIDATION.md) should reuse
`EntityExportService`/the PostgreSQL composition helper for customer exports. It owns
authenticated entity selection, asynchronous delivery, access expiry, retention/deletion
and accountant handoff UX. This operator command adds no commercial endpoint, membership
model or customer lifecycle policy.

[authority cutover](CUTOVER.md) must record a verified **target PostgreSQL entity**
export before migrated-entity activation: offline validation result, coverage counts,
reviewed diagnostics, and normalized accounting/report comparisons with whole-profile
acceptance's accepted whole-profile evidence. Store private evidence outside the
repository. A complete export of a partially loaded target, or an export with unresolved
bookkeeping diagnostics, is not activation evidence by itself. Final reconciliation and
the frozen Sheets source contract source-format boundary remain independent
requirements; commercial production additionally requires mutation baseline contract
integrity/concurrency and managed database recovery evidence.

Export is not managed backup/PITR, reverse migration to Sheets, a new import/ restore
workflow, or authorization for cutover. managed database recovery and platform
operations retain backup, requested-point recovery and restore rehearsal ownership. This
capability does not delay reference repositories, transaction and journal repositories
and reporting repository's milestone for browser/API integration.

## Validation

```powershell
$env:TABULA_RASA_TEST_DATABASE_URL = 'postgresql+psycopg://tabula_dev@127.0.0.1:55432/tabula_dev'
python -m pytest tests/test_bookkeeping_export.py tests/postgres/test_entity_export.py
```

Native tests create fresh disposable databases and seed two synthetic entities
with overlapping IDs. They cover all 26 canonical collections, exact values and
historical labels, source ownership, document-byte separation, independent report
totals/balances, deterministic content, offline validation, omitted domains,
corrupt references/accounting, schema drift, private no-clobber delivery and a
concurrent committed write between export reads. Tests never use a live ledger,
profile secrets, credentials or real export artifacts.
