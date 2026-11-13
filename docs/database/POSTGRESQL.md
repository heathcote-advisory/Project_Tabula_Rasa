# PostgreSQL development and operations

The PostgreSQL runtime provides explicit database configuration, connections, forward
migrations and isolated test infrastructure. [ADR
0001](../adr/0001-canonical-accounting-schema.md) owns the accounting model. This
foundation does not select a managed provider or activate a PostgreSQL ledger. The
Streamlit composition still uses Google Sheets, even if a database URL is present.
Installing only `requirements.txt` remains sufficient for it. Explicit per-profile
service composition and the whole-profile acceptance and authority cutover acceptance,
maintenance, activation and rollback procedure are documented in [Bookkeeping acceptance
and cutover](../accounting/CUTOVER.md). Forward revision `0005_bookkeeping_cutover` adds
operator controls without rewriting the retained schema baseline or changing the
canonical export v1 bookkeeping inventory. Identity revisions
`0006_identity_control_plane` and `0007_identity_sessions` attach organization ownership
to retained canonical entities and add durable authentication state without copying or
rebaselining ledger records. Revision `0008_owner_lock_scope` narrows user owner locks
to state updates/deletion, preserving membership and last-active-owner invariants. These
revisions are extended by `0010_workspace_lifecycle`, which adds recipient-bound
invitations, bootstrap idempotency receipts and server-verified session email while
retaining the bookkeeping inventory. `0010_onboarding_routes` permits the explicit
invitation login return route without changing ledger records. See [Workspace
lifecycle](../identity/WORKSPACES.md). Identity revisions are forward-only: recover with
an approved compatible procedure instead of a destructive identity downgrade. See
[Named-user authentication](../identity/AUTHENTICATION.md).

Revision `0009_external_connections` adds tenant-owned provider references
and approved existing-entity claims without rewriting bookkeeping. Follow
[external connection and claim setup](../identity/CONNECTIONS_AND_CLAIMS.md)
for separate encrypted secret custody and operator approval. It is forward-only
and does not activate authority, install credentials or migrate live profiles.

Revision `0012_operations` adds durable entity-scoped job definitions/runs/attempts
and append-only, same-transaction bookkeeping audit events. It preserves the
canonical v1 export inventory; audit inspection/export has separate authorization.
See [Durable jobs and bookkeeping history](../operations/JOBS_AND_AUDIT.md) for trusted
actor attribution, worker registration, operator commands, integrity checks,
retention/recovery limits and the explicit hosted scheduling setup checkpoint.

Forward revision `0013_accounting_controls` adds opt-in recurring generation
authority, monthly period state and immutable correction relationships. It
activates no entity policy. Follow [jobs operations](../operations/JOBS_AND_AUDIT.md)
and [accounting periods](../accounting/PERIODS_AND_CORRECTIONS.md) for separate adoption/rollback
procedures. Complete database recovery must preserve these controls and audit
history; the financial v1 export retains its narrower portability boundary.

Forward revision `0015_support_controls` adds expiring organization/environment
support grants and immutable support-action records. It provisions no operators
and changes no financial v1 export fields. See [Support operations](../operations/SUPPORT.md)
for human-approved provisioning, revocation and compatible rollback.

Forward revision `0016_business_tenancy` enforces one retained business per
private organization, including inactive bindings. It changes no canonical
bookkeeping/export fields and performs no automatic conversion. Ambiguous
legacy containers block the entire upgrade transaction. Follow the read-only
inventory, explicit reconciliation and compatible rollback procedure in
[Business tenancy](../identity/BUSINESS_TENANCY.md) before deploying.

## Access and migration tools

Revision `0014_document_locations` permits managed S3 connection references and retains
prior receipt locators separately from canonical metadata and links. It stores no
receipt bytes or credentials and preserves export v1's 26 domains. Follow [Document
storage and recovery](../storage/RECEIPT_RECOVERY.md) for explicit verified
Drive-to-Drive isolated recovery, preserved original grants/locators, private byte
archives and the selected-provider recovery procedure. Hosted results and limitations
are tracked in [receipt byte recovery](../storage/RECEIPT_RECOVERY.md) and [operational
readiness verification](../operations/AUDIT_RECOVERY.md). S3 adoption is optional under
document-provider adoption review and does not block the Drive-backed document provider
configuration and receipt byte recovery scope.

