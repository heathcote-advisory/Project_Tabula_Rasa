# Project Tabula Rasa — Design system

## Purpose and authority

This document is the durable user-interface and interaction specification for
Project Tabula Rasa. It defines the product character, visual tokens, layout
rules, reusable component behavior, responsive and accessibility requirements,
Streamlit constraints, page contracts, and objective UI acceptance criteria.

It is intentionally text-first. Screenshots are optional implementation
evidence; they do not replace the written contract.

Two kinds of authority must remain distinct:

- **Current-state authority:** When the question is what the application
  actually does now, the implementation and maintained tests are evidence of
  current behavior.
- **Intended UI authority:** When the question is what UI or interaction is
  intended, the active task requirements and this document are the durable
  design contract.

When code, tests, documentation, and this design disagree, identify the
discrepancy and reconcile it intentionally. Do not silently redefine intended
design because code drifted, and do not change runtime behavior as part of a
documentation-only cleanup.

Bookkeeping uses the React/FastAPI/PostgreSQL application. The commercial shell
and first-use sections below specify its current interface. The Streamlit entry
point is retained solely for loopback operator administration, Google consent/grant
replacement, folder administration and source/recovery setup. Its bookkeeping pages,
navigation and session generation are retired. New PostgreSQL businesses use native
in-app creation; this does not acquire a Google grant.

Architecture, setup, usage and recovery belong in [Architecture](docs/ARCHITECTURE.md),
[Setup](docs/SETUP.md), [Using Tabula Rasa](docs/USING_TABULA_RASA.md) and
[Backup and recovery](docs/BACKUP_RECOVERY.md).

## Product character

The interface should feel professional, calm, precise, trustworthy,
financially literate, operational, efficient, and purpose-built. It should
not feel like a marketing site, consumer-banking promotion, cryptocurrency
dashboard, gamified budgeting product, generic admin template, or decorative
spreadsheet. It must remain comfortable during extended bookkeeping sessions.

## Design principles

### Information before decoration

Financial data, bookkeeping state, required actions, warnings, and recovery
information take priority over ornament.

### Dense but readable

Use space efficiently for tables, filters, forms, navigation, financial rows,
validation summaries, backups, and imports. Use larger spacing only between
major information groups.

### Tables are primary

Transactions, mileage, accounts, categories, reports, and profile summaries
are core work surfaces. Charts support interpretation and must not become
decoration.

### One dominant action per workflow

Each independent form, tab, expander, or workflow section normally has one
obvious primary action. A multipurpose page may still contain several
independent workflows and must not hide valid secondary actions.

### Semantic, non-exclusive color

Use color for state, direction, risk, completion, selection, and attention.
Use text, signs, icons, or explicit messages as well; color is never the only
state indicator. Blue is the default interaction color, not a financial-gain
indicator.

### No hover-only behavior

Important actions, values, warnings, and states must remain visible or
keyboard-accessible without hover.

### Native Streamlit first

Prefer native Streamlit controls. Use custom CSS, HTML, or components only
when the benefit is substantial and maintainable.

### Safe actions are explicit

Destructive or consequential actions must state what changes before execution.
This includes permanent deletion, receipt-reference removal, credential
rotation, Drive disconnection, storage-root changes, imports, and backup
creation or upload.

### Profile context is always visible

The selected business and workbook context remain discoverable. Every
profile-sensitive read, write, import, export, receipt action, backup, cache,
selection, and prepared snapshot is scoped to that business.

## Visual tokens

### Color

| Token | Value | Use |
| --- | --- | --- |
| canvas | #F5F4F0 | Warm application background |
| surface-primary | #FFFFFF | Tables, forms, cards, and dialogs |
| surface-secondary | #FAFAF8 | Secondary grouped controls |
| surface-hover | #F2F3F4 | Row and control hover |
| surface-selected | #EAF0FC | Selected rows and active navigation |
| text-primary | #202124 | Main text and financial values |
| text-secondary | #62666B | Supporting descriptions |
| text-muted | #85898E | Timestamps and tertiary metadata |
| border-default | #DDDED9 | Standard boundaries |
| border-subtle | #E9E9E5 | Table separators |
| border-strong | #B8BAB6 | Focused or emphasized boundaries |
| interactive-primary | #3267D6 | Primary actions, links, selection |
| interactive-hover | #2858BC | Primary hover |
| interactive-focus | #6B96ED | Focus rings |
| success | #287A4B on #EAF5EE | Confirmed completion and healthy state |
| error | #B33A3A on #FBECEC | Failed or invalid state |
| warning | #996600 on #FFF5D9 | Attention and incomplete state |
| neutral | #62666B on #F1F2F1 | Pending or disabled state |

Use green for confirmed success, balance, and favorable movement; red for
errors, adverse movement, invalid state, and destructive actions; amber for
duplicates, incomplete setup, out-of-balance state, and review conditions;
blue for links, navigation, selection, primary actions, and information; and
neutral gray for pending or inactive context. Do not make every save button
green or every ledger amount colored.

### Financial values

Direction must remain clear without color:

```text
+$1,250.00
−$420.00
$0.00
```

Use a plus sign when positive direction matters, a consistent minus sign,
tabular numerals, and consistent currency precision. Right-align comparable
amount and balance columns. Ordinary ledger amounts normally use neutral text
because their meaning depends on account context. A negative balance is not
automatically an error; normal liability and equity stored signs are not
errors.

### Typography

Use a system sans-serif stack:

```css
font-family:
  Inter,
  ui-sans-serif,
  -apple-system,
  BlinkMacSystemFont,
  "Segoe UI",
  sans-serif;
```

| Role | Size | Weight |
| --- | ---: | ---: |
| Application title | 24px | 600 |
| Page title | 22px | 600 |
| Section title | 16px | 600 |
| Card title | 14px | 600 |
| Body and controls | 14px | 400 |
| Table text | 13–14px | 400 |
| Secondary text | 12px | 400 |
| Major metric | 26–32px | 600 |

Use tabular numerals for financial figures where supported. UI writing is
direct, specific, concise, consistent, and action-oriented. Prefer Save
transaction over Submit and an explicit duplicate count over vague success.

### Spacing, geometry, and elevation

Use a 4px base grid with approved values 4, 8, 12, 16, 20, 24, 32, and 40px.
Use 36–44px table rows, compact filter controls, 12–16px metric-card
padding, and 16px primary surface padding. Keep related controls 8–12px
apart and major groups 24–32px apart.

Use moderate corner radii, thin borders, subtle dividers, and restrained
shadows only where hierarchy benefits. Do not use gradients, glassmorphism,
heavy cell borders, saturated alternating rows, or rounded cards for every
block.

## Charts and metric cards

Charts are quiet, readable, and subordinate to the values they explain. Use
lines for trends, bars for period comparisons, horizontal bars for category
ranking, and stacked bars only when composition matters. Avoid 3D charts,
rainbow palettes, and charts that repeat a visible metric without adding
meaning.

Every chart has a clear title, scope, units, readable axes, limited gridlines,
and visible values or a useful tooltip. A typical chart is 220–320px high and
must render safely for empty and single-point data. Use blue as the primary
series and related muted tones for neutral categories; semantic colors are for
semantic series, not decoration.

