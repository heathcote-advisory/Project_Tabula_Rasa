# Durable jobs and bookkeeping history

Durable jobs and atomic audit history extend the shared PostgreSQL runtime. Page loads
do not schedule work or perform recurring mutations. Forward-only migration
`0012_operations` extends the retained `0011_control_plane_join` chain. Run the normal
explicit migration command before deploying this revision. Schedule an operator
migration window: the migration drains and blocks audited table writers before recording
the activation watermark, and holds those locks through baseline capture and trigger
installation. Reads may continue; pause old application writers until the compatible
audited runtime is deployed. The commercial HTTP runtime starts and serves page loads
without scheduler or recurring mutations. Legacy Streamlit initialization/page reconciliation is retired. recurring job execution's opt-in worker
integration is documented below; the accounting control service's independent
[period/correction policy](../accounting/PERIODS_AND_CORRECTIONS.md) remains off until
approved adoption.

## Reuse and ownership

The existing `Database.transaction`, repository operation, aggregate locks,
loaded baselines and canonical accounting writers remain the mutation owners.
Transaction, journal, reference, mileage, recurring, asset, document and import
services retain their existing validation and business rules. SQL capture uses
those canonical tables; it adds no accounting engine or Sheets audit worksheet.
HTTP service composition extends the existing server-authorized context and
request ID. Workspace bootstrap supplies its existing named actor.

The only new state machine owns queue execution and attempts. It does not duplicate
recurring occurrence state, depreciation calculation, document recovery, import
deduplication or remote-write reconciliation. recurring job execution integrates
independently committed recurring occurrences; accounting periods and corrections adds
one shared database period gate and reuses canonical journal writes for corrections.

## Jobs: supported effect boundary

`JobRegistry.register` is the single trusted source-code registration boundary.
A handler receives the shared database and an immutable `JobExecution` containing
the server-selected entity, job ID, stable idempotency key and worker service ID,
plus optional typed target ID, business date and generation-authority revision.
It invokes the existing services with that entity and stable domain record IDs.
The queue stores no arbitrary payload, credentials, Python module name or browser
session. Job rows cannot choose handler code or service identity.

The default supports **short, atomic, same-database effects**: the worker opens
one transaction and participating repositories join it. A second explicit
registration option, `independent_idempotent=True`, supports existing services
that already guarantee stable identities and safe replay after partial commits.
That service receives a separate one-connection participant against the exact
same database while the worker retains the job-row execution fence. Its existing
outer transaction boundaries stay intact. Participant statements have the same
30-second SQL timeout as the atomic worker. A timeout
rolls back the current supported operation; confirmed earlier independent effects
remain for stable-identity retry. Each committed service mutation keeps
its same-transaction audit event. A failed job may therefore have committed
partial effects, and replay must confirm them through the existing service's
idempotency rules. The scheduler does not simulate rollback or invent those rules.

External provider writes, browser impersonation and handlers that swallow failed
operations are outside both contracts. A service requiring an independent outer
transaction must explicitly select the idempotent option. Native tests prove
`RecurringService.reconcile` works through it after a failure following committed
occurrences. recurring job execution's registered handlers below own the budget,
due-schedule selection and classification of attention/failure results; returning a
domain failure as ordinary success is not a valid handler. Future document/import/export
delivery must declare its stable-effect contract before registering.

`job_definitions` stores interval, next due time and pause state. `jobs` is the
durable run, uniquely keyed by entity and idempotency key. `job_attempts` stores
each claim, worker identity, start/finish and fixed outcome code. There is no
additional Redis broker, scheduler daemon or application transaction manager.

1. A bounded scheduler tick locks due definitions with `SKIP LOCKED` and atomically
   materializes one job per definition's due slot. After downtime it coalesces
   missed ticks into one catchup invocation and advances to the next interval.
   A workload owns actual domain catchup, rather than the scheduler inventing
   historical bookkeeping occurrences.
2. A worker claims one due run using `FOR UPDATE SKIP LOCKED`, commits its lease
   and records an attempt. Default lease is 60 seconds, configurable 1â€“3,600.
