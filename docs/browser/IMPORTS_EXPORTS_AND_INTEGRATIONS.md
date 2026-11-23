# Imports, exports and integrations

This guide covers financial CSV downloads, transaction and mileage imports,
copy/bulk actions, mixed-use source receipts and optional Maps integration.
Each action reuses its domain service, selected-business scope and protected
mutation boundary. Financial CSVs do not replace the
[complete recovery export](../accounting/EXPORT.md). Live use requires configured
dependencies and the separate [cutover procedure](../accounting/CUTOVER.md).

## Current business-currency source interpretation

Each business has immutable USD or CAD books. The shell, forms and reports show
the server-selected book currency; imports never infer a target from a filename,
locale, dollar symbol or browser header. A currency-free source is interpreted
in that book's currency. Verify the source book before committing; a retained
USD Sheets source must not be imported as CAD or relabeled to bypass conversion.

CSV and QuickBooks imports recognize optional `Currency`, `Currency Code` and
`currency_code` columns (case-insensitive). Every nonblank explicit code must
match the selected book. Contradictory/unsupported codes fail both preview and
fresh commit validation, including completed-batch replays. XLSX/ZIP General
Ledger conversion retains explicit currency from both paired rows rather than
dropping it. Import identity, duplicate prevention, reference creation and batch
atomicity keep their existing per-business owners. No FX or cross-business merge
is supported. Historical Sheets migration/reconciliation remains USD-only.

## Financial CSV actions

Transactions offers **Export selected**. Checkboxes select logical entries only;
selecting a journal includes its parent and every ordered posting. Downloads
retain the visible selected-entry order. Changing filters/search/sort or the
displayed identity set clears the selection. The API freshly checks all requested
IDs against the structured filter scope and refuses a missing/filtered/deleted
entry as a whole, rather than returning a partial file. No selections means
the button is disabled; a direct empty API selection returns headings.

Data & backup offers **Download transactions CSV** and **Download journal
postings CSV**, covering the selected business's complete financial ledger,
including inactive reference labels. Mileage remains in its own CSV. Full
transactions include transfers without converting them into income or expenses.

| Existing action | Ordered fields and empty behavior |
| --- | --- |
| Export selected | Date, Type, Account, To account, Category, Payee, Amount, Tags, Description, Receipt, Reference, ID. When journals are present, Debit and Credit follow Amount; posting amounts appear only in their side column. Empty selection returns the ordinary headings. |
| Download transactions CSV | ID, Date, Type, Account, To account, Category, Payee, Description, Reference, Amount, Tags. No journal or mileage rows; empty ledger returns these headings. |
| Download journal postings CSV | Entry ID, Date, Memo, Posting ID, Position, Account type, Account ID, Account, Debit, Credit, Payee, Description. One row per ordered posting; empty ledger returns these headings. |

Full exports retain existing leaf account/category names; selected exports retain
hierarchical display paths, mixed-use/recurring labels and receipt indicators.
Existing numeric presentation is preserved, including signed values and blank
opposite-side cells. UTF-8 CSV quoting preserves commas, quotes and multiline
text. Formula-leading text receives an apostrophe; numeric cells keep their
signs. Filenames include `USD` or `CAD`, and API responses include
`X-Book-Currency`; the existing CSV columns stay unchanged. Downloads neither mutate stored values nor require a DocumentStore.

The three read-capability endpoints are `GET .../exports/transactions.csv`,
`GET .../exports/journal-postings.csv` and `GET .../ledger/selected.csv` beneath
`/api/v1/entities/{entity_id}`. The last accepts repeated `entry_ids` and the
existing structured ledger filters. The browser sends selected IDs and filters in
a JSON body to `POST .../ledger/selected.csv`, preserving read capability and
requiring Origin/CSRF, so large selections do not exceed request URL limits.
Responses are private, uncached attachments;
failed reads show safe errors and never manufacture a file. Scope/navigation
changes abort delivery of a pending browser download.

For full-entity recovery artifacts, see [canonical bookkeeping export](../accounting/EXPORT.md).
Customer and accountant ZIP delivery is described below.

## Customer bookkeeping and accountant handoff

Data & backup → **Accountant handoff** offers a ZIP to Owner, Admin and Accountant
members (`bookkeeping:read` + `bookkeeping:export`). Editor and Read-only retain
ordinary CSV downloads. `POST /api/v1/entities/{entity_id}/exports/handoff` requires
the named session, current selected-business membership, Origin and CSRF. Its
body chooses `scope: documents_inclusive` (the UI default) or `metadata_only`.
Extra fields and arbitrary audit scopes are refused.