Metric groups use a short label, primary value, and optional comparison or
context. Support normal, positive, negative, warning, loading, and unavailable
states. Unavailable data is shown as an em dash rather than zero unless the
value is truly zero. Do not put every metric in a heavily bordered card.

## Application shell

The browser shell below owns bookkeeping navigation and selected-business context.
The local administrator has a title, a bookkeeping destination notice, Account
settings and Log out. It has no bookkeeping page selector or automatic workbook
initialization/reconciliation. Stale bookmarked bookkeeping route state cannot
restore a retired page.

Local administration requires application-password login, the explicit local-admin
flag and a loopback bind. Configuration changes require password reauthentication.
Its own business selector scopes Google and recovery controls to the selected local
profile. Named-user membership never unlocks this interface. Public Terms of Use and
Privacy Policy remain available before and after authentication without loading
workbooks, Google grants or ledger data.


## Commercial shell

**Current behavior — tenant Google connections :** Named Owners see
a Google Drive connection section within Data & backup for the selected
business. It shows safe status and explicit confirmed Connect/Reconnect/
Disconnect actions and requires fresh login. With customer OAuth enabled, Connect
redirects to Google consent, provisions a dedicated business folder and returns with
connected account email. Connecting/incomplete setup has explicit retry guidance;
reconnect preserves the same account/folders. For enrolled connections with
unreadable credential custody, the panel shows Reconnect required and bookkeeping
remains available. Other deployments retain operator
provisioning. Disconnect preserves files/links and explains separate Google revocation. No provider tokens, secret references,
folder locators or credential-entry fields are displayed. Unavailable/stale
commands remain visible failures and are not automatically retried.
Read-only, Editor, Accountant and Admin do not see connection management.
Existing local Account settings remains under its loopback flag/password gate,
with unlock tied to the current application-password configuration; commercial
membership and claimed profile IDs do not unlock that interface.

**Current behavior** for the opt-in named-user foundation:

- Pending identity displays a loading state before any private feature is
  mounted. Missing/expired/revoked identity offers Sign in and Create account
  through the hosted provider. Create account opens hosted registration and returns
  to `/invitations` after verified authentication. Registration grants no business
  access, ledger or beta approval. Disabled/incomplete registration retains private
  operator guidance and existing-account Sign in. Failed callbacks show a clean sign-in failure page with an allowlisted
  explanation, safe request reference and deliberate Sign in action to restart
  provider reauthentication. The screen never retries automatically or asks users
  to delete cookies; provider query values are never displayed. An ordinary
  signed-out visit has no failure warning. Authenticated users without active business access see My Businesses,
  explicit creation/invitation/migration choices and Sign out, not an empty ledger.
- Named sessions show the existing business shell with a labeled Sign out
  control. Session loss or authorization-storage outage unmounts private queries,
  drafts and receipt views; confirmed identity/scope/version changes remount the
  existing private boundary. Visible tabs recheck on focus/pageshow/visibility
  and every 60 seconds. Same-origin sign-out signals contain no private data;
  they invalidate pending initial session responses as well as loaded private UI.
- If selected business access is removed while another authorized business
  remains, session validation selects an available authorized business and the
  private boundary remounts. The no-business My Businesses screen appears only when no
  available business remains.
- Logout clears the local browser even when server revocation cannot be
  confirmed, and displays a safe failure instead of claiming durable success.
  Authorization failure screens retain Sign out without restoring private UI;
  a reload during storage outage can obtain logout-only CSRF through a same-origin
  read. Network failure remains visible and does not claim cookie clearing.
  Read-only and setup users can inspect identity and sign out without ledger
  write permission. [Named-user authentication](docs/identity/AUTHENTICATION.md) owns the
  operator settings, session policy and remaining release gates.
- Named users can switch among server-authorized business choices.
  Business, currency and role labels use server claims; backing organization names stay hidden.
  The shell shows Book currency and states the unit for all monetary amounts above
  its content, including entry forms, ledger, reports and downloads on desktop/mobile.
  Currency labels do not convert or round stored numeric values. Each new business
  has one fixed currency; switching remounts the selected unit with private state.
  Selector/list labels and download filenames identify USD versus CAD. CSV columns
  remain compatible. Import guidance states that currency-free amounts use the
  selected book and contradictory explicit source currency is rejected.
  Duplicate business names include the full stable business reference in selector/list
  labels. Confirmation names the target reference, and the shell retains the selected
  reference so users can distinguish same-name ledgers before entering records. Confirmed switching discards
  private drafts/uploads/queries and receipt URLs, returns to Dashboard and
  signals other tabs to reload authority. An uncertain switch unmounts private
  content and offers a safe reload/sign-out state.
- Business & members exposes Owner/Admin invitation and member administration,
  with current-role restrictions, loaded membership versions and last-owner
  protection. Ownership grants require a separate confirmation and recent login.
  Recipient-bound invitation credentials appear once for private distribution;
  authenticated acceptance/decline is also available without a selected business.
  [Business lifecycle](docs/identity/WORKSPACES.md) owns the exact security and
  retry contract. Browser creation receipts are scoped to the user and first-use/current-business
  context so an old completed setup cannot redirect a new creation. The bootstrap service supplies
  its transactional backend without adding a separate profile configuration UI.
- My Businesses and the sidebar show one flat set of authorized businesses,
  each with its independent role. Open business and sidebar selection share the
  same target-reference/discard confirmation. Long option labels stay contained;
  the complete selected name and stable reference remain visible in the shell.
  Business & members uses `/business/members` (the old `/workspace` URL redirects),
  names the business/reference on the member and invitation surface, and separates
  ownership actions from routine role changes. Review invitation shows only the
  recipient's offered business and effective role before acceptance or decline.
  Acceptance selects that business through the authorized selection boundary.

**Current behavior** for the technical-alpha application:

- The React shell uses the visual tokens, typography and approximately 760px
  stacking boundary above. Navigation covers Dashboard, Transactions, Assets,
  Mileage, Accounts, Categories & contacts, Reports, and Data & backup in the same order.
  A labeled Menu button exposes navigation on narrow screens.
- The server-resolved business name and USD/CAD book currency are always visible. The fixed synthetic
  context is identified as technical-alpha development. This shell does not
  expose internal profile keys, workbook metadata, Google credentials or local
  administration controls as commercial authorization.
- Hosted development/staging mode protects the shell, assets and API
  with an environment-specific browser HTTP Basic prompt over HTTPS. It retains
  one fixed synthetic business; the gate grants operator access, not commercial
  membership. Minimal health/version probes are public. Production rejects this
  synthetic mode; named authentication owns real login and session behavior. Hosted setup and
  acceptance remain subject to the [operator checkpoint](docs/operations/DEPLOYMENT.md).
- Dashboard and Reports show service-calculated profit/loss, balance-sheet
  sections, current earnings, the accounting-equation check and clearly labeled
  all-date account balances. Their From/To inputs govern the period/as-of date.
  Accounts, Categories and Payees/Tags offer shared create fields and tables,
  plus protected edits/removals/merges. Accounts deactivate; categories retain
  guarded removal without an invented inactive flag. Parent names are visible.