3. Execution locks that run and checks the exact unexpired lease. That transaction
   lock is the exclusivity fence, including after the wall-clock lease expires.
   Reclaimers skip a live execution. Statement timeouts are 30 seconds. Atomic
   execution also has a 30-second idle-in-transaction timeout; independently
   committing services disable that idle timeout on their orchestration connection
   so its fence survives participant work. Handlers must be bounded and must not
   do external I/O.
4. For atomic handlers, effects, audit events, successful attempt and job success
   commit together. For independently committing services, each effect/event pair
   commits through that service, and job success is recorded only after it returns
   successfully; stable domain identities prevent duplicate effects after a crash.
   Repeated/obsolete claims cannot repeat committed atomic effects.
   If the commit response is lost, one fresh locked status read accepts committed
   success; otherwise it records a safe retry/failure. A database outage can leave
   a run unconfirmed until lease recovery; it never justifies a blind external
   side effect or an unbounded readback loop.
5. Process death before execution leaves an expiring claim; death during atomic
   execution rolls back its effects. Independently committed effects remain for
   idempotent service confirmation. A later worker records `lease_expired` and reclaims,
   subject to the original attempt budget. No heartbeat thread is required while
   the transaction lock is held.

Only sanitized database unavailable/exhausted/concurrent-write failures retry automatically.
Backoff is `min(300, 2 ** attempts)` seconds; default attempt budget is three,
with a hard maximum of ten. Domain/validation errors, unknown handlers and other
exceptions become `poison` in `dead` state. They do not block healthy runs.
Diagnostics expose fixed codes and operational IDs/times, never exception text,
idempotency keys, ledger values or credentials. Inspection includes both run
status and attempt history. Dead runs remain available for operator inspection.

Pause prevents definition materialization and queued/reclaimed claims; an already
executing transaction finishes. Resume preserves the next due time for catchup.
Cancel fences a queued or abandoned claim, records a cancelled attempt, and waits
for an executing transaction; it cannot undo success. After fixing the cause,
explicit operator retry grants one additional attempt to a dead run, up to ten,
preserving identity/history. Do not delete and recreate a job to bypass deduplication.

## Operator entrypoints and manual hosted checkpoint

Use the isolated environment and a separately approved target. Database settings
and secret handling follow [PostgreSQL operations](../database/POSTGRESQL.md). The CLI never
prints database URLs and never creates a schema or selects an entity implicitly.
Read diagnostics with:

```powershell
python -m tabula_rasa.infrastructure.postgres.job_cli inspect --entity SYNTHETIC_ENTITY --service-id staging-operator
python -m tabula_rasa.infrastructure.postgres.job_cli attempts --entity SYNTHETIC_ENTITY --service-id staging-operator --id JOB_UUID
```

`ledger.probe` reads the
existing ledger service without producing accounting changes; it exercises durable
queue execution. After approving the exact synthetic database/entity, an operator
may use these explicit mutations for staging:

```powershell
python -m tabula_rasa.infrastructure.postgres.job_cli enqueue-probe --entity SYNTHETIC_ENTITY --service-id staging-worker --id probe-1 --allow-mutations
python -m tabula_rasa.infrastructure.postgres.job_cli define-probe --entity SYNTHETIC_ENTITY --service-id staging-worker --id hourly-probe --interval-seconds 3600 --allow-mutations
python -m tabula_rasa.infrastructure.postgres.job_cli tick --entity SYNTHETIC_ENTITY --service-id staging-worker --budget 5 --allow-mutations
```

`pause`, `resume`, `cancel` and `retry` use the same explicit entity, service ID, `--id`
and mutation flag. Each invocation exits after a bounded budget (1â€“100 ticks). Exit
failure instructs inspection of durable status before retrying. The flag is an execution
guard, not proof of human approval or membership. No HTTP job-control route is added.
Future member-triggered creation must use the existing server authorization
capability/membership resolver before selecting entity/service; role membership alone
never provisions a platform worker.

### NAS recurring failure and missed-run notifications

The optional `scripts/nas_recurring_monitor.py` is an operator-only, read-only
adapter around the existing policy and durable queue records. It never schedules,
claims, retries or generates work. `deploy/nas/run_recurring_monitored.example.sh`
delegates generation to the existing bounded `run-recurring.sh tick`, then runs
the inspection in the same verified immutable worker image. The example refuses
an unconfigured image or missing mounted files before calling the runner. Keep
the task disabled until the exact installation and notification tests are accepted.

