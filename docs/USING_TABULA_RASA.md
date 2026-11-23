# Using Tabula Rasa

Bookkeeping runs in the browser application with named login and PostgreSQL
business scope. Operators manage local Google grants, folders and recovery
setup through the separately protected CLI. See [Setup](SETUP.md) and
[Google Drive](GOOGLE_DRIVE.md).

## Sign in and select a business

Use named login and My Businesses to open an authorized business. Create business
initializes new PostgreSQL books. Membership and role checks apply independently to
every request; another business's identifier grants no access. Switching business
clears the previous business's cached data and drafts after the supported discard
confirmation. Native business creation does not acquire Google consent or a grant;
follow the [operator-assisted connection procedure](identity/CONNECTIONS_AND_CLAIMS.md).

## Dashboard, accounts, categories and reports

Dashboard and Reports use server-calculated accounting totals. Date filters select
the reporting period and balance-sheet as-of date. Transfers do not affect income or
expenses, mileage remains separate and current earnings are included in equity.
Accounts and categories support two layers; family totals count each direct activity
once. Accounts and Categories support column sorting, with children kept beneath
their parent and visible indentation. Default order is Asset/Liability/Equity for
accounts and Income/Expense for categories, then name. Dashboard mirrors the hierarchy
without sort controls. Referenced records retain deletion guards and accounts deactivate rather than
delete. [Bookkeeping](browser/BOOKKEEPING.md) defines the supported forms and reports.

## Transactions, journals and protected edits

Use Add transaction/journal for ordinary income/expense, transfers and balanced
multi-line journals. The combined ledger preserves whole journal grouping. Ordinary
transaction copy starts with independent identities and no receipts or recurring
provenance. Descriptions use compact previews: hover for full text, or focus and
press Enter/Space (tap on touch) to open it. IDs stay searchable and included in CSVs
while hidden from standard Transactions and Mileage columns. Debit/Credit appear for
displayed journals; receipt counts use a paperclip. Top Back controls retain dirty-form
confirmation, and Mileage restores the originating filtered log. Confirmed bulk Category/Payee/Tag updates and deletion use loaded baselines.

If another client changes a loaded record, save/delete is refused and the draft stays
visible. Choose Review latest record, then Keep draft or Discard draft and reload;
review current values before resubmitting. Payee/tag merges protect every loaded
source and target. [Concurrency](browser/CONCURRENCY.md) and
[copy/bulk workflows](browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md) own these controls.

## Recurring and mixed-use expenses

Ordinary Expenses offer Make recurring; mixed-use sources have their own recurring
conversion and independent copy. Schedules retain stable occurrence identities and
catch up due business dates without duplicate entries. PostgreSQL worker authority
uses the existing queue; explicit generation in worker mode enqueues background work.
Startup and page loads never generate. Failed schedules remain inspectable and
retryable without blocking unrelated businesses or schedules. End permanently stops
future generation and retains history. Separate confirmed Delete is available only
when the sole linked occurrence is the original; it removes recurrence and keeps the
original expense/source and receipts as an independently editable entry. Additional
occurrences prevent deletion, including when generation wins a concurrent race.

Mixed-use expenses own a source record with total expense, business-use percentage,
allocated amount and rationale. Its ordinary Expense projection is read-only through
the generic transaction editor. Source edits and receipts preserve source/projection
parity. [Recurring and assets](browser/RECURRING_AND_ASSETS.md) specifies the workflows;
[jobs](operations/JOBS_AND_AUDIT.md) owns timers, authority and monitoring.

## Assets and mileage

Asset register workflows preserve acquisition links, useful life, overrides,
depreciation history and existing ledger effects. An owner's operational depreciation
deferral does not retire these rules or data. Mileage supports manual trips, configured
Maps lookup/routing and supported imports. Mileage → Vehicle management owns
this business's vehicle catalog; Add/Edit selects a stable vehicle, and renames
update linked display labels. Deactivate to retain history while hiding new choices.
Review suggested miles before saving.
Maps usage monitoring is read-only, explicit and deployment-wide; it is not a billing
meter or business Google grant. [Mileage and receipts](browser/MILEAGE_AND_RECEIPTS.md)
and [Maps setup](GOOGLE_MAPS.md) define provider requirements.

## Receipts

When a business has an authorized DocumentStore, ordinary saved transactions and
mixed-use source details offer attachment, inspection/download, unreferenced-file
recovery and confirmed unlink. Unlink preserves document bytes. Upload/link errors
preserve confirmed work and provide retry for unresolved files; do not re-upload a
confirmed receipt just because linking failed. Loaded-source conflicts require review
and reload before retrying. [Receipt recovery](storage/RECEIPT_RECOVERY.md) describes
separate database/document recovery and existing bounded provider recovery.

## Data and backup

Data & backup provides the supported CSV/QuickBooks imports and PostgreSQL exports.
Transactions offers Download selected ledger CSV, Download transactions CSV and
Download journal postings CSV. They preserve business scope, whole journal grouping
and literal formula-leading text. Complete canonical exports retain all supported
bookkeeping collections and provenance; audit history is excluded.

Accountant handoff is the second-to-last Data & backup section; Closure and privacy
requests is last, with red closure actions and the existing confirmation safeguards.