- The server-configured local business selector requires an explicit keep or
  discard-and-switch choice, is disabled during mutations and remounts queries
  and drafts on the current page after a confirmed context change. Failed switches preserve the current
  scope, private cache and draft while showing a selection error. Independent clients select
  independently. Functional selection never proves membership authorization.
- Transactions offers separate guarded navigation to Add transaction and Add
  journal forms. Creation uses the protected editors' shared fields; journals use
  target/side/amount posting rows, stable line IDs, visible totals/difference and
  explicit add/remove controls. Creates retain identities/drafts on failure,
  never retry automatically and reset only after confirmed success.
- The combined ledger offers inclusive From/To, Type, Accounts (including labeled
  Income/Expense category targets), Tags, complete-entry sorting and text
  search, defaults to all dates/newest first and preserves journal grouping.
  Amounts, dates and receipt contents are outside text search. Journal posting
  rows show Debit/Credit with no independent mutation action. Derived transactions
  retain their source owner. Other scoped routes retain availability states;
  Mileage retains the existing-record editor. No internal-edition workflow
  is removed or replaced.
- Shared loading, empty, validation, conflict, unexpected-error and success
  states use text plus semantic status/alert roles. Conflicts preserve drafts
  and offer an explicit review action. Forms retain loaded server metadata
  independently from editable values and request confirmation before dirty
  navigation or document unload.
- Protected mutations submit the baseline captured at load. Conflicts retain the
  draft and original token. Review latest record offers Keep draft or Discard
  draft and reload; only the latter replaces fields and metadata together.
  Fixed account types select their required classification; Other permits a classification choice. Payee/tag active-to-inactive saves require confirmation and preserve the original loaded baseline. Active accounts deactivate only through the confirmed Deactivate action; inactive accounts may reactivate through Active and Save changes. Parent choices contain compatible roots, and parents with children remain roots and retain their classification/kind until the children are moved. Transfer destinations exclude the selected source account; changing the source clears an identical destination. Changing transaction kind clears an incompatible category, and existing/new payee inputs are mutually exclusive. Bookkeeping selectors retain only each loaded field/posting's historical inactive references alongside active choices.
  Deletion/deactivation requires acknowledgement; merges retain target/source
  tokens. Merge source choices exclude the selected target, and changing the target clears it from selected sources. Successful saves advance the baseline from the confirmed response.
  No mutation retries automatically. Journal target selectors show Choose target
  for new postings and clear the selection when Account/Category changes.
- Context changes remount private queries and drafts; no prior business/session
  cache is reused. Forms stack; tables may scroll within their own region.
  Keyboard focus, labels and business scope remain reachable at 320 CSS pixels.
- Browser workflows use one native modal confirmation component for discard, navigation,
  business switching, record reload and destructive confirmations. The keep
  action receives initial focus, Tab/Shift+Tab wrap within the prompt, Escape
  keeps the draft, and dismissal restores the initiating control when it remains
  mounted. Pending operations cannot be dismissed or submitted again. A rejected
  destructive confirmation or failed reload closes so the shared error/review action is reachable.
  A successful route change closes mobile navigation and focuses main content;
  blocked navigation keeps its initiating link available. Declared top-level
  validation errors are associated with their labeled fields and generic messages.

**Current behavior — shared commercial visual foundation :** The React
stylesheet owns the shared type roles and spacing grid above, with the approved
commercial palette below. Primary surfaces use 16px padding. Native single-line
controls have a 40px minimum on desktop with a fine pointer and 44px on touch or
narrow layouts;
multiple selects remain multiline listboxes (128px minimum), and textareas are
vertically resizable (112px minimum). Disabled controls retain legible neutral
text. Keyboard focus combines a contrasting primary outline with the focus halo.
Create forms share primary submit/secondary back actions and section headers;
confirmation dialogs share action tones, with permanent deletion explicitly red.
Accounts demonstrates the shared page header, primary Add link, neutral Edit
action and keyboard-scrollable table region. Long account/parent names wrap;
Edit's accessible name identifies the full account. Table body cells, including
row headers, use ordinary weight; column headings retain emphasis. Financial
values use tabular numerals, and Accounts aligns the opening-balance heading
with its values. Shared metrics use 28px values and ordinary labels. These are
foundations for later page slices, not completion of the full design epic.
The compact [requirement/implementation audit](docs/browser/UI_COMPONENTS.md)
records commercial/Streamlit conflicts, reuse owners, hooks and visual evidence.

**Current behavior — light Soft workspace (owner-selected direction):**
The commercial app stays light even when the operating system prefers dark.
This explicitly replaces the earlier commercial neutral palette; the internal
Streamlit palette above retains its own owner.

| Commercial token | Value / treatment |
| --- | --- |
| Canvas | `#EEF2F7` |
| Primary / secondary surfaces | `#F8FAFD` / `#EAF0F8` |
| Native control fill | `#EDF2F9` |
| Hover / selected | `#E5EDF8` / `#E3ECFD` |
| Primary / secondary text | `#223149` / `#52627A` |
| Default / subtle / emphasized borders | `#CFDAE9` / `#DCE5F0` / `#7B8DA7` |
| Primary action / hover / focus halo | `#2E60CC` / `#2858BC` / `#6B96ED`; filled actions use white text |
| Neutral pending/disabled | `#52627A` on `#E8EDF4` |
| Controls / surfaces | 8px / 12px radii; surface shadow `0 4px 16px rgb(34 49 73 / 4%)` |

Forms and editor/filter surfaces stop growing at 960px; tables keep the available
page width and intentional local scrolling. Labels and primary record names/amounts
use weight 500, ordinary supporting table cells 400 and page headings 600.
Compact desktop fields retain readable padding;
touch targets, native multiline selectors and error/focus associations remain.
Tables share a rounded, subtly bordered region, a tinted header and restrained
row dividers/hover; financial columns stay right-aligned and tabular. Records use
primary-name and quieter supporting-detail hierarchy, compact text status/type
badges and lightweight inline row actions. At desktop widths of at least 1000px,
action columns stay visible while the financial columns scroll. Narrow layouts
retain local scrolling and 44px action targets. Writer detail links say Manage
for Assets, Recurring and Mixed-use; read-only detail links say View. The route,
loaded baseline, confirmations and available commands remain capability-owned.
The ledger displays transaction type and source as separate pills (for example,
Expense and Mixed-use), with the existing combined accessible row name. The
Type column also shows a Recurring pill for initial and generated occurrences
with a persisted recurring schedule ID, including mixed-use projections. It
retains the accounting type and source pills; descriptions do not determine
recurring identity, and Type filtering/sorting retains accounting semantics. Mileage
keeps 16px between Download filtered mileage CSV and the log table. Semantic
success/error/warning colors and all bookkeeping/state owners remain unchanged.
The [integrated acceptance record](docs/browser/VISUAL_REVIEW.md) tracks the
selected direction, exact-candidate evidence and separate manual/release gates.

