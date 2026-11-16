# Browser bookkeeping workflows

This guide describes reference setup, transactions, journals, the grouped ledger
and financial reports in the React/FastAPI application. Shared forms use
[loaded-record protection](CONCURRENCY.md); services own accounting invariants.
[Imports, exports and integrations](IMPORTS_EXPORTS_AND_INTEGRATIONS.md) covers
CSV/QuickBooks imports, copy/bulk actions and source-owned integrations.
The [cutover procedure](../accounting/CUTOVER.md) separately controls live authority.

## Business selection

Each book has one USD or CAD currency, initially selected at creation. Owner/Admin
can explicitly change its label in Business & members without converting amounts;
see [Business settings](../identity/WORKSPACES.md#business-currency-settings). The server-owned
business/session metadata supplies the unit beside the business and role, and
above monetary content on desktop and mobile. Entry forms, ledger and reports
use that unit without converting, rescaling or rounding stored values. Switching
businesses clears private drafts/queries and reloads currency with the selected
scope; permissions and totals stay independent. Financial download filenames
identify the currency. Transfers remain outside income/expenses and mileage
remains separate from financial transactions.

Transactions filters can be collapsed with **Hide filters** and restored with
**Show filters** using mouse, Enter or Space. The active scope remains visible;
collapsing does not change filters, results, sorting or entry selection. **Clear
filters** remains inside the expanded panel. The collapse preference survives
navigation in this browser tab, separately per page and authorized business/user
scope when session storage is available. Transaction filter values still reset on navigation. Mileage returns from its
edit page with the originating search/date/tag scope restored within the same business.
Report-period controls and create/edit forms remain expanded.

The local development seed creates Alpha and Beta demonstration entities in the
disposable loopback database. Startup validates both configured fixtures and fails
closed if either is missing; it never creates missing entities. A disposable fixture
from the former Alpha-only setup must be recreated with the explicit seed tooling.
**Select business** lists the development resolver's Server-configured choices.
Switching requires **Keep current business** or **Discard draft and switch**. A
confirmed switch replaces the context, private query cache, selections and drafts.
Selection is disabled during a pending bookkeeping mutation. If switching fails, the
current business, private cache and draft remain mounted; an error explains that the
switch was not confirmed. Independent browser clients can select different businesses.

`GET /api/v1/entities` enumerates only resolver-supplied contexts and resolves
their names through the ledger service. `POST /api/v1/session/entity` requires
Origin/CSRF and write capability checks, permits only the explicit trusted
technical-alpha resolver, and sets an HttpOnly, SameSite=Strict selection
cookie. The cookie must identify one of its configured entities; tampering cannot
introduce another entity. Entity-qualified requests must agree with the resolved
context before service composition. Other injected resolvers retain their existing
selection/authorization ownership and cannot use this endpoint as a bypass.

Hosted development/staging uses deployment's authenticated operator gate and one fixed
Alpha synthetic entity; Beta selection remains local-only. Unknown selection cookies and
attempts to select Beta in hosted mode reject before service composition.

This is functional technical-alpha context, not login or membership authorization.
Identity and tenancy owns authenticated memberships and context selection. Hosted
deployment and staging acceptance remain deployment and technical-alpha validation;
beta/paid activation and operator recovery agreements remain the separate gates. No
credentials, profile settings, provider controls, schema migration or entity creation
are exposed in the browser.

## Reference setup

Accounts, Categories, Payees and Tags expose **Add** links, labeled create forms and
tables. Accounts include type, classification, opening balance, parent and status.
Categories include kind and parent. Payees/tags show active/inactive status. Accounts
groups its Add action with the page heading. Account and parent names wrap in the table;
each neutral **Edit** action retains the full account name for assistive technology.
Narrow tables scroll inside a keyboard-focusable region. Create forms distinguish the
blue primary save action from the neutral back action, keep keyboard focus visible and
show legible disabled controls while a save is pending. [UI
foundations](UI_COMPONENTS.md) records the shared visual contract and bounded evidence
under shared UI components and browser design system. Creation, normalization,
hierarchy/reference validation and persistence delegate to the existing entity-scoped
application services. Parent names are displayed without treating table position or a
display name as a mutation key.

Accounts open in Asset → Liability → Equity order, then name. Categories open in
Income → Expense order, then name. Children remain directly beneath their parent,
including while users sort by column headers; sorting compares siblings rather than
flattening families. Indentation, a child marker and assistive parent/level text expose
the hierarchy. Other classifications/kinds retain deterministic fallback ordering.
Dashboard current account balances use the same default order; balance-sheet sections
order families within their existing accounting section, without interactive sorting.

Existing edits, removals and merges use the
[commercial concurrency contract](CONCURRENCY.md). Accounts deactivate
rather than physically delete. Categories support guarded removal; the current
category service has no independent inactive flag. Payees/tags support rename,
active status, multi-source merge and the service's guarded remove/deactivate
result. Inactive historical references remain resolvable.

Create and protected-edit forms use the same labeled field groups and control
geometry. Their light Soft workspace surfaces use softly tinted controls, bounded
form widths and compact desktop sizing, with comfortable touch targets. Tables
share quiet headers, primary record/amount hierarchy, compact textual statuses
and inline row actions while keeping financial alignment and local scrolling.
On desktop, wide tables keep row actions visible as other columns scroll.
Related fields align on desktop and stack at narrow widths; memo and
notes fields are multiline. Native multiple selects retain their keyboard
semantics and display complete selected labels. Save is primary, Back and
explicit commands are neutral, and permanent Delete is red. Reference tables
wrap long names and merge source lists wrap labels while retaining their
existing selection rules and baseline checks.

The Transactions table shows a single-line ellipsized description preview. Hover
shows the complete original text; focus exposes the full accessible name and
Enter/Space or tap opens the complete description above the table's scroll boundary
without expanding the row. Escape or clicking outside closes the description.
Transaction IDs remain searchable and explicitly included in CSV exports while hidden
from standard columns. Debit/Credit appear only when a displayed entry has journal
postings; searches retain the complete matching journal. Receipt cells show `📎 (N)`
with a spoken count; zero receipts are blank and existing receipt actions remain.

## Workflow navigation and actions

Transaction, mixed-use, recurring and mileage editors/details offer a top Back
control. Leaving a dirty form requires an explicit discard choice. Inline ledger
edits return to the existing table context.

The sidebar indicates the current destination with a leading rule and weight, as well as
color. Transactions remains current while visiting Journals, Mixed-use expenses or
Recurring expenses. Their shared **Entries** navigation wraps on narrow screens and
identifies the current route, including create/detail screens. These are ordinary links:
unsaved changes still require keep/discard, cancellation restores the opener, and
successful navigation closes the mobile menu and focuses main content. Server business
context and capability checks retain their existing owners under identity and tenancy.

**Categories & contacts** owns Categories, Payees and Tags. Their shared
reference navigation stays in that main-menu group, including create and merge
routes. Each reference page shows one page title; the table retains its
accessible name and explicit per-record actions without repeated visible titles.

Dashboard groups primary **View ledger** and neutral **Set up accounts** links
beside its heading. Transactions groups primary **Add transaction** and neutral
**Add journal**; the other entry lists group their existing Add link with the
heading. Inline informational links remain underlined.

## Transactions and journals

The ledger **Type** column keeps the accounting type (such as Expense) and adds
**Recurring** for initial and generated occurrences linked to a persisted schedule.
Recurring mixed-use projections show **Expense**, **Mixed-use** and **Recurring**
together and remain managed through Mixed-use expenses. Descriptions do not
control this label; Type filters and sorting still use the accounting type.

**Transactions** shows the combined ledger with **Add transaction** and **Add
journal** actions. The standalone Journals page also links to journal creation.
Forms share field components with protected editors rather than introducing
parallel payload or bookkeeping implementations.

Simple creation offers Income, Expense and Transfer, local calendar date, amount,
active accounts, matching category, managed payee/tags, optional new payee/tag
names, memo and reference. Transfers clear the category; non-transfers clear the
destination account. New entries require the relevant setup before save is offered.
The service validates references again inside its mutation boundary and owns
inline reference creation. Receipt bytes and attachments are documented in [Mileage and receipts](MILEAGE_AND_RECEIPTS.md).

Journal creation starts with two posting rows. Each has a stable ID, account or category
target, debit/credit side, amount, optional payee and memo. Explicit add and remove
controls keep at least two editor rows. Changing target type clears the previous typed
target. Debit, credit and difference feedback uses integer cents; save requires complete
positive balanced rows. Application services remain authoritative for balancing,
reference validity and atomic parent/posting writes. The commercial editor currently
uses Side/Amount controls, consistent with its protected browser mutations editor,
rather than the internal edition's separate Debit/Credit inputs.

Each posting groups its target, side/amount and optional details. The
debit/credit/difference summary wraps at narrow widths; explicit Add/Remove
controls preserve stable line identity and the minimum two rows. Presentation
continues to use the existing shared journal validation and payload owners.

Create forms preserve drafts and stable transaction/journal identities through
errors, perform no automatic mutation retry and reset only after confirmed
success. HTTP transaction-create identities reserve the `journal:` prefix for the
combined ledger's journal projection, preventing colliding selection identities. Inline references, financial records and posting writes remain in the
existing PostgreSQL transaction. Successful creates advance draft identities;
an explicit retry after failure retains the original IDs. Inspect records after
an uncertain response before deciding how to proceed.

Protected edits/deletes retain their originally loaded baselines and conflict
review behavior. Opening an editor loads the complete service record and captures
its baseline; submission does not fetch a replacement token. Derived transactions
remain read-only in the ledger. Journals are complete mutable entries, with no
independently editable posting rows. Streamlit state/reruns are not involved.

## Ledger and reports

`GET .../ledger` delegates to `ReportingService.query_ledger`. Date boundaries are
inclusive. Type, account/category target and tag filters preserve the shared service
semantics, including either transfer account, whole-journal target matching and
OR-within-tag matching. The browser exposes From, To, Type, Accounts and Tags (Accounts
also offers labeled Income/Expense category targets), Complete-entry sort/order controls
and a logical result count, plus case-insensitive search of displayed text and journal
posting text. Search does not match amounts, dates or receipt contents. The initial view
covers all dates, newest first; **Clear filters** restores it, including empty search,
all target/tag/type selections and Date/Descending ordering. Related filters align on
desktop and stack on narrow screens. Accounts/Tags are labeled native listboxes with
multi-selection guidance; full selected labels are displayed below them even when the
browser truncates an option. **Clear search** appears for nonblank text and preserves
the other filters.

The table resolves historical names and hierarchy paths, includes transfer
destinations, receipt counts, references and stable IDs, and groups journal
postings below their parent with Debit/Credit values. It scrolls within its region
on narrow screens within the named, keyboard-focusable **Ledger** region;
posting rows have no separate edit/delete action. Ordinary rows use normal weight,
while journal groups have a visible boundary and subordinate postings. Amount,
Debit/Credit and receipt-count headings align with their values. Long names,
tags, memos and identities wrap. Short **Edit** / **Receipts** controls retain
complete record descriptions as their accessible names.

Dashboard and Reports use `GET .../reports/summary` over one fresh service snapshot.
`ReportingService` owns profit/loss, balance-sheet sections, hierarchy rollups,
current earnings, the accounting-equation difference and account balances.
The shared calculations retain exact decimals for HTTP summary values, including
cents in large balances, aggregate totals and hierarchy rollups; float conversion
is reserved for the existing internal presentation projections.
The endpoint only projects these values into decimal-string DTOs. The browser
does not recompute accounting totals. Profit/loss uses the selected From/To
period; the balance sheet uses To as its as-of date. The separately labeled
**Current account balances · all dates** view includes inactive accounts and
opening balances. Transfers and mileage do not affect income or expenses.

The shared **Report period** controls explain those scopes, and **Clear dates**
returns to all dates. Profit/loss, balance-sheet metrics/equation and all-date
balances are separate sections. Values retain server precision and signs with
tabular numerals; comparable numeric table columns align right. Negative net
income/current earnings keep their minus sign and use red. Empty statement groups
and account balances have explicit text. Missing values display an em dash and
unavailable feedback, not a fabricated zero; loading and errors show their shared
status/reload controls without displaying invented summaries. The equation status
includes the server's difference when nonzero. This remains the existing summary
catalog, without extra charts or browser accounting calculations.

Confirmed and uncertain mutations invalidate the selected context's reference,
entry, ledger and report queries. Loading, empty, validation, conflict and failure
states use the shared primitives. Pending editor loads replace ledger filters so
a quick interaction cannot accidentally change a similarly labeled list filter.

## Validation and remaining work

Use the commands in [Commercial development](RUNTIME.md) and supply a
disposable `TABULA_RASA_TEST_DATABASE_URL` explicitly. Build the browser first:

```powershell
npm --prefix web run build
npm --prefix web run lint
npm --prefix web test
$env:TABULA_RASA_REQUIRE_BROWSER_TESTS = '1'
python -m pytest tests/http tests/postgres/test_http_workflows.py tests/postgres/test_browser_workflows.py tests/postgres/test_http_baselines.py tests/postgres/test_browser_baselines.py
```

Native tests cover reference setup, create/edit/delete, inline payee/tag creation,
invalid/foreign references, unchanged state after rejected writes, duplicate IDs,
inclusive filters, reporting, independent entity selection, original-token
conflicts, and desktop/mobile compiled forms. Synthetic screenshots may be saved
with `TABULA_RASA_WORKFLOW_SCREENSHOTS` pointing to an ignored artifact directory.
No live ledger or deployment credentials are used.

Core, secondary and existing-workflow ports are implemented; follow their [workflow
guide](IMPORTS_EXPORTS_AND_INTEGRATIONS.md) for current import/copy/bulk/CSV, receipt
and Maps behavior. Historical exclusions from this core slice are not outstanding
implementation tickets. [live owner cutover](../accounting/CUTOVER.md) owns selected
live-owner readiness, [external-beta acceptance](RELEASE_VALIDATION.md) owns
external-beta admission, and [capability retirement
inventory](../operations/RESOURCE_RETIREMENT.md) owns supported-capability retirement.
Keep the internal edition until its capability-specific retirement conditions are met.

Reuse map: inspected HTTP composition/DTOs, protected editors, loaded-form/cache
helpers, reference/transaction/journal/reporting services, PostgreSQL repositories,
internal renderers and the design contract. Extended transport DTOs/routes, existing
field renderers, scoped cache invalidation and native fixtures. New code owns
create-draft lifecycle, selection presentation and ledger/report presentation;
there is no parallel domain, persistence, concurrency or retry implementation.

## Accessible confirmations and validation

Confirmation prompts keep keyboard focus inside the modal and start on the keep
action. Escape keeps the draft; dismissal returns focus to the initiating
control where it remains available. Confirmed navigation focuses main content.
Rejected destructive confirmations and failed reloads close and expose the preserved draft's error
and review action. Pending operations disable the prompt's actions and Escape.
Declared field errors identify the affected labeled control without exposing
submitted values or internal server messages. See the
[browser-quality gate](TESTING.md) for repeatable coverage
and links to the release/acceptance owners.

Managed vehicle selection and history are defined in
[Mileage vehicles](MILEAGE_AND_RECEIPTS.md#vehicle-management).