**Accountant handoff** lets Owner, Admin and Accountant download the selected
business's complete bookkeeping ZIP, instructions and checksum manifest. Choose
bookkeeping with all receipt files (default), or metadata only. Full handoff
includes verified unlinked uploads; missing or unverifiable receipt bytes prevent
delivery. Reconnect/recover receipts before retrying, or explicitly choose the
metadata-only financial archive. Audit history, credentials and operational
policies are excluded; records predating audit activation have no complete
mutation history. The page explains package/time limits. Nothing is retained as
a download link: save the ZIP privately; a retry requires current access and
reads a fresh snapshot. See the [handoff contract](browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md#customer-bookkeeping-and-accountant-handoff).

Operational PostgreSQL/database and document-byte backups follow
[NAS backup](operations/NAS_BACKUP.md) and [receipt recovery](storage/RECEIPT_RECOVERY.md).
Preserved Sheets archives are format 13 only. Their retained CLI recovery requires a
new blank target and refuses configured live workbooks. After PostgreSQL divergence,
never reactivate a stale Sheet or reload a frozen source as recovery.

## Imports

Preview validates rows without persisting changes. Confirmed imports retain stable
identities, duplicate prevention, business scope and failure guidance. See
[imports and exports](browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md) for current browser
controls. Supported source formats and accounting interpretation follow.

### Tabula Rasa CSV

Upload a CSV with the required Date, Type, Amount, Account, and Category
columns, plus optional Payee, Description, and Reference columns. Type must be
Income or Expense; this workflow does not import transfers. Review the first
20 rows, diagnostics, and duplicate summary before **Import rows**.

### QuickBooks transactions

Supported source exports are:

- QuickBooks Online Transaction Detail by Account CSV; and
- QuickBooks Self-Employed General ledger XLSX or a ZIP containing the
  supported workbook.

For Transaction Detail by Account, export the report with its headers and
remove title, subtotal, and footer rows before uploading. For Self-Employed,
enable General ledger under Settings > Export data and upload the ZIP or its
General_ledger.xlsx file.

The supported English-language source columns are Date, Transaction Type, Num,
Name, Memo/Description, Account, Split, and Amount.

| QuickBooks value | Import behavior |
| --- | --- |
| Account | Source account |
| Split on Income or Expense | Category |
| Split on Transfer or Funds Transfer | Destination account |
| Positive Amount | Income |
| Negative or parenthesized Amount | Expense |
| Name | Payee |
| Memo/Description | Description |
| Num | Reference |

Names match existing accounts and categories case-insensitively. A duplicate
has the same date, type, amount, source and destination accounts, category,
payee, description, and reference as an existing row or an earlier upload row.
Duplicates are skipped. The preview reports ready, duplicate, income,
expense, transfer, conversion-error, and validation-error counts. Fix
blocking errors before **Import validated rows**; valid rows are written in
one batch.

The optional **Automatically create missing accounts and categories** control
shows proposed additions before writing. New accounts use zero opening balance
and infer a subtype/classification; new categories infer Income from positive
amounts and Expense from negative amounts. New items are Layer 1 records.
Matching uses the stored name, so existing child records remain valid matches.

### QuickBooks mileage

In QuickBooks Online, open Expenses & Bills > Mileage, choose Download Trips,
and upload the CSV under Import QuickBooks mileage.

Recognized data includes trip date, classification, purpose, vehicle, start
address, end address, and distance. Only Business rows are imported.
Personal and Unreviewed rows are counted and skipped. A business row requires
a date, purpose, start address, end address, and positive distance. Explicitly
kilometer-labeled distance columns are converted to miles.

The preview shows ready, duplicate, personal, unreviewed, and validation-error
counts plus row details. A duplicate has the same date, distance, vehicle,
start address, end address, and business purpose as an existing or earlier
upload row. Review the preview before **Import validated mileage**.


## User-visible safety behavior

A successful save reflects a confirmed commit. Loaded baselines reject stale writes;
ordinary page loads do not generate occurrences or mutate books. Failed authorization,
profile binding or source validation fails closed. Private ledger values and provider
credentials never belong in logs or error payloads. Supported correction and recovery
procedures preserve historical identities, audit records and source artifacts.

## Invitation-only beta enrollment

New external businesses and named collaborators require individual operator
approval. Create Business may record a pending request without creating books;
keep the original name, country and request, then retry after approval. Offers
last up to 48 hours, bounded by their approval deadline shown during review, and
need a new approval/invitation after expiry or withdrawal.
An admission pause preserves existing authorized business access and export.
New U.S. businesses start in USD and Canadian businesses in CAD. Owner/Admin
can change the existing bookkeeping label under Business & members after explicit
confirmation, without converting any numeric amounts. Business country and admission
remain unchanged; there is no separate enrollment-currency setting. See [Business settings](identity/WORKSPACES.md#business-currency-settings).
Canadian enrollment still requires selected-release currency-safe
product verification. My Businesses shows beta status and limitations. See the
[admission procedure](operations/BETA_ADMISSION.md) for supported scope and release
requirements. Customer-owned Google Drive is required for beta receipts; reviewed
policies and the actual support channel must be provided before enrollment.
