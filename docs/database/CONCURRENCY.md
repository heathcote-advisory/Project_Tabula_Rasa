# PostgreSQL mutations and loaded-record baselines

The shared PostgreSQL runtime owns connections and transaction boundaries;
domain services retain normalization and bookkeeping decisions. Mutations carry
loaded baselines and validate fresh state at the write boundary. Supported commit
units and canonical relationships are defined by
[ADR 0001](../adr/0001-canonical-accounting-schema.md#operation-boundaries).

## Reuse and ownership

- Inspected `postgres/runtime.py`, the canonical migration/ADR, reference-data,
  transaction/journal, recurring, mixed-use, asset and mileage ports/services,
  domain preparation/history rules, and their Sheets adapters.
  The runtime already owns shared connections and outer commit/rollback;
  services/domain modules already own normalization and bookkeeping decisions.
- Reuse that runtime through `postgres/operations.py`; extend existing ports
  with optional `expected` inputs instead of adding parallel mutation APIs.
  Sheets snapshots reuse the existing fresh row, lock and uncertain-write paths.
- New logic comprises token transport, ordered aggregate locks, error
  translation, version triggers, and deferred relationship/history constraints.
  Recurrence, allocation and depreciation calculations keep their Python owners.
  These are concurrency and relational integrity mechanics,
  not a second domain workflow or generic CRUD framework. There is no new
  process-local lock or Sheets retry/reconciliation mechanism.

## Repository operation boundary

Compose all participating adapters with **the same `Database` instance**.
Use `repository_operation(database)` inside infrastructure for mutations and
`repository_operation(database, read_only=True)` for reporting snapshots.
Nested participating calls share the outer connection and cannot commit.
Repository code must not manually commit or create an independent connection
for participating payee/tag, source/projection or other coupled writes.
Services do not import connections, SQLAlchemy, Psycopg or these helpers.

Return a successful supported operation only after its outer context exits.
Callbacks run once, after fresh identity/baseline/reference validation.
Return nested mapped values only to the orchestrating outer operation, which
must not expose them as committed results before commit. Deferred checks can
fail at that exit. A caught nested failure poisons the operation; a later
outer exit fails rather than reporting success after rollback.

An occurrence creation or independent attention write uses
`repository_operation(database, require_outer=True)`. This refuses nesting in an all-run
transaction. Each committed occurrence can then enter `generated`; a failed occurrence
rolls back before attention writes or the next schedule. the recurring/mixed-use
repository's full recurring/mixed-use adapters implement the best-effort attention
policy; their native repository tests and the earlier contract probes run the real
`RecurringService` and `TransactionService`: a deferred SQL failure on the second
occurrence preserves the first, permits a healthy schedule to continue, and produces
only durable returned records. Attention failure cannot undo those commits; rerun fills
missing dates without duplicates. The small test probes implement only the exercised
ports and are not deployable adapters. the recurring/mixed-use repository's full
conformance tests exercise its actual source/accounting ports, including SQL failures on
mixed occurrence pairs.

Connection loss can leave commit status uncertain. The safe `unavailable`
result makes no rollback claim and does not retry. Domain adapters must classify
that outcome using stable operation/record identity before any safe resubmission;
no blind retry or unconditional successful-write readback is introduced here.

## Token and caller policy

[Browser baseline bridge](../browser/CONCURRENCY.md) now carries these tokens through
real browser load/edit/delete/merge requests and requires them at HTTP boundaries. Its
two-client native tests exercise the existing repository checks; the compatibility
policy below remains reserved for internal callers.

`application.baselines.RecordBaseline` encodes an opaque `v1` string containing entity,
aggregate kind/ID, immutable persisted incarnation, and integer version. It contains no
SQL/ORM values and grants no authorization. identity and tenancy still owns
Server-derived entity authorization. Tokens must be compared against the server's
selected entity and record, never used to select that entity.

Mapped PostgreSQL records return a `baseline` string from the **same snapshot**
as the loaded aggregate. `baseline_for(key, row)` builds it from infrastructure
metadata. `MileageTrip.baseline` carries the same optional metadata through
service normalization. Sheets records continue to return their existing shapes.

An edit/delete request carries the loaded baseline separately from changes:

```python
service.update(record_id, expected={"baseline": loaded_record["baseline"]}, name="New name")
```

Use `guarded_mutation(database, selected_entity, keys, expected=tokens)` for an
edit/delete/bulk operation. It starts or joins the shared transaction, validates
that every key belongs to the server-selected entity, locks the entire affected
set, and checks **every** loaded token before yielding any write or inline
reference callback. An edit/delete guard requires a nonempty root set; adapters
handle an empty bulk selection as a no-op before invoking it. A partial bulk token
set fails before any callback. It
rejects extraneous expected keys rather than silently ignoring them. `tokens`
maps each `AggregateKey` to its loaded baseline string; adapters extract it from
the neutral `expected` inputs. Domain/reference validation remains in the
adapter and its build callback after this guard.

The lower-level `lock_aggregates` and `check_baseline` remain available for
specialized operations that enforce the same ordering and complete set. A
mismatched entity, kind, ID,
incarnation or version raises `StorageConflictError(code="stale")`. Absence
raises `StorageValidationError(code="baseline_required")`; malformed tokens
fail validation. A baseline from before delete/recreate remains stale even if
the domain ID and version are reused. Do not reread on submit to manufacture
a new baseline: it must describe the record the user loaded.

| Caller / port | Policy |
| --- | --- |
| browser and API workflows supported commercial interactive edit/delete/bulk/merge | Require the loaded token, or a complete normalized expected snapshot enforced by the adapter under the same locks. Propagate it through load response, form state and mutation request separately from editable fields. |
| Account update/rename/deactivate; Category update/delete (reference repositories) | Additive optional `expected` transport implemented. No account-delete port is added. Sheets accepts complete loaded snapshots. |
| Mileage edit/delete (mileage repository) | Additive optional `expected` transport and returned token metadata implemented. Sheets accepts complete loaded trip snapshots. |
| Payee/Tag/Transaction optional `expected` paths (reference repositories and transaction and journal repositories) | Existing transport retained; commercial composition/callers must require their loaded baseline. Omission remains legacy/internal only. |
| Journal edit/delete (transaction and journal repositories) | Existing required `expected` input retained. PostgreSQL maps the entry token or complete normalized entry/posting snapshot. |
| Other owner/source ports (recurring and mixed-use repositories, asset repository, document metadata repository and ledger setup service) | Apply the same aggregate convention in their adapters and extend typed metadata where their implemented read models need it. |
| Legacy/internal calls without `expected` | Preserve fresh locks, domain/reference checks and existing results; do not claim stale-form detection. Services omit the newly added keyword for this calling path to preserve older internal ports. |

The guarded boundary requires baselines by default. Only an explicitly chosen
legacy/internal adapter policy uses `require_baselines=False`; supplied tokens
are still checked and fresh locking is still mandatory. This switch does not
turn a commercial request with a missing token into an internal request.

For existing full snapshots, the domain adapter compares normalized persisted fields and
relationships, excluding derived display labels/totals/next-due and attention fields. A
partial expected record is insufficient for a commercial guard. This foundation does not
implement SQL/domain snapshot projections; reference repositories and ledger setup
service own those mappings. The token path covers database metadata without embedding
SQL columns in services.

The new Sheets account comparison includes ID, name, type, classification,
opening balance, active, creation timestamp and parent ID. Category comparison
includes ID, name, kind and parent ID. Mileage comparison includes every stored
trip field, including sorted tag IDs and creation timestamp, excluding resolved
tag names. Comparisons normalize decimal and boolean representations. They run
against the existing fresh row while holding the workbook's existing lock.
Frozen workbook format 13 retains its existing columns and optional snapshot
semantics.
Snapshot equality cannot detect an edit/revert or recreation with identical
persisted values; PostgreSQL incarnation/version tokens provide that protection.

## Locking and version advancement

Lock the complete affected root set before details and callbacks. The helper
orders keys by entity, infrastructure table name and ID, independently of client
selection order. Hierarchy mutations first acquire the existing database entity
guard, matching the canonical hierarchy triggers. Include merge sources/targets,
mixed owners/projections and asset roots when an operation can affect them.
Do not discover additional earlier-order roots after already taking later locks;
domain adapters must construct their supported operation's set deliberately.
These are database row locks, not authorization or process-local workbook locks.
See PostgreSQL's [row locking and deadlock guidance](https://www.postgresql.org/docs/current/explicit-locking.html).

Lock existing roots, not absent create IDs. Prepare stable new IDs/incarnations
before creation and let unique constraints arbitrate collisions. Use constraints
for uniqueness and entity-qualified references. When a supported domain check
depends on a reference's active/type/classification fields staying unchanged
until commit, the adapter must also take an appropriate database reference lock
in its consistent operation order. `FOR SHARE` protects those field reads;
`FOR KEY SHARE` only protects keys/deletion and is insufficient for that check.
Do not replace these targeted checks with a whole-ledger lock or graph snapshot.

The consolidated `0001_initial` baseline includes aggregate-version enforcement:

- Business-field root updates advance `version` and `updated_at`; no-op updates
  do not. Explicit counter decreases/jumps are rejected. Attention code/time
  changes update the schedule timestamp without advancing its editable baseline.
- Posting, entry-tag and document-link changes advance their entry; changes to
  owned entries propagate to mixed sources/assets. Source, schedule, asset and
  mileage tag joins advance their owners. Asset overrides/events advance assets.
- Document links advance document and target baselines; import attribution
  advances batch and entry/mileage targets. Relationship repointing invalidates
  both old and new owners. Reference display-name changes advance only that
  reference, not every referring accounting record.

A counter can advance multiple times during one operation, including creation.
Consumers require equality, not a particular increment or initial counter.
Build the returned baseline after all participating detail writes, using the
same transaction; a root `RETURNING` value captured before details is obsolete.
This is an in-transaction mapping read, not post-success verification. Counters
and changes roll back together. Explicit baseline downgrade removes the schema
and remains destructive; the operator gate and recovery requirements still apply.

## Relational integrity and saved history

The consolidated `0001_initial` baseline includes specialized integrity/history
guards without implementing domain calculations in SQL.
Deferred checks inspect the final shape of affected roots under row locks:

- A complete mixed source has its expected same-entity Expense projection,
  matching date, account/category amounts, payee, reference, deterministic
  allocation description and tag set. A missing or mismatched state must
  accurately describe actual rows; it never excuses an invalid accounting
  entry. Both source/detail edits and referenced category changes recheck it.
- Recurring targets match their schedule's immutable source kind, use ordinary
  Expense entries or complete mixed pairs, and exclude depreciation. Current
  occurrence provenance cannot be repointed. Removing a current association
  remains supported and advances the target's baseline; template edits do not
  rewrite past occurrence values. Attention stays outside the template token.
- Populated asset posting settings retain their Expense/Other-Asset types;
  acquisition targets must be simple entries and override sequences fit the
  asset life. Events index entries owned by that asset. Posted events and their
  entry/posting/tag facts are immutable, including zero-valued quarters.
  Saved history locks the existing domain schedule terms; posted history also
  locks posting targets. Descriptive asset edits and valid later retirement
  remain supported. Retirement cannot precede a saved event. Override changes
  lock the asset before checking history, including a posting that committed
  while the writer waited. Earlier overrides remain locked by later ones.
- Receipt links use ordinary simple entries or the logical mixed source;
  journals and depreciation entries cannot receive links. Import entry targets
  must be simple. Entity-qualified FKs continue to apply to every relationship.

The checks target affected relationships, not repeated whole-ledger snapshots.
Adapters still own active-reference validation, normalized allocation/schedule
calculations, generated identities, receipt limits and supported read mappings.
They must lock the complete affected set before running their domain callbacks.

Upgrade performs a one-time validation of existing affected roots and link
targets. Incompatible rows cause the entire migration to roll back, retaining
the prior revision/data; the operator must inspect and correct the declared
state through the supported inactive-data workflow before retrying. There is
no automatic history repair or startup conversion. Explicit downgrade removes
these new guards and occurrence version triggers while preserving data and
existing aggregate counters; normal runtime never invokes downgrade.

## Safe failures and remaining acceptance

`repository_operation` translates raw/nested/deferred provider failures into
existing neutral errors, with a stable `code` and no provider exception context:

| Code | Application exception |
| --- | --- |
| `duplicate`, `concurrent_write`, `stale` | `StorageConflictError` |
| `reference`, `invariant`, `required`, `read_only`, baseline validation | `StorageValidationError` |
| `unavailable`, `operation_aborted`, `boundary_mismatch`, other infrastructure failure | `StorageError` |
| Missing selected-entity aggregate | `StorageNotFoundError(code="not_found")` |

Provider messages, SQL, parameters, constraint DETAIL and private record values
are never interpolated into these errors. Domain repositories can add safe
domain-specific guidance without retaining the provider error.

Native tests cover independent loaded callers, a later stale update/delete,
an overlapping writer, incarnation/scope checks, versioned detail/link changes,
inline accounting atomicity, isolated occurrence commit units, migration
downgrade/reupgrade, and safe nested failures. Existing native catalog tests
verify entity-qualified FKs across all 26 tables. New native cases exercise
complete/inspectable pairs, forbidden history/target changes, secondary
cross-entity references, a partial/stale bulk token set before callbacks, real
service inline-reference commit/rollback, real recurring service reconciliation,
and migration validation/refusal against existing data. Sheets tests cover optional
baselines, no-baseline compatibility, failure before inline tags, normalization
and two-workbook isolation.

The shared guards and specialized relational enforcement apply to the implemented
reference/accounting, reporting, recurring/mixed-use/assets, mileage, document
and import adapters. Every mutation adapter must enforce its supported domain,
entity/reference and loaded-baseline invariants at the actual boundary; transport
propagation follows [Commercial concurrency](../browser/CONCURRENCY.md).
Reporting read models do not acquire mutation baselines.

Completed cross-adapter/caller verification is recorded in [Cross-adapter concurrency evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/253) and [protected browser mutations](../browser/CONCURRENCY.md).
Selected live migration and authority acceptance remain with [live owner
cutover](../accounting/CUTOVER.md), while [commercial recovery
agreement](../operations/DATABASE_RECOVERY.md) retains commercial activation. A closed
shared-integrity ticket or synthetic contract probe does not authorize either operation.