Install `scripts/operational_heartbeat.py` beside `nas_recurring_monitor.py` in
the protected `config/bin` directory. The wrapper mounts both files read-only
as siblings, including when the pinned worker image predates the shared helper.
Deploy the scripts and wrapper together; replacing the monitor alone is not a
compatible installation. The same payload-free sender is reused by
[coordinated backup health](NAS_BACKUP.md#coordinated-point-publication-and-health).

Supply a protected, metadata-only `recurring-monitor.json` with these fields:

```json
{
  "database_sha256": "<approved database identity SHA-256>",
  "database_role": "restricted_worker_role",
  "entity_ids": ["00000000-0000-4000-8000-000000000001"],
  "business_time_zone": "America/New_York"
}
```

Use the restricted worker connection, not an operator/schema login. The adapter
verifies the exact database identity and role before entity reads. It requires
worker authority in the configured business zone, an enabled hourly dispatcher,
no retained dead jobs, and a successful dispatcher within the configured freshness
window. The optional `max_age_minutes` field accepts an integer from 1 to 1440
and defaults to 90 when omitted. Queued work or expired running leases older
than that same window require attention too.
Counts cover all retained jobs, rather than only a recent diagnostic sample.
One unhealthy selected entity makes the overall signal fail; it does not prevent
the existing runner from processing other entities. These are operational health
checks, not a second recurrence engine or proof that every intended business
occurrence has been reconciled. Review legitimate due schedules and attention
results separately.

Inspection alone sends nothing. The explicit `--notify` flag uses
`TABULA_RASA_RECURRING_HEARTBEAT_URL` from a separate protected environment file.
For Better Stack use only its HTTPS `incidents.betterstack.com` heartbeat URL.
The adapter refuses redirects and sends no body, logs, ledger values, entity IDs,
database URL or credential material to the provider. It sends success only after
all entity checks pass and the existing runner exit is zero; otherwise it sends
`/fail`. An unconfirmed database read or notification is never successful health.
Container startup, NAS shutdown or network loss can prevent any signal, so the
independent provider must also detect missing signals.

Before installation, approve the provider/account, recipient, resource allowance,
exact script/image/binding, protected heartbeat-secret transfer and controlled
notification tests. A suitable hourly monitor expects a signal every hour with
30 minutes of grace; missing-signal detection is approximately 90 minutes after
the last successful signal, plus provider and delivery latency. Confirm the actual
selected provider settings rather than claiming an alert-time guarantee. Keep
the NAS polling cadence, freshness window and provider expectation aligned. For
an every-two-hours NAS task with 30 minutes of grace, add
`"max_age_minutes": 150` to the metadata and set the provider to two hours with
30 minutes of grace. The durable dispatcher definition remains hourly: the
existing queue catches up due work when the NAS next runs. This changes polling
latency, without promising hour/minute scheduling or contacting the web service.
Keep database settings and heartbeat secrets owner-readable only; mount the non-secret
script/configuration read-only. No new identity, Google, session or schema grants
are needed. The example retains the established NAS memory and non-root image
boundaries and does not request unsupported CPU/PID kernel limits.

Validate the inspection and failure predicate without production bookkeeping
mutations. Separately verify actual failure-email delivery, a missed-signal alert
and recovery using an explicitly approved controlled notification test. Never
poison a live business schedule or invent an entry for an alert test. New Better
Stack heartbeats remain Pending until the first signal; an unprimed or paused
monitor supplies no missing-run protection. Before enabling generation/timer,
verify the tested monitor is active and routed to the accepted recipient. Its
creation, a successful DSM exit or a local test alone does not prove delivery.

The read-only form is:

```sh
python scripts/nas_recurring_monitor.py --config /protected/recurring-monitor.json
```

For the pinned commercial worker image, mount the script and metadata file and
invoke it through `python -c 'import runpy; runpy.run_path("/monitor.py", run_name="__main__")'`
from the image's normal `/app` working directory, as the example does. This
preserves application import resolution without adding legacy UI/Sheets packages.
After fixing a reported cause, use the existing explicit job recovery procedure;
the monitor never cancels or recreates jobs to suppress an alert.

### Retired one-off staging probe

The dedicated staging ledger-read probe workflow and launcher are retired.
Normal queue/worker commands, recurring monitoring and sanitized job telemetry
remain supported. Retain the earlier probe's durable history and collector
acceptance evidence. A future job exercise requires its own explicit synthetic
target/window approval and the existing worker/incident procedures; source
cleanup does not run a worker, delete history or change installed NAS tasks.

The explicit `tick` command composes the shared sanitized telemetry sink using the
existing `TABULA_RASA_ENVIRONMENT`, image `TABULA_RASA_RELEASE_ID` and optional
`TABULA_RASA_TELEMETRY_*` settings. No collector settings selects console only.
Confirmed successful execution emits `job_run` / `job_execute` after commit; confirmed
terminal poison, exhausted transient retry and exhausted lease budgets emit fixed
`job_poison`, `job_transient` and `job_lease_expired` failure codes. Recurring handlers
execute through this same queue. Job type, target, schedule, business date, payload and
exception text are never telemetry fields. Job, entity and worker references use the
existing stage-specific HMAC policy; no key omits them. Empty ticks, obsolete claims,
cancellation and retryable attempts do not manufacture terminal outcomes. A lost success
response emits success only after durable readback confirms it; an unconfirmed result
emits neither success nor a fabricated failure. Durable attempt history remains
authoritative.

Telemetry failure never changes a committed result or repeats effects. Shutdown attempts
to drain queued events within the existing four-second bound; crashes, quota failures
and incomplete delivery can still lose diagnostics. No outbox or exactly-once event
guarantee is introduced. Configure terminal-failure alerts and explicit-success recovery
at the collector, verify actual hosted delivery, and retain missing-timer/manual
inspection ownership separately. Source changes alone do not establish a hosted
scheduler, notification or recovery acceptance.

| Stage | Selected execution ownership |
| --- | --- |
| Development | Explicit local CLI against disposable PostgreSQL; no hosted schedule. |
| Staging / restricted technical alpha | Manual bounded invocation first. If periodic execution is needed, an operator-approved OS/NAS timer invokes the CLI with a private staging URL and exact synthetic entity. A stopped timer delays work until catchup; this is not an uptime guarantee. |
| Commercial background execution | Evaluate a bounded Render cron invocation against accepted latency/capacity/recovery objectives. A paid always-on worker is optional if later workloads justify it. recurring job execution supplies the first real recurring handler; operational readiness verification supplies readiness evidence; external release and customer lifecycle and commercial recovery agreement retain launch/activation authority. |

Provider facts checked October 16, 2026: [Render Free](https://render.com/docs/free)
web services sleep after 15 idle minutes and do not offer free worker/cron types.
[Render cron](https://render.com/docs/cronjobs) uses UTC schedules, exits per
invocation and has a $1 monthly minimum per cron service plus metered compute.
An HTTP keepalive is not a scheduler. [Neon plans](https://neon.com/docs/introduction/plans)
list Free compute/storage quotas and five-minute suspension; frequent polling
can prevent suspension. Estimate usage using actual duration/frequency, event
growth and database capacity, and confirm the account's current limits before
selecting a cadence. No paid plan, always-on compute or provider scheduler is assumed.

**Manual setup/approval remains required:** approve the exact environment and
entity allowlist, responsible operator, release image/branch, handler inventory,
timer/cron owner and cadence, spend/compute/storage limits, protected secret
injection, narrow runtime DB role, missing-run/dead-run alert destination and
staging crash/retry drill. Install runtime dependencies, migrate with a separate
owner role, configure a low pool size and run the synthetic probe. Verify provider
run history and `inspect`/`attempts`, and test stopping/resuming the timer. Record
the evidence before enabling any real workload. Secrets belong in the operator's
protected environment/provider secret store, never commands, logs or Git.
This change provisions no scheduler, installs no hosted secrets and activates
no production writes.

## Recurring workload and authority switch

Forward migration `0013_accounting_controls` adds persisted recurring authority
and typed job target/business-date fields. Existing entities retain explicit
PostgreSQL reconciliation. Legacy Sheets/Streamlit session and page generation
are retired; startup and page loads never generate occurrences.
Commercial GET/startup remains read-only. `recurring.dispatch` and
`recurring.schedule` are registered beside the probe, using the existing recurring
service, schedule baseline guard, stable schedule/date occurrence identities and
atomic transaction or mixed-use source/projection writers. No schedule arithmetic
or second posting engine lives in the worker.

An approved operator can adopt one exact synthetic entity:

```powershell
python -m tabula_rasa.infrastructure.postgres.job_cli recurring-enable --entity SYNTHETIC_ENTITY --service-id staging-operator --time-zone America/New_York --allow-mutations
python -m tabula_rasa.infrastructure.postgres.job_cli recurring-status --entity SYNTHETIC_ENTITY --service-id staging-operator
python -m tabula_rasa.infrastructure.postgres.job_cli tick --entity SYNTHETIC_ENTITY --service-id staging-worker --budget 5 --allow-mutations
```

Enable persists the selected IANA business time zone and one hourly dispatcher
definition. It does not launch a process or provision a hosted timer. Run the
bounded CLI on an approved OS/NAS/provider cadence, with a separately selected
entity per invocation. The dispatcher uses its current UTC instant converted to
that business zone to choose the local calendar date. Recurrences remain date
granular: hourly polling adds no hour/minute scheduling promise. Spring-forward
skips no due date; repeated fall-back hours select the same occurrence identities.
Stop/resume and provider compute latency delay dispatch without changing due dates.

Each dispatch enqueues at most 100 pending schedule jobs, oldest due first. A
schedule job generates at most 100 missing occurrences through its persisted
business date, committing each supported occurrence atomically. Subsequent
dispatch continues from the domain's next due date. A missed hourly slot creates
one catch-up dispatcher job, not a job for every missed hour. Downtime preserves
overdue business dates and catches up in bounded batches after restart.

Explicit `POST /recurring/reconcile` retains server authorization write/Origin/CSRF
authorization. In worker mode it uses the exact same enqueue path as timer dispatch and
returns `queued_count`; the browser explains that generation is in the background. The
command cannot generate future business dates. In explicit mode its existing synchronous
result and supplied cutoff behavior remain available. Repeated requests share a job for
the same schedule baseline, next due date, cutoff and authority revision. Persisted and
in-flight revision checks fence an old business cutoff after a time-zone or authority
change. Stable occurrence identities suppress effects when different cutoff jobs
overlap. Schedule edits/pause/resume advance the baseline, so obsolete no-op jobs cannot
suppress newly eligible work.

Workers read current schedules/references before effects. Paused/ended/deleted schedules
safely produce no generation; existing history is retained. Edited templates apply under
the existing fresh schedule-baseline check. An intervening edit stops stale generation,
marks attention and leaves confirmed occurrences intact. A failing/locked schedule never
rolls back another schedule. Safe transient failures use bounded retry/backoff;
validation, period locks and other poison require inspection. Terminal jobs do not
consume the next dispatch's pending budget. `inspect` includes safe schedule
IDs/cutoff/status and `attempts` retains history, without ledger values or error text.
Schedule attention remains visible through existing authorized views; operational
observability owns wider alerting. Fix the cause, then use `retry --id JOB_UUID` when
eligible. Never delete/recreate jobs to bypass deduplication.

For rollback, stop the external timer, run `recurring-disable` with the same
explicit entity/service/time-zone/mutation arguments, verify persisted authority
is `explicit`, and resume the former explicit command path. The switch takes an
entity-specific exclusive transaction advisory lock; occurrence writes hold its
shared counterpart through commit. It drains an in-flight occurrence, then fences
further worker effects. Old queued/claimed recurring jobs become safe no-ops;
historical jobs/attempts/occurrences remain. Re-enabling advances the authority
revision, retains due dates and resumes catchup. Switching neither migrates the
Sheets ledger or changes the frozen source/recovery contract.

Before hosted activation, record the exact environment/entity allowlist, operator
identity, approved business zone, timer owner/cadence, compute/storage budget, narrow DB
grants, missing-run/dead-run inspection/alert destination, and synthetic
two-worker/restart/switch rollback evidence. Use the stage-specific checkpoint above.
platform operations and application transition operational readiness, external release
and customer lifecycle and commercial recovery agreement commercial activation and
operational readiness verification recovery acceptance remain separate. See [accounting
periods](../accounting/PERIODS_AND_CORRECTIONS.md) for the independent opt-in lock
policy; enabling recurring never enables it.

## Atomic audit contract

The guarantee covers **application-service-mediated PostgreSQL mutations after
activation**. Ledger and matching events commit in the same transaction: audit
failure aborts bookkeeping; bookkeeping failure leaves no phantom audit event.
Deferred capture reads final canonical state and coalesces intermediate/detail
version updates into one event per operation/entity/table/primary key. Replacing
postings produces logical updates, rather than misleading delete/create pairs.
An operation can legitimately contain many target events. No-op replay through
existing domain identities creates no new event. Reusing a committed operation
ID for a new change to the same target fails rather than silently dropping history.

| Field | Contract |
| --- | --- |
| `event_id`, `schema_version` | Stable generated UUID, event schema 1. |
| `operation_id` | New UoW ID by default; stable `job:UUID` for an atomic job. Independently committing services retain one ID per UoW and share the job correlation ID. Import record/batch identities remain the existing canonical identities. |
| `entity_id`, `organization_id` | Selected entity and its persisted organization when known; no invented organization for old internal entities. |
| `occurred_at`, `sequence` | UTC database instant and ordering key. Sequence gaps are normal after aborted transactions. |
| `actor_type`, `actor_id` | Named user ID from authorized HTTP context, explicit worker service ID, approved operator ID, or truthful `system/internal-application` for internal/shared-password paths. Never retrospectively attribute a historical human. |
| `correlation_id`, `source` | Server request ID and registered route operation, job ID/`job`, or explicit internal/operator source. No client-provided actor. |
| `action`, `target_type`, `target_key` | Create/update/delete of canonical table and typed primary-key components. Account deactivation/reactivation is an update to `active`; existing domain rules still decide allowed removals. |
| `before_summary`, `after_summary` | Allowlisted IDs, financial measures, classifications, dates/status/provenance; `_redacted` marks intentional minimization. Text-only corrections still advance version. |
| `batch_id` | Stable import batch/chunk where applicable; import events include source, committed count and first/last accepted source positions. |
| `after_digest`, `transaction_id` | Internal final-state reconciliation and same-UoW coalescing aids. They are not cryptographic tamper evidence or an independent historical source. |

Captured tables cover transaction/journal roots and postings; mileage; account,
category, payee and tag changes; relationship repoints during reference merges;
mixed-use records; recurring definitions/occurrences; assets/overrides/depreciation;
document metadata and receipt link/unlink; import batches/records and migration-run
administration, unit checkpoints and ID mappings. Free-text names, memos, references, addresses, filenames, provider
locators, private bytes and credentials are excluded. Receipts remain external;
unlink events do not delete bytes. Corrections/close/unlock follow the
[accounting-control contract](../accounting/PERIODS_AND_CORRECTIONS.md), including atomic reversing
journals and protected relationship/reason records. Future restore workflows
must join this transaction contract when implemented.

CSV/QuickBooks transaction and QuickBooks mileage imports already commit at most
1,000 source rows per atomic submission. Each committed batch, record, canonical
target and relationship has matching evidence. Retries reuse canonical IDs and
produce no duplicate history. A failed submission rolls back its ledger/events.
Larger files remain explicitly split submissions. `AuditReader.import_status`
accepts the caller's expected stable chunk IDs and reports complete, partial or
unconfirmed with committed counts and missing chunks. A failed/unknown chunk does
not erase earlier committed chunks or manufacture an all-or-nothing claim.
Retain the import plan/IDs; preview and inspect unconfirmed chunks before resuming.
An absent event cannot establish whether a submission was attempted; worker attempt
failures are separately visible in job history. No raw rejected contents are retained.

## Inspection, immutability, retention and recovery

`AuditReader(database, authorized_entity, capabilities=...)` requires the separate
`audit_inspect` platform policy. Ordinary bookkeeping read/export rights do not grant
it. `query` supports time range, organization, actor, action, source, target type/ID,
batch and operation filters, with bounded keyset pages. For consistent multi-page
inspection, keep calls within `reader.operation()`'s repeatable-read snapshot. A
sequence cursor alone is not a live change-feed guarantee: concurrent transactions can
commit in a different order. [Support operations](SUPPORT.md) implements privileged
support operations's restricted CLI/API, expiring scoped grants, redacted bounded
inspection and immutable administrative records. Its sequence upper bound prevents
inspection from chasing its own events; it does not promise a multi-request
transactional snapshot. customer data export owns permitted customer export. Ordinary
telemetry and canonical v1 exports exclude audit content and job tables.

Database triggers reject event update/delete/truncate and direct inserts.
Row-level read policies scope non-owner roles to a server-selected reader entity;
the read contract also includes an explicit entity predicate for owner connections.
Deploy with a separate migration/function-owner role and a runtime role that has
only required bookkeeping, job and scoped support DML; **no audit INSERT/UPDATE/
DELETE/TRUNCATE, DDL, schema CREATE, trigger bypass, table ownership or BYPASSRLS**.
Grant audit SELECT only to provisioned inspectors or the trusted API serving
separately provisioned, expiring [support grants](SUPPORT.md).
Direct recovery inspection also needs canonical and sequence SELECT for verification. Context
settings and capabilities are server infrastructure, never browser claims.
DB owners/superusers can bypass these controls, as PostgreSQL documents in its
[row-security model](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
This is append-only application history, not protection against a malicious DBA.

Direct old Sheets edits, pre-activation transactions and out-of-band administrator
SQL are outside the historical guarantee. The activation watermark and immutable
`audit_baseline` identify pre-existing records without invented create events or
actors. `verify()` checks live canonical rows against their latest event or that
baseline, including deleted targets, and emits counts/status only. It detects
missing/stale current-state evidence; it is not a complete history-chain proof.
`boundary()` returns the original activation instant and unavailable-history
marker for an entity or selected target. Later edits never remove that marker.
Attention timestamps are operational fields, not bookkeeping edits. The verifier
must preserve this exclusion when comparing current-state evidence.

Retain all audit/baseline/activation and job history by default; no expiry, pruning or
deletion API is installed. The [audit policy](AUDIT_POLICY.md) records the
owner-approved interim operator contract (2026-10-19), existing enforcement, manual hold
handling and unresolved owner/legal decisions. No in-place redaction or pseudonymization
and no customer audit export are authorized. Sensitive-field minimization happens before
storage. audit retention policy retains statutory/commercial review, any separately
approved privacy mechanism and its broader lifecycle scope.

Database dump/PITR recovery must retain these tables, functions, triggers, sequences,
policies and the original activation watermark. An older recovery point loses later
history along with later bookkeeping; an internally consistent old state does not prove
the missing tail never existed. Record the restore point, unavailable interval and
independently retained evidence; never reset the watermark or backfill fictional actors.
operational readiness verification owns full archive/PITR restoration acceptance; older
managed database recovery drills and the existing bookkeeping-only recovery verifier do
not establish audit history recovery. audit retention policy owns retention/legal hold
interpretation after disaster recovery.

The legacy Sheets audit mechanism is retired. Its atomic history, identity, domain/
import coverage, retry/concurrency, minimization, inspection and integrity objectives
map to this contract/tests; authorized UI/export, retention and real restore evidence
retain owners privileged support operations, customer data export, audit retention
policy and operational readiness verification. Closing that legacy route never certifies
those separate launch outcomes.

## Validation

Native tests in `tests/postgres/test_jobs.py` and `test_audit.py` cover competing
workers, abandoned claims, fencing, rollback, lost commit responses, retry budgets,
poison isolation, pause/resume, entity boundaries, named attribution, append-only
tamper attempts, RLS, pre-activation gaps, imports and current-state reconciliation.
`test_recurring_jobs.py` adds real recurring workload selection, bounded catchup,
time-zone/authority fencing, schedule changes and failure isolation;
`test_periods.py` covers monthly controls, corrections and close/write races.
Affected repository/HTTP/export tests continue to exercise existing success/failure
and accounting behavior. Use disposable PostgreSQL and synthetic records only;
CI's current-revision `Full test suite` remains the authoritative full-suite gate.
