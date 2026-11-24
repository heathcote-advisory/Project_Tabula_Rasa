# Recurring, mixed-use and asset workflows

The browser uses existing recurring, mixed-use and fixed-asset services through
protected forms and explicit commands. This guide covers source ownership,
occurrences, depreciation, overrides and retirement. The
[core bookkeeping guide](BOOKKEEPING.md) owns ordinary entries and reports;
[release validation](RELEASE_VALIDATION.md) defines the selected release scope.
Availability of a route does not authorize live cutover or scheduled generation.

## Recurring expenses

Transactions links to **Recurring expenses**. The schedule list shows cadence,
next due date, latest current occurrence, status, attention and stable identity.
Details show current occurrence IDs, dates, amounts and initial/generated roles,
including mixed-use source provenance.

**Add recurring expense** supports ordinary or mixed-use Expenses. Save invokes
the existing initial operation, committing the schedule and initial occurrence
together. Repeat every starts at one month; create identities and corrected
drafts survive failure. Templates contain managed references rather than receipts.
Schedule edits affect future occurrences; existing occurrences retain their
saved amounts and provenance. **Pause**, **Resume** and **End** require explicit
confirmation and the originally loaded baseline. End is permanent and retains
history, and remains independent of whether any generated occurrence exists.

**Delete** is a separate destructive action shown only when the schedule's sole
linked occurrence is its original initial expense/source. Active, Paused and Ended
schedules follow the same eligibility rule. Confirmation removes the template and
recurrence association, retaining the original entry/source, projection and receipts
as ordinary editable records. The service checks the loaded schedule baseline and
revalidates membership under the same entity/reference/root locks used by generation.
A generated occurrence that wins the race prevents deletion; deleting first prevents
a prepared generator from recreating the schedule. Missing-original and used schedules
cannot be deleted. Use End to stop future recurrence while preserving history.

**Generate due occurrences** is an explicit, confirmed command with a cutoff date. Its
response reports confirmed generated and attention counts. Repeating the action uses
existing stable schedule/date identities and does not duplicate occurrences. Listing
schedules, loading details, visiting other pages and loading the runtime never invoke
reconciliation. This technical command is not a durable scheduler itself. [Jobs
operations](../operations/JOBS_AND_AUDIT.md) owns recurring job execution's opt-in
durable authority: when enabled, this same explicit command queues bounded jobs and
reports queued schedules; the browser asks the user to refresh later. Timer/explicit
requests share the service/idempotency path. Cutoffs cannot exceed the approved zone's
current business date in worker mode. Existing synchronous generation remains available
when worker authority is disabled.

[Accounting periods](../accounting/PERIODS_AND_CORRECTIONS.md) defines the accounting
control service's independent opt-in month lock. Source, generated and import writes
encounter the same transactional boundary. Missing closed occurrences stop with
attention and durable job failure; an Owner unlock and eligible retry can recover
without partial source pairs.

## Mixed-use sources and projections

Transactions links to **Mixed-use expenses**. The source log exposes total,
business-use percentage, allocated amount, rationale and linked projection. Column
headers sort ascending/descending with numeric amount/percentage comparisons and a
visible active direction. Full descriptions remain visible.
Incomplete source/projection relationships remain visible for inspection with
editing/removal unavailable; normal commands do not silently repair them.

Create and edit use Expense references plus the allocation inputs. The live
**Ledger result** calls the existing service's Decimal allocation calculation;
the browser does not calculate financial results. Save creates or updates the
source and its existing projection as one source-owned operation. New sources
have distinct stable source/projection IDs. Protected deletion removes the pair,
subject to the service's reference rules. Generated sources show recurring
provenance. The generic ledger cannot mutate their projections independently.
Its derived rows link to Mixed-use expenses or Assets for source-owned actions;
mixed-use allocations are labeled **Mixed-use** in the ledger. The ledger Type
column also shows **Recurring** beside **Expense** for both initial and generated
ordinary occurrences and mixed-use projections, using their persisted schedule
link. Editing an occurrence memo or visible date does not remove that identity.