Use synchronous **SQLAlchemy Core 2.0 + Psycopg 3**, with **Alembic** for
schema revisions. Core supplies bound SQL, connections and pooling without
an ORM domain model. Repository SQL and returned-row mapping belong to
`tabula_rasa/infrastructure/postgres/`; services retain the existing application
contracts and domain owners. An ORM session or SQL row must never become a
service parameter/result. See the [SQLAlchemy Core documentation](https://docs.sqlalchemy.org/en/20/core/)
and [Alembic connection-sharing recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#sharing-a-connection-across-one-or-more-programmatic-migration-commands).

Database dependencies are pinned separately in `requirements-postgres.txt`.
`requirements-dev.txt` includes them for tests. A future commercial deployment
must install both its application dependencies and this database file.

## Local setup

From the repository root with Python 3.12 and Docker running:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
docker compose -f compose.postgres.yml up -d --wait
$env:TABULA_RASA_DATABASE_URL = 'postgresql+psycopg://tabula_dev@127.0.0.1:55432/tabula_dev'
python -m tabula_rasa.infrastructure.postgres upgrade
python -m tabula_rasa.infrastructure.postgres current
python -m tabula_rasa.infrastructure.postgres health
```

The Compose service uses PostgreSQL 18.6, a named Docker volume, loopback-only
port 55432, and trust authentication for disposable development. Use a different
`TABULA_RASA_POSTGRES_PORT` if the port is occupied. Use synthetic bookkeeping data
here; real staging/production use authenticated roles and appropriate TLS.
For a persistent UAT database, copy `.env.example` to `.env.local` in the
repository root and populate `TABULA_RASA_DATABASE_URL` privately. PostgreSQL tools
load that exact file independently of the working directory; existing process
environment variables take precedence, including explicitly empty values.
Values are read literally without variable interpolation. `.env.local` is
Git-ignored; `.env` and `.env.example` are not loaded. For Neon use the UAT
branch's direct/unpooled URL with `sslmode=require`; retain
`channel_binding=require` when supplied by Neon. The configuration accepts
PostgreSQL's `disable`, `prefer` and `require` channel-binding modes and rejects
invalid or repeated values without retaining credential-bearing parser errors.
See the [PostgreSQL connection-security options](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNECT-CHANNEL-BINDING).
Installing `requirements-postgres.txt` supplies the environment-file loader.
Deployment secret injection remains supported. The file does not change the
active Sheets ledger or initialize database tables.

`TABULA_RASA_TEST_DATABASE_URL` must still be supplied explicitly in the process
environment and must target a disposable test administrator database, never UAT
or production. Loading local database settings does not modify process
variables or select the test target. An operator may store the dedicated test
URL privately in `.env.local`, then explicitly load only that variable into a
test command's process without printing it. A separate, empty Neon project
using its direct TLS endpoint is suitable; its role must create and drop
disposable `tabula_test_*` databases. Explicit settings mappings bypass the file.
Neither CLI output nor settings repr includes
the connection URL, username, password, SQL parameters, or private row contents.

`docker compose -f compose.postgres.yml down` stops the local service and keeps
the volume. `down --volumes` destroys its development data and should be used
only for an intentional local reset.

## Environment variables

PostgreSQL settings use the `TABULA_RASA_` prefix. Update any previously
configured `TABULA_DATABASE_*`, `TABULA_TEST_DATABASE_URL`, or
`TABULA_POSTGRES_PORT` variables to the names below; the old names are not read.

| Variable | Meaning / default |
| --- | --- |
| `TABULA_RASA_DATABASE_URL` | Explicit target database. `postgresql://` normalizes to `postgresql+psycopg://`; other drivers are rejected. Optional for Sheets, required for DB commands. |
| `TABULA_RASA_DATABASE_CONNECT_TIMEOUT` | Initial connection timeout in seconds, 1–60; default 5. |
| `TABULA_RASA_DATABASE_POOL_SIZE` | Connections retained per process, 1–50; default 5. |
| `TABULA_RASA_DATABASE_MAX_OVERFLOW` | Extra concurrent connections per process, 0–50; default 5. |
| `TABULA_RASA_TEST_DATABASE_URL` | Explicit test administrator endpoint with CREATE DATABASE permission. No fallback to `TABULA_RASA_DATABASE_URL`. |
| `TABULA_RASA_POSTGRES_PORT` | Compose loopback port; default 55432. |

URLs accept only the transport options `sslmode`, `sslrootcert`, `sslcert`, and
`sslkey`. URL-encode credentials containing reserved characters. Database roles and pool
sizing are deployment concerns; selected business entity context remains mandatory on
every repository query/mutation. A DB URL is not an entity selector or commercial
authorization grant. identity and tenancy supplies authenticated membership.

## Schema versions and migration policy

`python -m tabula_rasa.infrastructure.postgres upgrade` applies all revisions
to head in one PostgreSQL transaction. An advisory transaction lock serializes
migrators in the same database. Schema changes run only through this explicit
operator step; startup and connectivity checks never create tables or stamp
revisions. The baseline refuses conflicting pre-existing table names rather than
silently adopting an unknown schema. Alembic's revision table is independent of
v13 Sheets/archive versions and per-record optimistic versions.

`current` reports the exact revision (null for an empty DB), repository head,
connectivity, and `at_head`. It exits successfully if connected, allowing
inspection before migration. `health` and `upgrade` exit successfully only when
connected and at head. These commands emit one credential-safe JSON object.
This is a foundation readiness diagnostic, not an application health endpoint.

Baseline revision **`0001_initial`** contains all 26 ADR tables, named
row-local constraints, entity-qualified FKs, required indexes, immutable object
identities, and deferred entry balance/shape and two-level hierarchy checks.
It includes category-kind revalidation and the owned-event requirement for
zero-valued depreciation entries. Opening balances remain signed account values;
they are not duplicated as entries. Document tables hold metadata/links only.

Each future migration gets a new immutable revision in
`tabula_rasa/infrastructure/postgres/migrations/versions/`, with explicit
upgrade/downgrade behavior and native tests. Do not edit a revision once it has
been deployed. The SQL files beside `0001_initial.py` are frozen migration
assets, not runtime schema/model generators. No autogenerate/create_all/stamp
path substitutes for versioned migrations.

For local/staging and later production, inspect `current`, review migration SQL
and data impact, establish the required recovery point, apply `upgrade` once as
a deployment step, then inspect `health`. The deploying operator supplies the
target secret and migration role; ordinary application roles should not have
schema-administration privileges. Destructive upgrades require explicit reviewed
migration instructions and maintenance/recovery planning; do not bury deletion
inside startup or an automatic compatibility branch.

Downgrades are **never automatic application rollback**. Prefer rolling back
compatible application code or shipping a corrective forward migration. A
downgrade may delete accounting data and requires the explicit
`--allow-destructive` switch plus an operator-reviewed recovery plan:

```powershell
# Disposable development database only: removes the entire canonical schema.

python -m tabula_rasa.infrastructure.postgres downgrade --revision base --allow-destructive
```

The baseline drops named owned tables/functions explicitly, without CASCADE; unexpected
external dependencies stop the transaction. It does not delete a database or
document-store files. Production backup/PITR requirements and real restore rehearsals
gate authority cutover activation. The [managed recovery
runbook](../operations/DATABASE_RECOVERY.md) owns managed database recovery's production
checkpoint, proposed objectives, separate real snapshot restore/PITR milestones and
read-only expected-point verifier; platform operations owns ongoing monitoring, document
recovery and incident/service restoration.

## Infrastructure operation convention

[Shared operation and baseline convention](CONCURRENCY.md) extends this runtime with the
adapter error boundary, ordered root guards and the consolidated initial revision
`0001_initial`. Later repository slices must consume its version and specialized
relationship/history guards before exposing supported mutations. Schema initialization
is explicit and atomic.

Composition constructs one `Database(DatabaseSettings(...))` per process/service
graph. Infrastructure adapters use `with database.transaction() as connection`
for a supported mutation. Participating repositories share that same Database
instance and context; nested boundaries join the existing connection, with
only the outer boundary committing. Adapters must not call `commit()` or create
independent transactions for coupled reference/source/entry writes.

A caught nested failure marks the entire operation aborted. A caught SQL failure that
leaves PostgreSQL's transaction aborted also cannot report commit success. Exceptions
roll back, return the connection to the pool, and reset operation context. Do not
swallow a database error and continue accounting work. There is no automatic mutation
retry or successful-write readback. Connection loss is an unavailable outcome, not proof
of rollback; later adapters under mutation baseline contract must classify uncertain
commits using their stable operation/record identities.

Top-level `transaction(read_only=True)` uses read-only REPEATABLE READ for
coherent reporting/export snapshots. Nested reads join a mutation's existing
READ COMMITTED transaction. A mutation cannot join a read-only operation.
UTC applies to instants; SQL dates keep their calendar meaning. Call `dispose()`
on worker/test shutdown. Infrastructure connections never escape to services.

Recurring reconciliation must invoke a separate outer operation for each
occurrence. Committed earlier occurrences survive a later failure; an aborted
occurrence rolls back before best-effort attention writes or the next schedule.
Wrapping an entire reconciliation run in this transaction helper is unsupported.

## Constraint names and error boundary

The baseline names PKs `pk_<table>`, FKs `fk_<table>_<reference>`, row checks
`ck_<table>_<rule>`, and unique constraints/indexes `uq_<table>_<keys>`.
The migration SQL is the exact constraint/index inventory. Aggregate checker
codes include `ck_entry_balance`, `ck_simple_shape`, `ck_simple_category`,
`ck_journal_shape`, `ck_depreciation_entry`, `ck_reference_hierarchy`,
`ck_object_identity`, `ck_object_version`, `ck_entry_identity`, and
`ck_posting_identity`.
Specialized codes include `ck_mixed_category`, `ck_mixed_relationship`,
`ck_recurring_relationship`, `ck_recurring_identity`, `ck_asset_relationship`,
`ck_asset_history`, `ck_depreciation_history`, `ck_document_target`,
`ck_import_target` and migration-only `ck_link_target`. All use SQLSTATE 23514
and the same safe `invariant` application code; constraint text is not user text.

| PostgreSQL SQLSTATE | Safe infrastructure error code |
| --- | --- |
| 23505 unique | `duplicate` |
| 23503 FK | `reference` |
| 23514 CHECK / deferred invariant | `invariant` |
| 23502 NOT NULL | `required` |
| 40001 / 40P01 serialization / deadlock | `concurrent_write` |
| 25006 read-only write | `read_only` |
| Other driver/connectivity errors | `unavailable` |

Messages never interpolate PostgreSQL DETAIL, raw constraint messages, record IDs,
fingerprints, or parameters. Unknown constraint names do not become user text. mutation
baseline contract and domain repositories translate these infrastructure codes into the
existing application error/result contract at their boundary. No provider exception type
is added to a service contract in this foundation.

## Repository composition

`create_postgres_services(database, entity_id)` supplies the existing ledger,
account, category, payee, tag, transaction, journal, reporting, recurring, mixed-use,
asset, mileage and import application services for one explicit, existing business
entity. All adapters share the same `Database`.
The caller supplies authorized entity context and owns the database lifecycle;
this function neither authenticates users nor selects the production backend.
Schema upgrades run as a separate deployment operation. The
[commercial rollout procedure](../operations/DEPLOYMENT.md#schedule-an-approved-forward-release)
owns immediate staging and owner-enabled scheduled production.

Each revision eligible for unattended migration declares its `rollout`
contract beside the Alembic upgrade. The current exact-head runtime requires
`mode = "maintenance"` for forward changes and `recovery = "forward_only"`.
Describe data rewrites, backfills and locking in `data`, and list the required
runtime `grants` as tuples of object kind, name and privileges. Supported kinds
are `table`, `sequence` and `column` with a `table.column` name. Review the
combined effect of sequential revisions. Destructive or unreviewed changes
require a separate operator plan. The bound migration checks the source under
the migration lock and verifies runtime grants before the transaction commits.
Retain a verified recovery point and use forward repair or isolated restore
after a committed migration. Application readiness still requires its exact head.

For a migrated disposable development database:

```python
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.runtime import Database
from tabula_rasa.infrastructure.postgres.reference_data import PostgresLedgerSetupRepository
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services

database = Database(DatabaseSettings.from_environment(required=True))
try:
    # Explicit provisioning, once per entity; duplicate IDs are refused.
    PostgresLedgerSetupRepository.create_entity(database, "example", "Example business")
    services = create_postgres_services(database, "example")
    services.ledger.ensure_defaults()
    loaded = services.accounts.list()[0]
    services.accounts.rename(
        loaded["id"], "Operating account",
        expected={"baseline": loaded["baseline"]},
    )
finally:
    database.dispose()
```

Defaults use the same definitions as Sheets and seed only when **both** accounts
and categories are empty. Names, active states, signed openings, classifications
and two-level hierarchies retain the current domain rules. Account deletion is
not a supported port. Category and managed-reference deletion refuse remaining
references. Payee/tag merges repoint every canonical owner atomically, including
journal line payees and mixed-source, schedule, asset and mileage associations.
Tag merges deduplicate associations before removing source identities. Display
names continue to resolve from stable IDs; a rename does not alter the referring
entry's baseline.

Simple Ledger entries contain the existing two canonical postings with stable
`<transaction-id>:1` / `:2` IDs. Advanced journals retain their original ordered
line IDs in the separate journal namespace. Simple and journal read models are
disjoint; both preserve exact amounts and shared accounting/domain validation.
Transaction CRUD/bulk and journal CRUD commit their complete aggregates together.
Inline payee/tag creation during service preparation joins the same transaction;
a validation or deferred-commit failure rolls it back too. Bulk selection is
all-or-nothing. No process-local workbook lock or remote-write retry applies.

Loaded records return the shared mutation contract's opaque `baseline` token from the
same snapshot as their values. Pass it separately through `expected`, never as an
editable field or entity selector. The adapters use the shared token/lock/error helpers
and `0001_initial` aggregate-version triggers. Returned tokens are mapped after
participating posting/tag/document-link writes, before the outer commit. A later stale
submit, including one after deletion and recreation with the same domain ID, fails.
Journals require a token or complete normalized loaded entry/posting snapshot.
Account/category snapshots must also be complete; the existing payee/tag/simple
transaction partial-snapshot compatibility remains. Derived labels and totals are
excluded from comparisons. Optional no-`expected` paths remain available for
legacy/internal callers and cannot detect a stale form. browser and API workflows must
propagate and require the agreed loaded baseline for supported commercial interactive
mutations; tokens confer no authorization.

Hierarchy mutations take the entity guard; accounting operations protect the reference
fields used during preparation. Bulk and merge roots use the shared mutation contract's
ordered aggregate locks. Merges discover all affected roots before locking and stop if
new owners appear while locks wait. FK, uniqueness and deferred posting checks remain
authoritative at commit. Repository failures use the shared safe error codes documented
in [PostgreSQL mutations and baselines](CONCURRENCY.md). An unavailable outcome does not
prove rollback and does not trigger a blind retry.

Receipt references can link only matching metadata already registered in the selected
entity. Unlinking/deleting an ordinary entry retains the document. Document acceptance
and external bytes follow the metadata/store boundary below. Existing Source-owned
projections and recurring provenance are readable; generic mutation of derived
projections or provenance is refused. Existing ordinary recurring occurrences retain
their association on edit and remove it on deletion. The secondary repositories
described below implement recurring/source/asset workflows alongside mileage and
imports. Receipt services require an explicitly supplied authorized DocumentStore.

Reuse map: the implementation inspected and extends the existing reference-data,
transaction/journal preparation, canonical posting translation, service assembly, and
database runtime and mutation baseline contract infrastructure owners. Shared bootstrap
defaults were extracted from the Sheets schema without changing workbook format. New SQL
mappings and legacy snapshot normalization belong to the core adapters; there is no
parallel domain state machine, token format, version counter or error translator.

## Mileage, receipt metadata and import adapters

These adapters extend the core implementation from the shared core repositories.
recurring and mixed-use repositories and asset repository are parallel work; this slice
does not implement their recurring, mixed-use or asset workflows.

`PostgresMileageRepository` maps native MileageTrip/MileageTag values to mileage records
and tag associations. The existing MileageService owns field normalization, active-tag
rules, round-trip preparation and QuickBooks mileage classification. Maps and routing
remain outside persistence. CRUD and submitted imports use the shared Database
operation, entity-qualified references and the shared mutation contract's baseline
checks. New/reactivated inline tags roll back with failed trips. Loaded mileage
baselines cover tag and import-attribution changes; update/delete accept the existing
optional loaded token or snapshot. Commercial callers must propagate their loaded
baseline.

Revision **`0004_mileage_order`**, after the consolidated `0001_initial` baseline, adds
a Database-generated insertion ordinal. Equal-date/equal-instant round trips show the
return leg first, edits preserve that ordering, and imported trips preserve their
prepared source order. The ordinal is an adapter ordering detail, not a bookkeeping
identity or source timestamp. Upgrading existing rows preserves their IDs, fields and
baselines; their previously unspecified tie order receives an ordinal. Downgrade removes
only this ordering detail. Coordinate Alembic revision ancestry if parallel recurring
and mixed-use repositories and asset repository work also adds a revision.

`PostgresReceiptRepository` accepts provider-independent metadata, canonical
document IDs, opaque store locators and links. Supply the selected entity's
authorized store and its stable kind explicitly:

```python
services = create_postgres_services(
    database, entity_id,
    document_store=authorized_store,
    document_store_kind="google_drive",
)
```

The store is configured for that same entity/profile context; its scope_key is
the opaque binding saved with accepted metadata. Schema/entity checks do not
authorize external byte access. Repositories never construct a Drive client,
read credentials, manage folders, fetch/upload files or delete bytes.
Without a store, mileage/imports are available and receipts remain uninstalled.
The existing Google Drive DocumentStore remains usable; selecting a future
object-storage vendor and adapting the current Simple Ledger receipt-reference
compatibility representation are separate work.

SQL acceptance and linking commit atomically. Stable canonical identity and
entity/store/scope/locator uniqueness deduplicate confirmed replays; conflicting
metadata or ownership is refused. One receipt belongs to one logical target.
The already-locked target serializes additions and enforces the 100-receipt
limit. Accounting and receipt mutations share `write_document_links` for
ordering, retained link timestamps and removal. Unlink and target deletion
retain document metadata and external bytes. Failed SQL linking leaves the
uploaded file recoverable from the DocumentStore inventory; ReceiptService's
existing recovery loop reuses its confirmed uploaded reference.

Mixed-use projection reads resolve the single source-owned link set.
`repository.for_mixed_use(source_id, expected_source={"baseline": loaded_token})` binds
a source-aware ReceiptRepository for `ReceiptService.with_repository`. It locks and
validates the complete pair and source baseline before changing links. A caller loads
the current source baseline for each mutation, including after its own earlier
successful link. Ordinary transaction receipt mutations refuse a mixed projection. This
integration seam is available to recurring and mixed-use repositories; it does not
create, repair or edit mixed-use pairs. Journals and depreciation entries remain
ineligible for receipt links.

### Existing receipt mapping

Explicit migration tooling may call `register_document` to accept known metadata without
linking or moving bytes. Preserve an existing portable DocumentMetadata.document_id.
When absent, `map_legacy_document` supplies the hex UUID5 of the URL namespace and
compact JSON `["tabula-rasa-document-v1", entity_id, store_kind, store_scope_key,
storage_key]`. Repeated mapping is deterministic and distinguishes entity/store
bindings. The original Drive file ID stays in storage_key. Preserve available filenames,
MIME type, size, checksum and URL; missing checksum/size stays unknown. Legacy Drive
names accepted by the frozen source contract remain representable. The compatibility
receipt record remains a legacy locator/name pair until integrity is known; a canonical
database ID alone never claims verification. Sheets migration framework and secondary
migration loaders own extraction manifests and migration ID-mapping records. This helper
does not migrate a live workbook, backfill bytes or invent historical provenance.

### Bootstrap and submitted imports

Reuse `PostgresLedgerSetupRepository.ensure_defaults` from reference repositories: one
entity guard, the shared default definitions and an atomic seed only when both
accounts/categories are empty. Repeated/concurrent setup does not duplicate defaults;
partial existing ledgers are preserved.

`PostgresTransactionImportRepository` uses the existing pure QuickBooks preview
and shared CSV input/reference mapping. Imports create references through the
existing reference adapters and entries/postings through TransactionService.
Fresh references are locked and duplicate selection is serialized with supported
ledger writes by the database entity guard. Auto-created references, payees,
balanced entries and completed import attribution commit together. A failure
rolls back the submitted batch; its safe error directs the caller to correct the
preview or inspect the ledger. There is no adapter retry.

Submit at most **1,000 transaction source rows**, or **1,000 accepted mileage
trips**, per operation; split larger sources deliberately. The repository does
not silently commit a prefix or retain raw uploaded files. CSV/QuickBooks batch
IDs hash their source kind/version and ordered supported field values before
fresh reference resolution. Entry IDs are reproducible UUID5 values from the
entity, batch identity and actual source row. An identical completed submission
returns no new rows, even after target edits or deletes; this also makes a
caller replay after lost commit confirmation safe. CSV retains identical rows
within a new submitted batch; QuickBooks retains the existing fingerprint
duplicate rules. Known accepted fingerprints continue suppressing repeated
QuickBooks rows after later edits. Manual identical transactions remain valid.

Transaction import_records retain actual source positions (CSV row 2 onwards, including
positions skipped as QuickBooks duplicates). Mileage's current port receives prepared
trips rather than raw source positions: its `mileage-prepared-v1` attribution records
accepted-trip ordinals, explicitly not raw CSV row numbers, and hashes accepted
normalized fingerprints for batch identity. Existing IDs supplied by mileage preparation
are preserved. Attribution is target-owned and cascades on deletion; a completed batch
is retained, but v1 does not promise permanent deletion history or every
differently-shaped resubmission after deletions. platform operations owns durable audit
extensions.

Reuse map: inspected MileageService/GoogleSheetsMileageRepository,
ReceiptService/receipt_records and the current receipt writers, the pure import preview
and Sheets CSV mapping, and core repository delivery's defaults/reference/accounting
adapters. Reuse those domain owners, the shared operation/baseline machinery and one
receipt-link writer. New logic is SQL mapping, metadata acceptance, Source-aware link
guarding and bounded accepted import provenance. No parallel Maps, accounting,
normalization or recovery state machine is introduced.

## Reporting repository

`PostgresLedgerQueryRepository` implements the `LedgerQueryRepository` contract and is
installed as `services.reporting` by the opt-in composition above. This is the core
reporting slice of PostgreSQL persistence and application transition and supports the
browser/API milestone. It does not activate an entity, migrate Sheets data, or satisfy
the later whole-profile reconciliation, complete export, recovery or production
activation gates.

`read_ledger()` opens its own read-only REPEATABLE READ operation. The entity
existence check, accounts, categories, entry projections, tags, receipt counts
and recurring metadata all use that connection and committed snapshot. Reporting
refuses nesting inside **any** existing `Database` operation, including another
read-only operation, with the shared `boundary_mismatch` error. This prevents
joining a READ COMMITTED mutation with incomplete/uncommitted aggregates; the
normal operation-abort rules still apply. Invoke reporting at the query boundary
and reuse the returned snapshot for multiple service calculations.

Reads include inactive accounts and historical payee/tag labels. Every join uses
the selected entity as well as local IDs. The adapter returns only native
`ReportingAccount`, `ReportingCategory`, `LedgerTransaction`, `LedgerJournal`
and `LedgerPosting` values: SQL dates, exact Decimal amounts, booleans, tuples
and UTC timestamp strings. SQL connections, rows, version tokens, document
locators and worksheet-shaped records do not enter these projections.

The immutable `projection_kind` separates simple entries from advanced journals.
Simple entries map their two ordered canonical postings into one transaction;
advanced journals retain their complete ordered lines, even when posting IDs
overlap the separate simple namespace. Mixed-use entries use source tags and
receipt links once, and occurrences resolve through the ordinary entry or mixed
source owner. Depreciation, including zero periods, remains a source-owned
Simple Ledger Expense. Only persisted activity is read; nothing is regenerated.

The adapter batches each relation rather than querying once per entry. There is
no process cache: `force_refresh` is accepted for port compatibility and either
value reads a fresh snapshot. Date/kind/account/category/tag filters, complete
journal selection, opening balances, P&L, current earnings and hierarchy rollups
remain owned by `ReportingService`, `ledger_queries.py`, `ledger_reporting.py`
and `accounting_postings.py`. Dataframe/display formatting remains in presentation
code. No financial calculations or second filter implementation are added to SQL.

### Query plans and index coverage

The current port reads the full selected entity before applying service filters; it does
not accept a SQL date/target/tag filter or pagination input. Snapshot work therefore
grows with that entity's ledger size. The canonical baseline already provides the
required access paths; reporting repository adds no migration or speculative index.

| Read relationship | Existing access paths |
| --- | --- |
| Entity/reference rows | `business_entities` PK; entity-leading account/category/payee/tag PKs |
| Entry projection and date | `ix_entries_projection_date (entity_id, projection_kind, txn_date, id)` and `ix_entries_date` |
| Complete ordered postings | `uq_postings_entry_id_position (entity_id, entry_id, position)`; entity-qualified projection/entry FK |
| Account/category targets | `ix_postings_account_entry` and `ix_postings_category_entry` |
| Ordinary/source tags | Entry/source join PKs and `ix_entry_tags_reverse` / `ix_mixed_use_tags_reverse` |
| Recurring owner lookup | Entity/entry and entity/source unique occurrence indexes |
| Receipt owner counts | Entity-leading document-link owner indexes and unique owner/position indexes |

`tests/postgres/test_reporting_plans.py` seeds two synthetic entities, each with
2,000 simple entries, 200 journals, 500 tag associations and 200 receipt links.
It analyzes table statistics, verifies the index catalog, executes the actual
adapter SQL with `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, checks output
cardinality, and enforces a fixed snapshot query budget (seven SELECTs rather
than one per entry). Both entities independently report income 22,000.

On PostgreSQL 18.6, the representative run returned 2,000 simple projections,
500 tag rows, 200 receipt counts and 400 journal lines. The planner used hash
joins and sequential/bitmap scans for broad entity reads, indexed mixed-owner
lookup, and the projection/date index for the smaller journal subset. Sequential
scans are reasonable when a selected entity contains half of a small table; the
test does not disable them or freeze a particular node strategy. Observed query
execution times were approximately 3.3, 0.9, 0.1 and 0.3 ms respectively, excluding
connection setup, transport and Python service calculations. These are development
diagnostics, not a production latency guarantee. Reassess with representative
customer distributions before extending the port or adding indexes.

Native reporting tests also verify independent expected balances/totals,
contra activity, inclusive dates, hierarchy rollups, complete journals, two-entity
isolation with overlapping IDs, inactive historical labels, source-owned history,
safe read failure and a bounded concurrent-write snapshot. The existing shared
service-contract fixture runs one independently calculated reporting workflow
on both Sheets and PostgreSQL.

## Recurring, mixed-use and asset repositories

Recurring, mixed-use and asset repositories implement the existing application
contracts over canonical entries and postings. They reuse core repository
transactions, loaded baselines, locks and integrity rules. Reporting, mileage and
ledger setup retain their own adapters and composition arguments. Integrations
must preserve each capability's arguments and shared accounting writer instead
of replacing the factory wholesale.

`PostgresRecurringRepository` provides schedule creation/update, coherent
snapshots, independent attention writes, initial expense creation and conversion.
The existing `RecurringService` owns cadence, due dates, reconciliation and
attention classification. A reconciliation snapshot closes before generation;
each occurrence commits its entry or complete mixed pair and its natural-key
association in one independent transaction. Only after that commit can its value
enter `generated`. A later SQL or deferred-check failure rolls back that one
occurrence, preserves earlier commits, and permits unrelated schedules to run.
Each occurrence locks the schedule in the shared aggregate order and checks the
loaded schedule baseline and active status before preparation; a concurrent
edit, pause or end stops stale generation while retaining earlier commits.
When another reconciliation has already committed the requested stable identity,
the waiting caller confirms matching business fields and occurrence provenance
without rewriting the record; mismatched contents or unrelated identities fail.
Mixed-use confirmation validates both the complete source and its projection.
Attention remains best effort: a failed attention write cannot undo occurrences
or poison the next schedule. An uncertain response fails safely without a blind
retry; callers inspect stable identities before resubmission.

`TransactionService.create_occurrence` uses the adapter's optional dedicated
occurrence hook; Sheets retains its existing create fallback. PostgreSQL's hook,
mixed occurrence creation and attention writes refuse an enclosing transaction.
Initial creation/conversion refuses a schedule that appears after lock discovery
with a reload conflict; it never adopts an existing root outside the ordered
lock set. It otherwise joins one operation covering the schedule,
inline payee/tag resolution, accounting and association. No pending partial pair
is created by these SQL operations. Repeated initial requests can confirm the
existing initial or generated occurrence at that natural key; a repeated
conversion with the same explicit schedule identity confirms matching committed
facts without another mutation. Conflicting payloads fail. Existing conversion
retains the source/entry identity, creation instant and receipt links. Template
changes preserve historical occurrences; ordinary occurrence edits retain the
association and deletion removes it. Schedule tokens exclude attention and
derived due/display metadata.

`PostgresMixedUseRepository` persists source-authoritative allocation output and its
deterministic Simple Ledger projection atomically. It reuses
`MixedUseService.prepare_pair`, transaction preparation and the existing
allocation/parity owners. Source and projection identities remain stable; tags are
maintained on both canonical owners, while receipts have one logical source link set
visible through both read models. Links require matching registered metadata in the
selected entity, enforce the current 100-receipt limit and one logical target, and
preserve retained-link creation instants during reordering. Unlinking or deleting a pair
retains document metadata and external bytes. Document registration/storage remains the
document repository's responsibility. `sources()` exposes declared missing/mismatched
relationships for inspection; normal list/get/edit, link and generation refuse
incomplete pairs and never regenerate them.

`PostgresAssetRepository` maps current asset properties, tags, optional acquisition
references and historical overrides to the canonical asset tables. `AssetService`,
`asset.py`, `depreciation.py` and `JournalService.prepare_depreciation` retain the
lifecycle, straight-line schedule and posting decisions. Depreciation remains a Simple
Ledger Expense with two canonical postings, including zero-valued quarters; it does not
add a second advanced journal. Its stable entry ID and the `depreciation_events`
entity/asset/sequence key identify the same committed period. Posting loads only the
selected asset history and candidate period identities, preserving collision checks
without projecting unrelated ledger records. All periods from one posting call commit
together; a failed period rolls back that call's entries, events and asset version.
Coupled infrastructure operations can join asset mutation and posting in the same outer
transaction. Loaded asset locks precede fresh history checks, so concurrent
edits/postings cannot bypass saved-history restrictions or double-post. Descriptive
edits and valid retirement remain supported; mixed-use depreciation (mixed-use asset
accounting) remains outside this slice.

All records and relationships include the selected entity. Source, schedule and asset
mappings return mutation baseline contract baselines after participating detail writes;
supplied stale or cross-entity tokens fail before mutation callbacks. Legacy/internal
no-baseline calls retain fresh locking but provide no stale-form guarantee. Browser and
API workflows remains responsible for requiring and transporting loaded baselines and
identity and tenancy for deriving authorized entity context.

Reuse map: inspect the three application/domain owners, their Sheets adapters, canonical
migration/ADR, core accounting/reference adapters and mutation baseline contract
operation helpers; extend the canonical writer for typed source ownership, source
receipt targets and ordered secondary-root locks. Small shared helpers own occurrence
projection and tag associations. No schema revision, allocation/recurrence/ depreciation
formula, parallel state machine, token scheme, retry manager or workbook lock is
introduced.

Native tests in `tests/postgres/test_recurring_repository.py`,
`test_mixed_use_repository.py` and `test_asset_repository.py` verify committed
results after immediate/deferred SQL failures, best-effort attention failure,
rerun deduplication, source/receipt atomicity and retention, inspectable incomplete
states, asset history/zero periods, shared-operation rollback, independent loaded
callers and overlapping writers, and two entities with overlapping identities.
`test_secondary_service_contracts.py` runs independently expected month-end,
allocation, conversion and depreciation workflows through both Sheets and native
PostgreSQL services. Tests use only synthetic disposable databases.

These adapters do not switch the active Sheets ledger, add scheduling workers, migrate
source records or document bytes, or satisfy production activation by themselves.
secondary migration loaders can consume these domain paths for secondary migration;
whole-profile acceptance whole-profile reconciliation, complete entity export, real
database recovery rehearsals, frozen Sheets source contract's frozen source boundary and
the shared mutation contract's consumer invariants still gate authority cutover
commercial production activation.

## Native integration tests

```powershell
$env:TABULA_RASA_TEST_DATABASE_URL = 'postgresql+psycopg://tabula_dev@127.0.0.1:55432/tabula_dev'
python -m pytest tests/postgres
```

Every integration test creates its own uniquely named `tabula_test_<UUID>`
database, migrates it from zero, closes its connections, and drops only that
database in fixture cleanup. The administrator database is never truncated,
downgraded, or populated by these fixtures. No SQLite substitute is accepted.
Missing test configuration skips integration tests; configured but inaccessible
PostgreSQL fails them. Configuration and Sheets independence tests still run
without a test endpoint. `.github/workflows/ci.yml` supplies real
PostgreSQL for automated checks, so its integration coverage cannot skip for
missing configuration. Interrupted runs may leave a disposable UUID database;
an operator may remove that specific test database after checking its identity.

Coverage includes zero-to-head and repeat upgrades, destructive downgrade and
re-upgrade, schema inspection, direct invalid writes, two-entity references,
exact money/signed openings, complete posting commit, nested atomicity, aborted
operations, independent-connection read snapshots, and safe failure diagnostics.
Core tests cover service workflows on both adapters, complete canonical postings,
all-owner multi-source merges, two-entity isolation, late validation/commit rollback,
inline-reference atomicity, independent loaded callers and overlapping writers,
recreated identities, receipt metadata retention and immutable provenance.
CI runs the complete Python suite with this endpoint on every PR revision.
Use it for affected native tests locally; a full local run is exceptional
diagnostic work as described in [Development](../DEVELOPMENT.md#required-validation).

## Delivery boundary for the initiative

The PostgreSQL runtime, reference/accounting, reporting, recurring/mixed-use,
asset, mileage, document and import repositories implement the application ports.
HTTP/browser composition consumes those services; see
[Commercial workflows](../browser/BOOKKEEPING.md) and
[existing workflow ports](../browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md).
Shared baseline/version/lock and history constraints remain necessary in every
adapter; successful row/FK checks alone never establish domain correctness.

Completed implementation and synthetic verification are recorded in [PostgreSQL implementation evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/220). [live owner cutover](../accounting/CUTOVER.md) owns actual
owner-source migration, accepted bindings and single-backend authority; [operational
readiness verification](../operations/AUDIT_RECOVERY.md) owns stage-specific operational
evidence and [commercial recovery agreement](../operations/DATABASE_RECOVERY.md) retains
commercial recovery/activation. Repository delivery does not activate private data or
authorize dual writes, source deletion or a customer release.

## Initial baseline and Sheets migration

The migration framework consolidates the unused pre-production `0001_canonical`,
`0002_aggregate_versions`, and `0003_domain_integrity` revisions into `0001_initial`,
retaining their table definitions, aggregate-version machinery, and domain/history
guards. The operator confirmed on 2026-10-05 that only disposable development/test
databases exist and no staging database has been provisioned. Original revisions remain
in Git history, not the active Alembic chain. `migration_id_mappings.source_updated_at`
retains original source update provenance independently of database-controlled aggregate
update instants.

An old disposable database must be explicitly recreated, never stamped to the
new revision. An unexpected retained database is a stop-and-investigate case;
preserve its data and old revision chain. This implementation recreates only
synthetic UUID test databases through the existing test fixture. It does not
reset an operator database or silently upgrade an old revision identifier.

Repository code alone need not introduce a revision. Any later schema/constraint
change uses a new forward revision, coordinated to keep one Alembic head.
`0020_book_currency` follows admission revision `0019_beta_admission`, permits
USD/CAD on new entities and prevents updating currency on any existing entity.
It preserves existing rows/default USD, memberships, source archives and identity.
Deploy a currency-aware compatible image after the explicit migration; rollback
must retain CAD interpretation and admission enforcement. Do not downgrade the
schema, relabel CAD rows or use an older USD-only export validator.
The deployed baseline and its retained forward history remain immutable.

See [the migration operator guide](../accounting/SHEETS_MIGRATION.md) for dry-run,
explicit loading, extension contracts, restart, and the persistent staging checkpoint.
whole-profile acceptance and authority cutover remain separate.

## Complete entity portability export

The operator command exports one explicitly selected PostgreSQL entity through an
application-owned capability, without Sheets access. Its independently versioned
canonical format includes all supported bookkeeping domains, retained provenance,
historical references and external document metadata. See [Complete PostgreSQL
bookkeeping export](../accounting/EXPORT.md) for the private artifact procedure,
format/validation contract, customer-delivery procedure and authority cutover's
verified-export activation evidence. It does not switch the active ledger or replace
managed database recovery's managed backup/PITR recovery requirements.

Forward revision `0011_control_plane_join` marks the integrated head after
`0009_external_connections` -> `0010_workspace_lifecycle` -> `0010_onboarding_routes`. Upgrade to
this single head from the supported connection or workspace revision; those
published revisions are unchanged.

## Beta program control plane

Forward revision `0019_beta_admission` adds closed-by-default program limits,
per-business named approval/reservations, expiring independent operator grants
and append-only event history. It preserves canonical bookkeeping/export v1,
existing memberships and separately operated internal owners. Follow
[beta admission](../operations/BETA_ADMISSION.md) for inventory, synthetic staging,
operator commands, compatible rollback and the separate release handoff.

## Customer lifecycle control plane

Forward revision `0021_customer_lifecycle` follows `0020_book_currency`. It adds
customer requests and immutable transition events without changing the 26-collection
financial export. Confirmed business closure fences canonical SQL writes and affects
named discovery/capabilities; no ledger is deleted and no provider is contacted by
the migration. Existing businesses stay active until an Owner explicitly confirms
closure. Back up the full control plane with the ledger; restoring bookkeeping v1
alone cannot restore closure/access decisions. See [customer lifecycle](../operations/CUSTOMER_LIFECYCLE.md)
for exact access windows, support recovery, policy proposals and manual approval.

## Google Drive recovery binding

Forward revision `0022_drive_recovery_binding` adds nullable recovery metadata to
the existing external connection row. The migration preserves connection references
and performs no provider or custody calls. Successful customer consent records the
original Drive account fingerprint and managed folders. Preserve these rows in full
database backups. See [Google Drive recovery](../identity/GOOGLE_DRIVE.md#recover-after-loss-of-credential-custody)
for enrolling existing connections and recovering from lost ciphertext.