`bookkeeping.json` is the validated canonical v2 export, including all 27
collections, stable vehicle identities and original mileage vehicle labels.
`README.txt` explains the handoff. `manifest.json` is
`tabula-rasa-customer-handoff` version 1: business, snapshot time, canonical
version, scope, diagnostics, SHA-256/file sizes and an attachment map from document
IDs/original names to safe generated paths. Checksums detect corruption, not
authorship. Separate CSV downloads supply readable transaction and journal tables.
This ZIP is not an automatic restore format or a database/document backup.
Generated attachment paths retain only allowlisted receipt extensions (PDF,
PNG, JPG/JPEG, WEBP and HEIC), so extracted files open normally without accepting
user-controlled path components.

Documents-inclusive requires a working scoped DocumentStore and includes all
canonical documents plus verified unlinked provider-inventory files, marked
`unlinked_inventory` in the attachment map. Such uploads may not yet have a SQL
row; their attachment metadata lives in the handoff manifest. Canonical JSON is
unchanged. ReceiptService/DocumentStore retain authorization and provider retry
ownership. Every file must match its known size/digest. Missing integrity
metadata requires explicit backfill; disconnected providers, missing/deleted or
changed bytes, unverified inventory and quota failures stop delivery before any
ZIP bytes are returned. Metadata-only contains no receipt bytes. Provider URLs
are not portable attachments or guaranteed future access.
ReceiptService resolves the scoped association/provider inventory once per
package. Byte reads reuse those references; Drive checks each file's current
profile/folder membership with a targeted metadata GET, and managed storage
retains its version-pin checks. Neither path rescans the full inventory per file.

Audit events are explicitly **excluded**, including read-only and import-batch
events. Identity, grants, secrets, jobs and operational policies are excluded too.
The manifest records audit activation and states that pre-activation history is
unavailable; existing records are never represented as having complete mutation
history. This preserves the restricted AuditReader policy. Customers cannot
request arbitrary audit scopes or another business's history through export.

Preparation is synchronous: at most 50,000 canonical records, 50,000 attachments,
200 MiB of uncompressed package content and five minutes of file preparation,
with at most two preparations/deliveries per API process. Unsupported sizes fail without
truncation or automatic retry. The UI shows progress/error states and suggests
checking receipt access/integrity and limits before explicit retry. A retry reads
a new coherent ledger snapshot; receipts are checked against that snapshot and
fresh scoped inventory. These supported limits avoid requiring an export queue.

Archives use an anonymous OS temporary file: owner-only on POSIX, inherited
service-account temporary-directory ACL on Windows. Deploy with a private,
encrypted ephemeral temp volume and adequate quota, outside public/static/shared
directories. The handle closes on failure, stream completion/cancellation or
process termination. No persistent URL, retained server copy or reusable link
exists, so no link-expiry or cleanup job is needed. Each request authenticates
anew; expired sessions and revoked members are denied. Session, selection and
capability are rechecked after provider reads and before streaming. Bytes already
handed off cannot be recalled. Private no-store responses and shared browser
abort/blob-URL cleanup protect navigation and business changes.

Append-only `customer_export_actions` reuse the existing audit capture writer
with named actor, business, correlation and source `customer_export`. `requested`
commits before preparation; `available` commits before streaming and means the
authorized response was released, **not proof the browser saved every byte**.
`failed` is recorded when database/membership still allow it; otherwise a lone
request remains unconfirmed. Export data, filenames, provider errors and
credentials never enter evidence or application logs. Apply the forward schema
migration before deployment; startup never migrates. This implementation grants
no beta admission, production activation or private-export publication authority.

## Mixed-use source receipts

Choose **Receipts** on a saved mixed-use source detail. The ordinary receipt screen owns
the common file validation, stable upload IDs, upload/link progress, download,
reference-only removal and unreferenced-file recovery controls. Mixed-use endpoints bind
that workflow to the source owner. Source and financial projection read one native link
set; generic transaction endpoints refuse Source-owned mutations. Recurring templates
have no receipts. Generated occurrences own independent source receipts. Acquisition
receipts stay with their ordinary acquisition transaction; depreciation has no parallel
workflow.

`GET .../mixed-use/{record_id}/receipts` lists metadata. `POST` accepts
`document_id` and the loaded source `baseline`; `DELETE .../receipts/{document_id}`
accepts the baseline. The native repository locks and validates the complete
source/projection pair before metadata/link writes. Confirmed responses return
the source with its advanced baseline, allowing the next file in a batch to use
that confirmed token. Background reads never replace the original loaded token.

