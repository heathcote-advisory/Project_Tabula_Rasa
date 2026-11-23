# Accounting periods and corrections

Monthly period locks and linked reversing journals extend existing PostgreSQL
posting, loaded baselines and atomic audit history. This is an opt-in control;
adoption requires an explicit operator decision. The controls use the existing
PostgreSQL journal engine. Recovery, retention and release approval remain
independent requirements.

## Implemented minimal policy

An accounting period is one calendar month in the selected entity's bookkeeping
dates, identified by its first day. SQL dates retain calendar meaning; UTC timestamps
describe audit instants. Closing October means October 1–31, inclusive. There is no
multi-stage approval workflow, automatic year close, earnings transfer or fiscal
calendar implementation.

| Operation while an affected month is closed | Policy |
| --- | --- |
| Create, backdate, edit, move into/out of, or delete a transaction/journal | Rejected atomically, including both old and new dates. |
| Change posting lines, entry/source tags or recurring/depreciation occurrence relationships | Same closed-date check; no direct detail-table escape. |
| Mixed-use source/projection create/edit/delete; recurring generation; depreciation posting; CSV/QuickBooks transaction imports | Same database boundary. An affected supported operation rolls back all its source, canonical posting and audit effects. Recurring commits each occurrence independently, preserving earlier confirmed occurrences and isolating a failed schedule. |
| Change an asset with closed depreciation history, including overrides/tags | Rejected. Existing domain restrictions remain stricter where posted history already locks terms/overrides. |
| Change account opening balance, type, classification or hierarchy; create a nonzero opening balance | Rejected while any month is closed. Zero-balance new accounts, labels and activation state remain available. Category kind/hierarchy changes affecting closed postings are also rejected. |
| Edit/pause/end a recurring template | Allowed under existing future-occurrence rules; already posted occurrences do not change. Missing closed occurrences require an authorized unlock, then retry. They are never silently skipped or shifted into an open month. |
| Mileage and receipt attachment/link/unlink; reference labels | Existing rules continue. Mileage remains separate from financial postings; receipt bytes are never deleted as an accounting rollback. |

Closed periods have no per-write override. An Owner may unlock with a reason, make the
supported domain correction and close again with a fresh loaded period version. Each
state/adoption change also appends an immutable `accounting_period_actions` record,
preserving its reason, previous/new state and version after later close/unlock actions.
It commits with the state and atomic audit history event; actor/correlation and
before/after evidence share that operation. Unlocking does not relax existing
Source-ownership, asset-history, stale-baseline or duplicate rules.

## Authorized reversals

Owner, Admin and Accountant may reverse an ordinary Simple Ledger entry or journal.
The command requires its loaded baseline, a new stable correction ID, a distinct
new reversal journal ID, an open-period date and a nonblank reason (maximum 1,000
characters). It creates the canonical journal through the existing journal writer,
swapping Debit/Credit on every original posting with exact amounts and targets.
Original entries/postings are preserved. There is one reversal per original entry;
a further correcting effect must have its own explicit linked history.

The immutable `entry_corrections` relation stores the reason and both identities.
The original and reversing entry cannot subsequently be rewritten/deleted, even
after unlock or disabling period enforcement. Replaying identical IDs/date/reason
confirms the existing result without another journal or event; differing payloads
conflict. Source-owned mixed-use/depreciation entries require the authorized unlock
and their existing source workflow; this endpoint cannot bypass source accounting.
A replacement, when needed, is a separate ordinary entry under existing validation.

Reversal journal, relationship and their atomic audit history events commit together.
Event actor and correlation come from server identity. Before/after posting summaries
show the effect; the relationship identifies the original. Reasons remain in the
restricted relation/action-history tables and are excluded from ordinary logs and
minimized audit summaries. No historical actor or pre-activation event is fabricated.

## API and security boundary

All routes use server authorization's fresh membership/entity resolver before service
composition, normal Origin/CSRF checks and safe errors. Missing/foreign entities have
uniform authorization denials. The database revalidates privileged role/entity authority
in the mutation transaction; worker/system bookkeeping identities cannot close or
unlock. Explicitly provisioned, entity-bound operator contexts may perform approved
maintenance; tenant roles do not provision operators.