**Current behavior — commercial core presentation :**
Independent
page sections use the 32px spacing token, including sections following explanatory
text or an action row. Sidebar context controls stay within the available width;
the selected business's full name remains visible above the selector. The top bar
identifies Business & members rather than displaying a generic Page placeholder.
The business-first replacement names and selector require their own selected-release verification.

Transactions and Mileage share an accessible Show filters / Hide filters button.
The header keeps a short active-scope summary visible while collapsed. Collapsing
keeps controls mounted and preserves filters, sorting, results, selection and CSV
scope. Only the collapse preference is stored in session storage, separately for
each page, actor, authorization cache scope, organization and business. Navigation
still resets filter values as before; unavailable storage simply disables preference
persistence. Report-period controls govern metrics as well as tables and remain
expanded, as do create/edit/import forms and other workflow cards.

Pending confirmations show live progress wording and disable their actions.
Membership/ownership commands report confirmed success, retain safe rejection
feedback, and stop resubmission after an uncertain outcome with reload guidance.
There are no automatic mutation retries. Existing authorization, reauthentication,
loaded-version and last-owner checks retain their owners. Final business-specific
membership/onboarding acceptance still requires selected-release verification; see the
[follow-up record](https://github.com/Roklion/Project_Tabula_Rasa/issues/401).

The shell marks the current main destination with weight and a leading rule as
well as color. Transactions remains the current main workflow for Journals,
Mixed-use expenses and Recurring expenses. Categories & contacts groups
Categories, Payees and Tags, including their create and merge routes. Moving
between those reference routes keeps Categories & contacts current in the main
menu. Each group has its own wrapping, labeled navigation list with a visible
current link. Mileage shares View mileage log / Add trip navigation;
Add trip remains capability-gated. These are real guarded route links, not ARIA
tabs. A cancelled dirty navigation retains the draft, menu and initiating focus;
a completed navigation closes the menu and focuses main content.

Page headers group related actions. Dashboard's primary View ledger and neutral
Set up accounts are styled links; Transactions uses primary Add transaction and
neutral Add journal. Journals, Payees/Tags, Mixed-use and Recurring lists reuse
the shared header with their existing capability-gated Add action. Informational
inline links retain their underlining.

Dashboard/Reports share labeled period controls and Clear dates, separated
Profit and loss / Balance sheet / all-date balances sections, responsive metric
groups, ordinary row weight and aligned numeric headings/cells. From/To are
inclusive; To is the balance-sheet as-of boundary. The all-date section is
explicitly independent of those controls. Decimal strings retain server signs
and precision. Negative net/current earnings retain a minus sign and use red;
missing values show an em dash/unavailable status, never an invented zero.
Empty statement groups and account balances explain their absence; loading and
failed reads do not fabricate summary values. The accounting-equation message
shows balanced, difference (including the reported value), or unavailable text.
No report, chart or client accounting calculation is added; the Streamlit page
contracts below do not expand the commercial summary endpoint's scope.

The combined ledger uses a labeled filter surface: From/To/Type, then Accounts
and Tags, search with conditional Clear search, complete-entry sort/order and
Clear filters. Desktop related controls align; narrow controls stack. Native
multiline listboxes have equal deliberate height, selection instructions and
full selected-label summaries (native option wrapping varies by browser).
Clear filters restores all dates/types/targets/tags, empty search and Date /
Descending ordering. The keyboard-focusable Ledger region contains horizontal
overflow; accounting columns and stable IDs remain available. Names, tags and
memos wrap; ordinary rows stay normal weight. Each journal has one parent/group
with subordinate Debit/Credit postings and no posting mutation actions.
Ordinary Edit/Receipts controls use short visible labels and complete record
labels for assistive technology; source-owned rows retain their owner links.
[UI foundations](docs/browser/UI_COMPONENTS.md) records the reuse map and
synthetic before/after evidence. Integrated visual review, identity/onboarding
and hosted/device verification remain separate.

**Current behavior — commercial forms and secondary presentation :**
Create and protected-edit routes share labeled field groups, two related desktop
columns that stack at 760px, multiline memo/notes controls, decimal keyboard
hints and native listboxes with selection guidance and full selected labels.
Save is primary, Back and explicit commands are neutral, and permanent deletion
is red. Existing confirmation, dirty-navigation, historical inactive choices,
loaded baselines and explicit stale-conflict review retain their owners.
Journals group each stable posting's target, side/amount and optional details;
Add/Remove remain explicit, and debit/credit/difference feedback stays visible
without duplicating balancing or payload logic. Reference lists wrap long names
and retain stable per-record Edit actions; merge choices and confirmations keep
their original semantics.

Assets, Mixed-use and Recurring reuse these fields, detail groups, action and
table patterns. Source ownership, service-calculated allocation, posted-history
locks and future-only schedule edits are explained alongside the applicable
controls. Depreciation and occurrence history remain locally scrollable tables;
generation, posting and retirement retain explicit protected confirmations.
Mileage uses the same create/edit fields, a named scrollable log, wrapped text
and aligned distance columns; manual entry, round trips and scoped CSV remain.
Shared fields keep their labels and controls at the top of a grid row, so the
compact date filters do not stretch beside the multiline tags list. Reference
management pages show their title once; tables retain accessible names without
repeated visible section headings or captions.
Receipts group linked files, attachment and unreferenced recovery separately,
with full filenames, keyboard-accessible metadata, upload/link status and clear
unlink-retains-file guidance. Read-only users retain inspection/downloads;
unconfigured storage offers availability/reload without attachment controls.
Data & backup provides the three import workflows described below for authorized
users. Browser backup remains outside this slice; absent workflows are not shown
as disabled controls or promised as a release.
The integrated named-user signed-out, session-unavailable and setup-required
surfaces also share headers, surfaces and Sign in/Sign out actions, while their
identity, private-view unmount and receipt-abort behavior retains the authentication boundary.
The [route inventory and evidence](docs/browser/UI_COMPONENTS.md#forms-confirmation-and-feedback)
records all current commercial routes and accepted exclusions. Identity/onboarding, integrated visual review and hosted/provider/device
verification retain separate component contracts.

[Foundation conventions](docs/browser/FOUNDATION.md) owns technical contracts.
[Commercial concurrency](docs/browser/CONCURRENCY.md) owns the loaded-baseline contract.
**Current behavior** for mileage and receipts: Mileage has a complete manual log,
inclusive date/any-tag filtering, scoped totals/CSV, one-way/round-trip creation
and fresh detail routes reusing the protected editor. Ordinary saved transactions
link to receipt management with portable metadata inspection, same-origin
downloads, multi-file attachment, upload/link progress and unresolved-file retry.
Confirmed uploads survive link failures. Retry and cancellation notices distinguish
confirmed uploads from unconfirmed selections and directs users to inspect
unreferenced receipts before selecting unconfirmed files again.
Confirmed unlink removes only the reference; unreferenced files can be relinked.
Read-only users can inspect/download;
unconfigured document storage shows availability text without upload controls.
Source-owned receipt workflows stay with their owners. The financial CSV, source-receipt and Maps workflows
add complete-entry **Export selected**, transactions/journal-postings CSVs on
Data & backup, mixed-use source receipts and optional Maps lookup/routing.
Mixed-use receipt changes carry the separately loaded source baseline and share
ordinary upload/recovery controls. Generic projections remain read-only. Maps
suggestions and place IDs remain transient, separate from recorded text; explicit
distance confirmation updates the editable draft, and only Save persists it.
Round-trip directions are calculated independently; failures preserve manual
entry. Optional lookup fields share the light two-column field grid; route
guidance, calculation and failure messages span its full width before editable
mileage and stack with the fields on narrow screens. [Workflow ports](docs/browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md) owns these contracts.
Transaction and QuickBooks mileage imports, copy, bulk and Expense-to-recurring conversion are also available through their existing domain services. [Commercial mileage and receipts](docs/browser/MILEAGE_AND_RECEIPTS.md)
owns this slice's transport, recovery and validation contract.
[Commercial core workflows](docs/browser/BOOKKEEPING.md) owns core workflow contract.
Named identity supplies authenticated identity/membership context selection,
[Secondary workflows](docs/browser/RECURRING_AND_ASSETS.md) delivers recurring, mixed-use and asset workflows.
[Browser quality](docs/browser/TESTING.md)
documents the automated browser baseline. [Technical alpha](docs/browser/RELEASE_VALIDATION.md)
defines the release/routing matrix and outstanding hosted receipt configuration,
staging, rollback and manual-device gates; no internal workflow is retired.
The default hosted synthetic runtime can bind the existing receipt UI through
the explicit server-only [Drive file configuration](docs/storage/STAGING_RECEIPTS.md).
Absent configuration retains the availability screen; invalid supplied bindings
stop startup. No credential controls or Google authorization routes are added to
the commercial browser.
The Streamlit-specific shell requirements above continue to govern the internal
edition.

## Commercial first-use setup

**Current behavior** for first-use onboarding on the named-user runtime:

- Signed-out Sign in and Create account actions distinguish existing login from
  hosted end-user registration, email verification and required factor enrollment. Users
  without an active business see My Businesses and choose Create your first business,
  Join an invited business, or existing
  Sheets migration. Existing members go directly to their authorized ledger.
- Fresh setup collects a business name and country/currency, then reviews the one supported
  standard bookkeeping default. Back preserves the details. After enrollment approval, Create business opens
  Dashboard with account review, first transaction and ledger next actions.
  The server creates a private backing organization and grants the creator Owner;
  no workspace name or organization selector appears in setup. Each new business
  uses an independent container and copies no other memberships. Existing data
  is not converted or granted access implicitly. Persistent boundaries and the
  non-destructive rollout are defined in [Business tenancy](docs/identity/BUSINESS_TENANCY.md).
- Authenticated My Businesses lists permitted names and each specific role, with
  Create another business available independently of the current business role
  when the server advertises business creation availability. Migrated-profile
  runtimes disable bootstrap and omit creation controls in both first-use and
  existing-member views; invitation and operator migration paths remain available.
  It shares the first-use form and bootstrap owner. Business & members retains
  member/invitation actions and has no Add business within a container action.
  The existing authorized selector and scope boundary still own selection and
  remount private state when business authority changes.
- Progress and failures use shared status/alert components. Explicit retries
  retain the request ID and submitted payload; refresh checks the durable server
  receipt. Only opaque user-scoped request IDs enter tab session storage. Names,
  credentials, drafts and ledger contents are never restored from browser storage.
  Business selection remains server-authorized. Shared section spacing separates
  independent setup/invitation sections by the section token, including the section
  following Review defaults; heading-to-content gaps remain smaller.
- `/invitations` routes new and existing members to the shared acceptance form,
  including after sign-in, without provisioning a second organization. The
  private credential is pasted after login and never included in a link.
  Confirmed acceptance also signals peer tabs to clear stale setup or ledger scope.
- Invitations are optional and available through the supported private delivery
  flow; connections for internal use are optional through Data & backup; beta receipts
  require customer-owned Google Drive. Existing live
  migration requires the explicit operator cutover process. First-use setup
  does not import or claim existing Sheets books.
- Shared tokens, controls, labels and touch targets support narrow screens and
  keyboard operation. This local implementation does not grant external-beta or
  security, hosted-provider and external-release acceptance.

[Business lifecycle](docs/identity/WORKSPACES.md) owns setup recovery and deployment.

## Page headers, workflow groups, and messages

**Current behavior — customer portability:** Data & backup includes Accountant
handoff for the selected business. Owner, Admin and Accountant can download a ZIP
of all 26 canonical collections, instructions and a versioned checksum manifest.
The default includes retained receipts and verified unlinked provider files; an
explicit metadata-only choice discloses that receipt bytes are absent. Audit
history, identity, credentials and operational policies are excluded. The manifest
discloses audit activation and unavailable pre-activation history. Shared loading
and error components explain preparation and explicit retry. Missing/unverifiable
files prevent full delivery. Limits are 50,000 records, 200 MiB of package content
and five minutes of file preparation. Authority is rechecked before delivery;
there is no retained archive or reusable link. Navigation/business changes abort
the pending browser download. Editor and Read-only see the full-export capability
requirement and retain ordinary CSV downloads.

**Current behavior — customer lifecycle:** Data & backup exposes separate
business closure, personal login closure and privacy-review requests. Only a
current active Owner can request or confirm business closure. The impact review
identifies the business, requires saved-export acknowledgement and recent sign-in,
and starts a 60-day read-only window. The Owner retains handoff and cancellation;
any current active Owner may cancel with recent sign-in during that window.
At expiry ordinary business access ends; data remains retained and support review
is required. Bookkeeping mutations are also fenced in PostgreSQL, including workers.
Request identity, versions and immutable transition events support explicit retry
and restart. Account confirmation immediately disables the local login and revokes
its sessions, without deleting provider identity, businesses or history. Last-active-
Owner checks run both when requesting and confirming account closure. Privacy
erasure only requests case-specific review. Requests/status remain available to
authenticated users without a selected business; disabled users receive support
guidance. No deletion, archive pruning or formal hold tooling is enabled.
The [lifecycle policy](docs/operations/CUSTOMER_LIFECYCLE.md) separates effective
preservation from proposed 90-day live-data/backup deadlines and unresolved legal
decisions; public legal publication and customer release remain separate gates.

**Current behavior — existing workflow ports :** Data &
backup contains separate Tabula Rasa CSV, QuickBooks transaction and QuickBooks
mileage uploaders, scoped to the selected business. Uploaders require read, write
and import access (Owner, Admin or Accountant); Editor access alone is insufficient.
File preparation/preview is
read-only; explicit commands submit the selected source again for fresh validation.
Tabula Rasa CSV preserves its first-20-source-row presentation and Income/Expense
contract. QuickBooks transactions preserve CSV/General Ledger XLSX/ZIP conversion,
counts, row diagnostics, optional reference proposals and Transfer support.
QuickBooks mileage preserves Business/duplicate/Personal/Unreviewed diagnostics,
one-decimal distance and a confirmed import. Failed/uncertain commands retain the
file/draft and direct users to inspect the ledger/log before retrying. No automatic
retry or partial-batch success is shown. The shared page owns one dirty-navigation
guard across its three independent uploader states.

The commercial ledger offers explicit visible-entry selection with loaded stable
IDs/baselines, ordinary Copy transaction and confirmed Category/Payee/Tag changes
or eligible permanent deletion. Copy starts with a new identity, active Tags,
exactly one source memo suffix and no receipts or recurring ownership. Journal
selections and mixed-use/depreciation projections disable generic mutations;
selected CSV export uses the financial CSV export contract. Filter/search/sort changes clear selection.
Bulk mutation validates the entire selection before an atomic PostgreSQL write;
untouched fields remain unchanged. Entering Bulk mode loads baselines and refreshes
the displayed ledger before enabling selection; a failed refresh prevents entry.
After a submitted failure, the preserved bulk draft remains visible with actions
disabled until the user leaves Bulk mode and loads a fresh selection.

Complete mixed-use sources offer independent Copy mixed-use expense with new
source/projection identities and no copied receipts/recurrence. An eligible ordinary
Expense offers Make recurring, loading a fresh baseline and the existing schedule
fields; confirmation creates a schedule around the existing initial Expense rather
than duplicating it. Prefilled copies require confirmed cancellation even without
edits. Confirmed copy/conversion completion closes that mutation surface.
[Commercial import, copy and bulk workflows](docs/browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md)
owns formats, failure behavior, transport, validation and alpha and live-owner boundaries.

**Current behavior — commercial secondary workflows (recurring, mixed-use and asset workflows):** Transactions
links to Mixed-use expenses and Recurring expenses. Mixed-use source creation,
editing and confirmed deletion preserve the stable source/projection pair; a
service-calculated Ledger result previews allocation. Incomplete relationships
remain inspection-only. Recurring lists show cadence, next/latest occurrence,
status, attention and ID; details show durable occurrence provenance. Ordinary
and mixed-use creation commit the schedule and initial occurrence together.
Protected Pause/Resume/End actions require confirmation; End retains history.
Generate due occurrences is an explicit confirmed command. Commercial page
loads never reconcile recurring schedules; the internal edition's on-load
behavior below remains specific to Streamlit.

With the entity's durable-worker authority explicitly enabled under the recurring-job policy,
Generate due occurrences queues the same bounded schedule jobs as the timer.
The result states the queued schedule count and directs the user to refresh
later for occurrences/attention. Otherwise it retains synchronous generated and
attention counts. The worker uses the approved business time zone and cannot
generate future business dates. [Jobs operations](docs/operations/JOBS_AND_AUDIT.md)
owns activation/catchup/rollback; page visits remain read-only.

Opt-in PostgreSQL monthly locks  reject prohibited accounting mutations
with a safe conflict and guidance to reverse in an open month or request Owner
unlock. Draft/baseline behavior remains unchanged. The narrow authorized
close/unlock/reversal API is documented in [accounting periods](docs/accounting/PERIODS_AND_CORRECTIONS.md);
no broad administrative screen is included. Source-owned accounting retains its
existing workflows and stricter history rules. Streamlit/Sheets remains unchanged.

Assets provides a register, create/detail/edit, retirement and guarded deletion.
Server-calculated quarterly schedules show override reasons, Posted/Pending and
ledger IDs. Protected override/removal and Post due depreciation require
confirmation. Terms/history locks and deletion availability come from the
service, including immutable posted provenance. Responsive tables scroll inside
labeled regions and forms retain the existing dirty-navigation/conflict contract.
Completed explicit commands require returning to the list for a fresh editor.
Detail visits wait for a fresh read before opening an editor; later background
read failures preserve its loaded draft and baseline. Read-only views expose
saved terms without mutation controls.
[Commercial secondary workflows](docs/browser/RECURRING_AND_ASSETS.md) owns
scope, recovery and release boundaries.

Page headers state the page purpose and place its primary workflow actions
near the relevant content. Use tabs for current workflow divisions:

- Transactions: Add transaction and View & edit ledger
- Transactions: Payees & Tags management
- Mileage: Add trip and View & edit mileage log

Assets is a separate top-level master-record workflow. It is not a
Transactions tab and does not expose a mixed-use placeholder before that
workflow is implemented.

Use bordered containers or expanders for dense independent workflows such as
edit forms, imports, profile administration, and backup controls. Do not move
long previews or multi-part recovery workflows into cramped dialogs.

Messages identify what happened, whether data changed, and what to do next.
Use informational blue, warning amber, error red, and confirmed-success green
with meaningful text. Do not show a success message before the relevant
Google API or storage operation confirms the write. Ordinary errors must not
expose secrets, tokens, private keys, private ledger data, receipt contents,
or raw stack traces.

## Tables and generic filters

Tables use compact rows, clear headers, subtle separators, restrained hover,
clear selection, and sticky headers where feasible. Dates and descriptions are
left-aligned; amounts, balances, and counts are right-aligned. Preserve stable
record-ID mapping and keep a reliable explicit selector for edit workflows.
Do not hide important accounting fields solely to avoid narrow-screen
scrolling.

For a management table whose visible rows can be changed, select one row with
the table's native row-selection affordance, then use Edit, Rename, or Remove.
The selected row's stable record ID, never its displayed position, identifies
the mutation target. Clear or repair selection when the profile, visible rows,
or underlying records change. Avoid a second dropdown that only reselects an
already-visible target; related-value selectors such as merge targets and
sources may remain separate.

Generic filters use explicit labels, AND semantics, visible scope, and a
clear path back to the unfiltered state. Changing result scope clears
incompatible row selections. Exact control order and field meaning belong in
the relevant page contract below.

## Forms, buttons, and destructive actions

Use one column for sequential decisions and two columns only for clearly
related desktop fields. Keep labels visible, helper text concise, tab order
logical, and validation near the workflow. Stack related columns on narrow
screens.

Blue is the normal primary action. Neutral styling is for alternatives and
read-only checks. Red is for permanent deletion or an equally destructive
operation. Amber may call attention to a review or retry action. Disabled
actions remain legible and explain why they are unavailable.

Permanent deletion confirmations name the record, state that deletion is
permanent and cannot be undone, and require explicit acknowledgement. Receipt
reference removal must state that the ledger reference changes while the Drive
file remains.

## Receipts as a cross-cutting interaction

Receipts are managed within the selected saved transaction's workflow. When
Drive is unavailable, show a clear setup message and do not display
nonfunctional upload controls.

Supported files are PDF, PNG, JPG, JPEG, WEBP, and HEIC. Every selected file
is validated before upload; each is non-empty and no larger than 20 MB, and a
batch is no larger than 200 MB. Preserve a recognizable sanitized filename,
suffix conflicts numerically, and store stable identity, size, and SHA-256
metadata.

Show linked files with an open-in-Drive link, Remove receipt action, and the
consequence that only the ledger reference is removed. The unreferenced
workflow can link an eligible app-managed file without renaming it. Integrity
metadata warnings remain visible.

Receipt-on-create is an ordered workflow: validate fields and files, save the
transaction, upload files, and link each result independently. A confirmed
transaction is never reported as unsaved because receipt work failed. Partial
work keeps successful links, reports complete and unresolved counts, and
offers retry only for unresolved work. Each Drive upload uses a prepared
receipt ID and content hash: after an uncertain response, read Drive once by
that identity, then retry the upload once only when the file is clearly absent.
Sheets linking uses the shared bounded write policy: one fresh readback can
confirm an uncertain link, while an unconfirmed uploaded file remains available
through Unreferenced receipt. The editor retains multi-file upload, retry,
cancel recovery, unlink, and unreferenced-link behavior.

Add and Edit share receipt recovery keyed by profile and transaction ID. New
transactions receive their UUID before persistence, without a placeholder row.
Pending receipt state is scoped to the selected profile and exact workbook and
receipt folder. A configuration change clears that pending state instead of
reattaching it elsewhere; uploaded files remain recoverable in Drive.

The interface reports the concise outcome and next action; it does not expose
an internal pending/uploaded/linked/failed state machine as a separate product
surface.

## Imports as a cross-cutting interaction

The Data & backup page keeps Tabula Rasa CSV, QuickBooks transactions, and
QuickBooks mileage as separate uploaders. Parsing, preview, diagnostics,
duplicate detection, and proposed account/category creation are read-only.
Explicit import actions perform the confirmed batch write.

Preview states must clearly distinguish ready, duplicate, skipped, conversion,
validation, and storage failures. Imports use the selected profile and validate
references again before writing. No partial import is claimed after a failure.

### Import page contract

The Tabula Rasa CSV workflow requires Date, Type, Amount, Account, and
Category, accepts Payee, Description, and Reference, previews up to 20 rows,
and imports Income or Expense rows only. It does not import transfers.

QuickBooks transaction sources are Transaction Detail by Account CSV and
Self-Employed General ledger XLSX or its supported ZIP. Its preview retains
ready, duplicate, Income, Expense, Transfer, conversion-error, and
validation-error counts; duplicate and error row numbers; up to 50 source
rows; optional account/category auto-create proposals; and an explicit Import
validated rows action. Duplicates are skipped and valid rows are written in
one confirmed batch after references are checked again.

QuickBooks mileage retains ready, duplicate, Personal, Unreviewed, and
validation-error counts; row numbers and details; up to 50 source rows; and an
explicit Import validated mileage action. Only valid unique Business rows are
written. The three uploaders have separate staged state and never write during
preview.

## Responsive behavior

The application is desktop-first but remains usable at narrower widths. At
approximately 760px and below:

- multi-column forms stack;
- metric cards wrap;
- dashboard sections stack by priority;
- sidebar navigation may collapse;
- tables may scroll horizontally; and
- secondary actions may move below the primary action.

The priority order is page title and primary action, primary financial summary,
attention items, supporting metrics, main table or chart, and secondary
details. There must be no control overlap or unintended page-level horizontal
overflow. Intentional table scrolling is acceptable. Do not silently omit
important accounting columns.

## Accessibility

All pages require keyboard navigation, visible focus, explicit labels,
sufficient contrast, non-color state indicators, descriptive button text,
predictable control order, and accessible error messages. Do not remove focus
outlines without an equivalent replacement. Icons are never the only
representation of delete, edit, warning, success, financial direction,
active/inactive, or balanced/out-of-balance state. Avoid nonessential motion.

## Streamlit constraints

Prefer the following mappings:

| Need | Preferred control |
| --- | --- |
| Text | st.text_input or st.text_area |
| Amount, miles | st.number_input |
| Date | st.date_input |
| Single choice | st.selectbox |
| Account filter | st.multiselect |
| Transaction type | st.segmented_control |
| Boolean or acknowledgement | st.checkbox or st.toggle |
| Upload | st.file_uploader |
| Submission | st.form and st.form_submit_button |
| Read-only selectable table | st.dataframe with selection |
| Edit/Bulk mode | segmented control or toggle with explicit labels |
| Export | st.download_button |
| Status | st.success, st.warning, st.error, or st.info |
| Metric | st.metric |
| Chart | st.plotly_chart |

Avoid replacing native controls solely for styling. Large prepared exports
should not be regenerated on every rerun. Show their preparation timestamp,
change Prepare to Refresh when a set exists, and invalidate a set after a
confirmed workbook write.

## Page contracts

The page contracts below are the canonical location for exact control order,
fields, table schemas, and current UI acceptance criteria. Generic sections
above define reusable rules and do not repeat these details.

### Bookkeeping workflows

The retired Streamlit Dashboard, Transactions, Assets, Mileage, Accounts, Categories,
Reports and Data & backup contracts no longer specify an active interface. Current
browser behavior and shared invariants are specified in [Using Tabula Rasa](docs/USING_TABULA_RASA.md),
[foundation](docs/browser/FOUNDATION.md), [protected forms](docs/browser/CONCURRENCY.md),
[imports/exports](docs/browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md) and
[recurring/assets](docs/browser/RECURRING_AND_ASSETS.md). Automatic recurring execution
belongs to PostgreSQL jobs; page loads never generate occurrences. Retained historical
recurring and asset data remain part of migration, canonical export and recovery.

### Internal support operations

**Current behavior:** The commercial support surface is a restricted operator CLI
and typed API, separately authorized by expiring organization/environment grants.
It exposes bounded metadata and read-only audit inspection, plus preview/confirm
access containment. It adds no customer navigation or remote Account settings
clone. See [Support operations](docs/operations/SUPPORT.md). Tenant membership,
selected business and legacy local administration do not grant support authority.

### Account settings

#### Purpose and availability

Account settings is the local administration home for business profiles,
credentials, Drive storage, and recovery metadata. It requires loopback-only availability and application-password reauthentication.
It is unavailable for writable administration in hosted environments.

Organize the surface as Business profiles, Google setup, Recovery, and
Security & deployment information. The last section is informational.
Streamlit Cloud Secrets generation/export is retired; commercial deployment uses
separate server secret provisioning and authorized connection activation.

#### Business profiles

When no local profile exists, lead with profile creation and do not render
unrelated Drive or Synology controls. Preserve:

- profile summary with key, business, masked spreadsheet ID, local backup
  folder, Google connection/account, Synology state/path,
  and configuration source;
- business name and immutable safe profile key;
- optional local backup folder and Save profile for the initial setup record;
- profile-scoped Connect/reconnect Google;
- ledger spreadsheet selection through Google Picker, with OAuth open and
  Editor capability validation before saving its ID, rejecting a ledger
  already assigned to another local business profile;
- Validate ledger: initialize genuinely blank workbooks; populated legacy or
  incompatible workbooks are rejected without writes under the frozen
  [format-13 source contract](docs/SHEETS_SOURCE_CONTRACT.md); historical
  ledger/archive conversion is retired;
- edit of business name and local backup folder; and
- candidate workbook Picker authorization for recovery/migration that preserves
  the active ledger ID.

Sheets and Drive use the same human Google OAuth grant with `drive.file`.
There are no Sheets service-account upload, private-key, or rotation controls.

Failed validation leaves the prior profile intact. Never display private keys,
refresh tokens, private ledger data, or receipt contents. Readiness performs
fresh workbook and configured-destination checks without walking ordinary
Drive parent folders.

#### Google setup

Preserve profile-scoped Connect or reconnect, disconnect without deleting
Drive files or revoking the Google application grant, existing-root Picker selection or app-managed root creation,
Provision storage folders, and authorized-account/destination summaries.
Managed Receipts and Ledger Backups children are identified by stored IDs and
profile metadata rather than names alone.

#### Initial recovery workbook provision

This retained operator action prepares approved source/recovery workbooks. New
PostgreSQL businesses use native in-app creation.

In Account settings > Google setup, a connected profile with no ledger can use
Create ledger spreadsheet instead of selecting an existing file. The app creates
`Business name Ledger` in that Google account's My Drive, saves its ID, and
initializes the existing required worksheets and default accounts/categories.
This does not require a separate Picker grant because the app creates the file.
The action is hidden once a ledger is configured and never replaces an existing
ledger. Drive receipt/backup folders are still configured in the same setup tab.

If creation or initialization is uncertain, no file is deleted or blindly
recreated. Inspect Drive and select the created file through Picker; if its ID
was saved, use Validate ledger to finish supported blank-workbook initialization.


#### Recovery

Preserve enable/disable, profile-root Cloud Sync summary, NAS destination,
schedule, retention, download-only direction, and destination-deletion
protection. Enabled records use layout profile-root automatically. Saving
these local metadata fields must not access Drive or verify DSM, and equivalent,
shared, or nested normalized NAS destinations are rejected.

Local backup and Synology metadata remain local configuration.
Changing a Drive root while recovery is enabled is blocked until the recovery
record is disabled and saved.

#### Security and deployment

State that the application password grants access to every configured profile,
profile keys are configuration identifiers rather than users, local profile
storage is plaintext protected by the operating system, and commercial provider
grants require separately approved encrypted server provisioning. Do not provide
credential export controls or direct users to paste local configuration into
Streamlit Cloud Secrets.

#### Acceptance criteria

Retained profile, credential, Drive, storage, recovery and readiness functions
remain discoverable. Security information exposes no credential export action.
The page does not imply per-profile permissions, DSM monitoring, or in-app restore.

## Component contracts

### Date control

Use explicit labels such as From, To, Date, Trip date, or As of. Preserve the
scope. Reports As of is never combined with the income-and-expense range.

### Hierarchy selectors

Display full hierarchy paths where used, preserve stable IDs behind labels,
sort category options by parent and category name, restrict category options by kind,
and include inactive accounts only for
historical edit contexts where supported. Transfer destinations must differ
from the source.

### Currency input and output

Use two decimal places for currency, default new transaction amounts to 0.00,
require a positive amount before saving, right-align displayed values, and
preserve the existing opening-balance sign conventions.

### Selectable table

Map every displayed row to a stable ID, make selection clear, preserve an
explicit edit selector, and do not add multi-row selection outside the
approved Bulk transaction workflow.

### File upload

Show accepted types and preserve input after non-write validation errors where
possible. Distinguish parsing failure, validation failure, Drive upload
failure, and ledger-link failure. Never expose file contents or secrets in
diagnostics.

### Prepared exports

Avoid repeated large payload generation, show preparation time, use Refresh
when a set exists, and invalidate or separate the set after a confirmed
workbook write.

### Ledger search

Use the Search transactions label, the exact field scope above, structured
filter AND semantics, result count, clear action, no-results state, and the
same profile/table-generation reset behavior as the Transactions contract.

### Bulk action bar

Hide it without a selection, show count, expose only valid actions, keep
export secondary, and show category-change and deletion review summaries
before writing.

### Receipt-on-create

Treat save, upload, and link as ordered stages. Never report a confirmed
transaction as unsaved. On partial failure show its saved identity and retry
path. Do not reset the form until the selected workflow completes.

### Success notice

Use concise confirmed messages such as Transaction saved, Category updated for
12 transactions, Permanently deleted 3 transactions; linked Drive files were
retained, Imported 24 transactions, and Latest cloud backup downloaded and
verified. Do not show success before confirmation.

## Durable non-goals and prohibited patterns

The current design does not include reconciliation, Tag hierarchies,
Tag-based financial reports, receipt-content search, cash-flow reports, per-profile user authorization, or
bulk changes beyond export, compatible Category/Payee/Tags editing, and
permanent delete.
Do not add them as implied badges, controls, status fields, or backlog entries
in this design specification.

Do not introduce marketing hero sections, oversized headings, excessive
whitespace, gradients, glassmorphism, neon accents, decorative animation,
oversized pills, unexplained icon-only controls, colorful charts without
semantic purpose, hidden critical errors, generic confirmation language, or
irreversible actions without explicit confirmation.

When implementing a UI change, inspect the current page implementation and
the relevant canonical guide, preserve every current function, prefer native
Streamlit controls, and verify success and relevant failure paths at the
supported desktop and narrow viewports. This is an implementation discipline,
not a PR or agent-review procedure; repository review rules belong in
AGENTS.md and .gemini/styleguide.md.


**Current behavior — commercial file selection :** Receipt and import
inputs share a labeled drop zone with a styled native Choose file(s) control.
Keyboard users retain the native file chooser. A single-file importer refuses
a multi-file drop; disabled workflows ignore drops. File-type/size validation,
read-only preparation, explicit import/upload commands and recovery stay with
the existing workflow owners. Selection never starts a ledger write or receipt
upload. Maps lookup buttons retain an 8px gap below their input.

## Invitation-only beta enrollment

**Current behavior:** My Businesses and first-use onboarding include the shared
beta notice below the business actions and forms, using existing surfaces and status text.
Asynchronous enrollment status must not shift controls during a pointer interaction.
Scope and service limitations sit in a keyboard-accessible disclosure. Business setup collects
U.S./USD or Canada/CAD. The review step states the fixed currency and that it
cannot change after creation. Canadian admission requires separate release/cohort approval. Create Business can return pending without creating a ledger. The
form preserves the exact request for explicit retry after approval, including
refresh recovery, and never treats a pending result as successful creation.
Named collaborators need separate operator approval and retain the existing
recipient-bound invitation/fresh-login workflow. An invitation lasts at most 48
hours, cannot outlive its approval, and displays its exact deadline during review. Customer messages explain
expiry, reapproval, capacity and pauses without exposing private cohort IDs.

Enrollment freezes do not remove existing business selection, membership-derived
access, customer export or closure rights. Notices state noncharging/invitation-only
scope, customer-owned Drive for beta receipts, excluded specialist features,
sensitive-data boundaries, best-effort support and unverified recovery targets.
Reviewed Terms/privacy, acceptance version and contact/retention policy remain
separate release requirements; no legal acceptance or mailbox is invented.
Operator controls have no tenant role or paid-plan UI. The
[admission procedure](docs/operations/BETA_ADMISSION.md) owns program state and
release handoff; it does not authorize external enrollment.