A stale source stops further batch uploads. Pending files and confirmed uploads
remain available; **Review latest source** / **Reload source** explicitly accepts
the latest saved source before **Retry unresolved receipts**. Retry after a
confirmed upload sends only the link. Storage owns one bounded uncertain-upload
readback and safe retry; the browser never retries mutations automatically.
Removing a reference leaves bytes recoverable. No simulated rollback deletes
an uploaded file after a failed link.

Native documents validate opaque provider locators at their metadata boundary.
Financial source edits preserve the native link set while shared domain
preparation validates financial terms. The frozen Sheets/backup Drive-reference
decoder remains unchanged. Entity-qualified requests, capabilities, Origin/CSRF,
original profile grants and managed receipt-folder bindings remain enforced.

## Optional Maps mileage

With separately restricted deployment-wide keys, Mileage offers **Starting
location lookup** and **Destination lookup** alongside editable recorded-text
fields. Choosing a suggestion supplies transient Place IDs. **Calculate mileage**
returns the primary driving distance; round trips call each direction separately.
The browser displays Google Maps attribution, provisional editable distances,
and an explicit **I reviewed the recorded locations and mileage** confirmation.
Changing recorded terms clears that confirmation. **Save mileage trip** alone
persists confirmed user records; lookup IDs, raw responses and route suggestions
are absent from the persistence payload.

Changing a lookup clears its selected Place ID and unchanged calculated drafts;
manually edited values survive. Manual entry remains available with unconfigured
Maps, failed lookup, quota exhaustion, unavailable provider or no usable route.
A successful outbound with failed return remains visible with clear manual-return
guidance. Return distance is independently editable. Protected edit/delete
baselines and mileage's separation from financial transactions remain unchanged.

`GET .../mileage/maps` returns only the browser key, availability and optional
region. `POST .../mileage/routes` accepts two selected Place IDs and `round_trip`;
it returns transient distances and a safe failure code without writing records.
Both enforce selected-entity context; route requests require write capability,
Origin and CSRF. Routes keys and human OAuth tokens never enter these DTOs.

The shared runtime accepts `TABULA_RASA_MAPS_BROWSER_API_KEY`,
`TABULA_RASA_MAPS_ROUTES_API_KEY`, `TABULA_RASA_MAPS_ROUTES_TIMEOUT_SECONDS`
(default 15) and optional two-letter `TABULA_RASA_MAPS_REGION_CODE`. A migrated
runtime instead consumes the trusted app-wide `google_maps` table, including
existing `autocomplete_region_code`. Equal browser/server keys suppress browser
exposure. No profile-specific key fallback or Maps quota-grant reuse is added.