| Route relative to `/api/v1/entities/{entity_id}` | Authority / request |
| --- | --- |
| `GET /accounting-periods` | Ordinary read; returns enabled and up to 1,200 known months (newest first), state/version only. Absent months are open, version 0. |
| `POST /accounting-periods/close` | Owner/Admin; `period_start`, `expected_version`, `reason`. |
| `POST /accounting-periods/unlock` | Owner only; same command fields. |
| `POST /entries/{record_id}/reverse` | Owner/Admin/Accountant; `baseline`, `correction_id`, `reversal_entry_id`, `txn_date`, `reason`. |

Period changes compare the loaded version (0 for an absent month). An identical
repeated action/reason is a no-op; a changed stale command conflicts. Close/unlock
and activation take an entity-specific exclusive transaction advisory lock;
financial mutations take its shared counterpart at the persisted boundary.
Under the runtime's READ COMMITTED writes, an overlapping mutation either commits
before close or sees the closed month and aborts. Independent entity operations
remain isolated. PostgreSQL deadlock/serialization failures remain safe conflicts.

Locked writes return HTTP 409 `period_closed` with guidance to reverse in an open
month or ask the Owner to unlock. Other correction/policy conflicts use the normal
409 envelope; role denial is 403. Submitted reasons, SQL and ledger contents never
enter errors. No broad period-management UI is introduced in this slice.

## Explicit adoption and reversible operational switch

Apply forward migration `0013_accounting_controls` with the separate migration
owner. Existing entities start with enforcement off and no closed months. An
approved operator, exact environment/entity and recovery checkpoint are required
before adopting a real ledger. With protected database configuration and the
operator identity supplied explicitly:

```powershell
python -m tabula_rasa.infrastructure.postgres.job_cli period-enable --entity SYNTHETIC_ENTITY --service-id staging-operator --reason "Approved synthetic rehearsal" --allow-mutations
python -m tabula_rasa.infrastructure.postgres.job_cli period-status --entity SYNTHETIC_ENTITY --service-id staging-operator
```

Verify the enabled state, rehearse close/rejected write/unlock/correction/reclose
using the authorized API and synthetic records, inspect restricted audit events,
and record the selected role policy and operator evidence before real adoption.
No hosted secrets, operator roles or production controls are provisioned here.

To roll back adoption, stop affected commands/workers and explicitly run
`period-disable` with the same entity/service/reason/mutation arguments. Verify
`GET /accounting-periods` reports disabled before resuming. This is an audited
policy change: it retains all period states, correction relationships and events.
Existing uncorrected entries resume legacy mutation behavior; corrected history
remains immutable. Re-enabling restores enforcement of retained closed months.
Never delete control rows or downgrade/rewrite published migrations.

Use a narrow runtime DB role with required canonical/control DML, no ownership,
DDL, TRUNCATE, trigger bypass or BYPASSRLS. The append-only action table is written
only through its policy/state capture trigger; grant its SELECT only to the
separate audit inspector, using the selected-entity RLS policy. Audit INSERT/UPDATE/DELETE remains
forbidden under the [existing audit contract](../operations/JOBS_AND_AUDIT.md). These
are application-mediated guarantees, not protection against an administrator
who bypasses triggers or fabricates server context.

Managed dump/PITR/NAS recovery must retain policy, periods, correction relationships,
audit history and their functions/triggers. The v1 bookkeeping export preserves
financial entries/postings, including reversing journals, but excludes these restricted
control/history records and reasons. It cannot by itself restore lock or correction
immutability policy. Record this boundary in recovery evidence; do not describe a v1
snapshot restore as complete operational recovery. operational readiness verification
owns audit/control-aware restoration and customer data export owns authorized
audit/customer export.

## Validation

`tests/postgres/test_periods.py` exercises ordinary/generated/import paths,
rollback, exact reversing effects and replay, same-month close/write races in
both orders, API role/cross-entity denials and SQL authority checks. Recurring
job tests exercise a closed mixed-use occurrence without a partial source pair.
Existing canonical, concurrency, audit and export checks remain applicable.
CI's current-revision **Full test suite** remains the merge gate.