The commercial slice exposes source setup/edit/delete and recurring creation. mixed-use
source receipts adds **Receipts** on each source detail through the shared receipt
workflow, with the original source baseline required for linking/removing references.
Source and projection retain one receipt set; generic transaction receipt endpoints
refuse derived rows. Recurring templates do not own receipts; generated sources own
their own set. See [workflow ports](IMPORTS_EXPORTS_AND_INTEGRATIONS.md) for recovery,
conflicts and validation. Independent source copy and ordinary Expense-to-recurring
conversion are also available under recurring conversion and source copying; their
preparation, baseline and recovery contracts are documented in the same workflow guide.

## Assets and depreciation

Assets offers a register, **Add asset**, detail and protected editing. Asset types
come from the service's canonical enumeration. Terms include acquisition/start
dates, cost, salvage, whole-quarter life, posting accounts and optional managed
references and acquisition linkage. Only straight-line depreciation is supported.

Beta-enrolled businesses can maintain their asset register and view depreciation
schedules and saved history. Posting depreciation and saving or removing period
overrides are unavailable for those businesses in the browser and API. The
restriction follows the selected business across users and remains in effect
when its beta admission is suspended or closed.

The server-calculated schedule displays amounts, cumulative depreciation, book
value, override reasons, Posted/Pending and durable ledger entry IDs. Viewing the
schedule never posts. **Post due depreciation** requires a cutoff and explicit
confirmation, returns the committed count/IDs and never retries automatically.
PostgreSQL commits all requested periods atomically; a later-period failure
leaves no partial posting. Repeating a confirmed action after a fresh load skips
already-posted periods.

Before posting, due/past period overrides require an amount and reason. The API
caps the requested as-of date at the server's current date so a browser clock
cannot make a future quarter eligible. Removing
the final override restores editable schedule terms. Saved overrides or postings
prevent permanent deletion; posted history locks terms, overrides and posting
accounts. Descriptive fields remain editable. Retirement requires confirmation,
preserves history and cannot precede posted periods. The service enforces these
rules even for callers that bypass disabled browser controls. Posted entries
retain depreciation provenance and cannot be independently rewritten by the
ordinary ledger editor.

## Protection and ownership

All reads and commands use server-resolved entity context. Unsafe requests retain
capability, Origin and CSRF checks. Edits, deletes, transitions, overrides and
posting submit the loaded baseline; missing/stale tokens fail without mutations.
Conflicts keep drafts and offer explicit Keep draft or Discard draft and reload.
Each detail visit waits for its fresh read before opening an editor, including
when cached data exists. Later background read failures preserve the loaded draft.
Commands require saved main-form changes before confirmation. A completed command
closes that editor's mutation surface; return to the list to load updated metadata.
Confirmed responses establish success. Failures retain drafts and invalidate the
affected private caches, with no automatic browser retry or success readback.

Reuse: existing application services and PostgreSQL repositories own rules and
mutations; `CreateEditor`, `ProtectedEditor`, fields, confirmations and scoped
query invalidation own form behavior. `ProtectedEditor` now also owns confirmed
source commands. Mileage and secondary detail routes share the fresh-read gate.
Read-only detail views reuse the same field presentation without
mutation controls. Asset detail reads assemble source/history together and use a
shared domain schedule annotation, also used by the internal renderer. New code
owns transport allowlists, responsive workflow presentation and representative
integration scenarios; it introduces no second accounting or retry owner.

## Validation

After building `web`, run affected checks from the repository root against a
disposable PostgreSQL database with `TABULA_RASA_TEST_DATABASE_URL` configured:

```powershell
python -m pytest tests/postgres/test_http_secondary.py tests/postgres/test_browser_secondary.py
```

Native browser coverage uses the compiled app, real API/services/PostgreSQL,
desktop/mobile Chromium, Firefox and WebKit, and independently loaded stale forms.
HTTP/repository coverage includes source/projection parity, atomic failure,
provenance, duplicate prevention, required baselines and two-entity isolation.
The existing [repository CI gate](../DEVELOPMENT.md#authoritative-ci-and-required-check)
remains authoritative for the current PR candidate; these local checks do not
claim staging acceptance or hosted review completion.