Follow [Google Maps setup](../GOOGLE_MAPS.md#commercial-browserapi-configuration) before
enabling provider requests. Maps key restrictions owns deployment restriction
verification: use exact website/API restrictions for the browser key and a Routes-only
server key with stable-egress IP restriction when available, or the explicitly accepted
quota/monitoring alternative. Provider configuration, rotation and a controlled
non-sensitive live request remain operator work; synthetic tests prove none of those
hosted conditions.

### Google Maps API usage

The authenticated sidebar includes a compact **Google Maps API usage**
expander on desktop and mobile. Open it to check setup; select **Load usage**
to query the dedicated read-only monitoring identity. **Refresh** bypasses only
the 120-second monitoring cache. There is no polling or provider request on page
load, expansion, navigation, focus or reconnect. A report can remain visible
after its cache expires, with a stale warning and explicit refresh. Signing out
or switching business discards panel state and late results.

Routes API and Places API (New) have separate compact endpoint rows. Rates use
the latest sample from a 15-minute lookback, converted to the quota's units.
Unlimited limits remain **Unlimited**. A successful Monitoring response with no
matching points shows zero; failures show guidance rather than zero. Google
can report several minutes late, including up to approximately 240 seconds after
sampling. This panel is neither a real-time remaining-request counter nor a
billing/spend meter.

`GET .../maps/usage` checks setup without contacting Google.
`POST .../maps/usage` accepts only `{ "refresh": false }` (or `true`) to load the
display. Both require normal authentication, read capability and authorized
selected-business context before accessing monitoring. POST also requires
Origin and CSRF. Only display titles, compact rows, safe guidance and cache
lifetime cross the API; project/user dimensions and credentials stay server-side.

The reader is deployment-wide, separate from browser/server Maps keys and every
business's Google OAuth grant. Missing monitoring configuration does not disable
mileage. See [optional HTTP quota monitoring](../GOOGLE_MAPS.md#optional-http-quota-monitoring)
for both runtime composition paths, operator configuration, later controlled
production verification and what synthetic/image validation cannot establish.
## Imports in Data & backup

**Data & backup** (`/data`) has independent uploaders for Tabula Rasa transactions,
QuickBooks transactions and QuickBooks mileage. Preparation, preview and import
require the selected business's import grant (Owner, Admin or Accountant), in
addition to read/write access. Editor access alone does not expose uploaders.
The existing restricted synthetic development context also permits imports;
its loopback/hosted gate and prohibition on migrated profiles remain in force.
Staging a file does not write.
Choose one file with the native picker or drop it into the labeled upload zone.
A multi-file drop is refused before preparation.
Source tables show at most 20 Tabula Rasa CSV rows or 50 QuickBooks rows.
Changing files/options invalidates their earlier preparation. An explicit import
action commits only the selected file to the selected business. Files are UTF-8
CSV or the supported workbook formats below, at most 25 MB and 1,000 source rows
per batch. Larger sources must be split. ZIP/XLSX expansion and worksheet size
are bounded before conversion. Headers must be unique and nonempty.

- **Tabula Rasa CSV:** Date, Type, Amount, Account and Category are required.
  Payee, Description and Reference are optional. Type is Income or Expense.
  Account/category names refer to existing compatible references; this format
  does not import Transfers or journals. Preparation shows source cells and file
  diagnostics, preserving the internal workflow's actual preview. Domain/reference
  validation happens on the explicit **Import rows** command.
- **QuickBooks transactions:** Transaction Detail by Account CSV, or
  Self-Employed General ledger XLSX with its `General Ledger` worksheet, or a ZIP
  containing exactly one such XLSX (excluding macOS metadata). Preview preserves
  Income/Expense/Transfer counts, ready/duplicate counts, duplicate row numbers,
  conversion and validation errors, and optional account/category proposals.
  Auto-created accounts use the existing inferred type/classification and zero
  opening balance; categories use the existing inferred Income/Expense kind.
  Correct all conversion/validation errors before **Import validated rows**.
- **QuickBooks mileage:** Download Trips CSV with the existing parser's Date,
  Type, Trip Purpose, Vehicle, Start Address, End Address and Distance aliases.
  **Preview mileage import** shows ready, duplicate, Personal, Unreviewed and
  validation counts with row diagnostics. Only unique valid Business trips are
  eligible; distances use the existing miles/kilometres conversion and one-decimal
  normalization. Errors disable **Import validated mileage**, which requires
  confirmation. Mileage never writes financial entries.
  Preview duplicates reflect current trips; the commit also checks durable import
  provenance, so its confirmed imported count may be lower after records changed
  between preview and submission.

Each accepted PostgreSQL import is atomic: transactions and any proposed
references, or mileage records and import provenance, commit together. A known
failed batch leaves no partial import. The repositories revalidate fresh entity
references/duplicates inside the mutation boundary and retain stable identity
and durable import provenance. An explicit repeat skips already imported rows,
including after an uncertain response; the browser never retries automatically.
CSV replay protection applies to the identical committed batch; it preserves the
existing per-row semantics rather than adding a new duplicate policy. QuickBooks
duplicate rows are skipped. A confirmed API result supplies the imported count and
IDs. An unavailable/lost response does not prove rollback: inspect the appropriate
ledger/log and refresh preparation before an explicit retry. Corrected drafts and
file selections survive failures; no fallback to Sheets is offered.

The transport accepts raw `application/octet-stream` bytes with the existing
Origin/CSRF and server-resolved entity checks:

| Command below `/api/v1/entities/{entity_id}` | Behavior |
| --- | --- |
| `POST /imports/csv/prepare` | Read-only source preparation |
| `POST /imports/csv` | Confirmed ordinary CSV batch |
| `POST /imports/quickbooks/preview?filename=...&auto_create=...` | Read-only conversion/domain preview |
| `POST /imports/quickbooks?filename=...&auto_create=...` | Confirmed QuickBooks transaction batch |
| `POST /imports/quickbooks-mileage/preview` | Read-only mileage preview |
| `POST /imports/quickbooks-mileage` | Confirmed mileage batch |

Raw provider exceptions, file contents and source values never enter request logs
or error envelopes. Authorized preview responses show the source and diagnostics
only to the selected business. No file is stored as an alternate live ledger.

## Ordinary copy and bulk actions

In **Transactions**, entering Bulk mode reloads transaction baselines and then
refreshes the visible ledger before enabling selection. A failed refresh leaves
selection unavailable; subsequent concurrent edits still reject as stale.
Explicitly select visible complete logical entries. Selection
stores each stable ID and its original loaded baseline; filter/search/sort changes
clear the selection. **Copy transaction** requires exactly one eligible ordinary
transaction. The service validates its current baseline and prepares an independent
draft with its date/fields, active Tag associations and exactly one `Copy From
<source ID>` memo suffix. Recopying removes older generated copy suffixes. Receipt
references and recurring ownership are excluded. Saving uses a new stable identity
and fresh references; later changes to the original do not rewrite the draft.
Cancelling an untouched prefilled copy still requires confirmation.

**Apply changes** permits only explicit Category, Payee and complete-set Tag
replacement. Category requires a homogeneous Income or Expense selection; mixed kinds
and Transfers cannot change Category. Untouched fields are omitted; clearing Payee/Tags
requires explicitly applying the field. **Permanently delete selected** shows the
count/amount and requires acknowledgement; receipt files survive removal of their
references. Journals support complete-entry selected export through financial CSV
export; generic copy/bulk mutation is disabled. Mixed-use and depreciation projections
remain with their source owners.

`POST /transactions/{record_id}/copy-draft` accepts the loaded baseline. The
`POST /transactions/bulk/update` and `/bulk/delete` commands require the complete
selected ID set and one loaded baseline per ID. Duplicate/missing IDs, extra or
missing baselines, stale rows and protected sources reject the entire operation.
PostgreSQL bulk mutations are atomic, with no silent subset application. Failed
mutations invalidate affected cached views and preserve the complete submitted
selection/draft for review, even when refreshed data no longer contains a selected
row. Actions remain disabled after failure. Leave Bulk mode and load a fresh
selection before another attempt. Confirmed
success closes Bulk mode and retains its result/count on the ledger; uncertainty
requires inspection before another command.

## Mixed-use source copy and Expense conversion

Choose **Copy mixed-use expense** on one complete source to open
`/mixed-use/{recordId}/copy`. The service prepares the source fields, active Tags
and `Copy From <source ID>` provenance; receipts and recurring linkage are excluded.
Saving creates new source/projection IDs together and normalizes allocation again.
An edit to the original after preparation does not change the independent copy.
The prefilled draft and cancellation use the shared create-editor safeguards.

Choose **Make recurring** on an eligible ordinary Expense to open
`/transactions/{recordId}/recurring`. The form loads a fresh Expense/baseline and
uses the existing ordinary Expense fields and recurring schedule cadence. It
creates the schedule and links the existing Expense as its initial occurrence in
one operation; it does not add a duplicate initial expense. Income, Transfers,
derived records and already-linked Expenses reject conversion. The original
baseline travels with **Confirm recurring conversion** through
`POST /recurring/from-transaction/{record_id}`. A stale original rejects the
operation. Stable schedule identity survives failed responses; uncertain status
retains the draft and requires inspection before retry/cancellation. Completed
commands close their mutation surface rather than offering another same create.
Existing receipt references on the converted Expense remain owned by that Expense.

## Service ownership and verification

`application/financial_csv.py` prepares financial CSVs from the selected ledger.
Receipt services and the selected `DocumentStore` own attachment and recovery.
`GoogleRoutesClient` computes route distance, and `MileageService` owns saved trips.
Transaction, mileage, mixed-use and recurring services own import, copy, bulk and
conversion rules. Their PostgreSQL repositories enforce fresh references, atomic
writes, provenance and business isolation. Browser forms use the shared protected
editors and keep drafts scoped to the selected business.

Run affected service, HTTP and native browser tests using synthetic providers
and disposable PostgreSQL. Verify exports for empty and populated ledgers,
receipt-link recovery, Maps failure and manual entry, import replay, stale baselines,
and independent business scope. Follow [development validation](../DEVELOPMENT.md#required-validation)
for local commands and CI ownership. Use [release validation](RELEASE_VALIDATION.md)
for hosted workflow evidence and provider checks.

Historical implementation results are recorded in the [financial CSV, receipts and Maps PR](https://github.com/heathcote-advisory/Project_Tabula_Rasa/pull/368)
and the [import, copy and recurring conversion PR](https://github.com/heathcote-advisory/Project_Tabula_Rasa/pull/367).
