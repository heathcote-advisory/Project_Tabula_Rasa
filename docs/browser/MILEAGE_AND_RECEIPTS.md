# Mileage and receipt workflows

Mileage records remain separate from financial transactions. Receipts use the
document service for byte storage and PostgreSQL for metadata and links.
These browser workflows reuse [protected forms](CONCURRENCY.md), existing mileage
services and source-owned document operations. See
[receipt recovery](../storage/RECEIPT_RECOVERY.md) for provider and byte recovery.

## Mileage

The log's **Hide filters** / **Show filters** button collapses only the filter
controls. Its summary keeps the selected dates/tag count visible; trips, totals
and CSV stay filtered. Expand to change or clear filters. Collapse preference is
remembered per page and authorized business/user scope in this browser tab when
session storage is available; navigating still resets filter values as before.

Open **Mileage** to view the business's log. Shared **View mileage log** /
**Add trip** / **Vehicle management** route links show the current workflow with weight and an underline,
wrap on narrow screens and retain the dirty-navigation confirmation. Add trip
is visible only to writers; detail routes remain associated with the log.

**From**, **To** and **Filter by tags**
align compact date controls with the top of the multiline tags list on desktop
and stack on narrow screens. They use one inclusive date/tag scope for the table, trip count, business-mile total
and **Download filtered mileage CSV**. Multiple selected tags match any selected
tag. The log preserves date, distance, vehicle, both locations, purpose, notes,
tags and stable identity. Read-only users can inspect a trip; writers can edit it.
CSV export prefixes formula-leading text with an apostrophe so spreadsheet
applications treat it as literal text; the stored trip remains unchanged.

Related create/edit fields share Trip details, Route and mileage, Tags, and
Purpose and notes groups. Optional Maps lookups use the shared field grid; route
guidance, calculation and failure messages span a complete row before editable
mileage, and the grid stacks on narrow screens. Purpose and notes are multiline
controls.
Save mileage trip is the primary action and Back to records retains the draft
navigation guard. The log wraps long locations, purpose, notes and tags, aligns
distance headings/values, and scrolls within its named keyboard-focusable region.
Tag listboxes expose keyboard guidance and the full selected labels.
Download filtered mileage CSV has a 16px gap before the log table, keeping the
action separate from the table header on desktop and narrow layouts.

**Add trip** captures the existing application-service fields. **Round trip**
initializes an independently editable return distance and creates two independent
records with reversed locations in one database transaction. Selected and inline
tags apply to both records. The return appears first when timestamps match.
Distances are normalized by MileageService to one decimal place; dates, positive
distance, required locations/purpose, tag validity and inactive history remain
service/repository rules. Mileage never creates financial transactions.

The detail/editor route loads its original baseline separately from editable
values. Save and confirmed permanent deletion keep the
[existing conflict/reload contract](CONCURRENCY.md). Background reads
cannot replace a loaded draft/token. A create with uncertain status keeps its
draft, disables another submission and directs the user to inspect the log;
neither creates nor edits retry automatically.

The browser preserves manual location and distance entry. Maps integration adds optional
Deployment-wide Maps lookup and transient route calculation; explicit review and
confirmation update the editable draft before Save persists confirmed records. mixed-use
source receipts extends the same receipt controls to mixed-use source ownership. See
[workflow ports](IMPORTS_EXPORTS_AND_INTEGRATIONS.md) for their transport,
configuration, conflict and verification contracts. QuickBooks mileage preview/import
under mileage imports is also available in Data & backup.

The entity-qualified transport provides:

- `GET /api/v1/entities/{entity_id}/mileage` with optional inclusive `start`/`end`;
- `GET .../mileage/{record_id}` for fresh detail and its baseline;
- `POST .../mileage` with declared fields, `round_trip` and optional `return_miles`,
  returning confirmed records and baselines;
- the existing protected `PATCH` and `DELETE` detail paths.

## Vehicle management

Open **Mileage → Vehicle management** to view this business's vehicles. Writers
can add a vehicle, edit its name, deactivate/reactivate it, or delete it when no
trip references it. The existing protected editor requires loaded baselines and
confirms deactivation/deletion. Read-only users can inspect the catalog.

Add/Edit trip uses a **Vehicle** selector, with **No vehicle** allowed. **Add a
new vehicle** creates and selects a catalog record without leaving the draft.
Inactive vehicles disappear from new selections; the current inactive vehicle
remains available when editing its trip. Renames keep the same identity and
appear in trip lists, details and filtered mileage CSV. Distances, round trips,
filters, totals and financial accounting remain unchanged.

The explicit forward migration maps each exact nonblank historical label once
per business. Case and spacing variants remain distinct: Vehicle management
shows quoted exact labels and stable references for deliberate resolution, never
automatically merging similar names. Original trip text is retained independently
of current display names. Explicitly choosing No vehicle on an assigned trip
clears that trip's reference and label; it does not delete the catalog vehicle.
Whitespace-only historical text remains preserved and
is shown as an unresolved label in Edit, where a vehicle may be selected. A
cross-business or inactive new reference fails at save. Referenced deletion is
rejected; deactivate instead. Import previews remain read-only; committed
QuickBooks mileage maps accepted vehicle labels into the same catalog atomically.

Vehicle transport reuses the managed-reference contract at
`/api/v1/entities/{entity_id}/vehicles`: GET lists the catalog, POST creates,
PATCH with a loaded baseline edits name/active, and protected DELETE refuses
referenced rows. Mileage commands carry `vehicle_id`; the returned `vehicle`
is the current display label. Committed source imports and explicit source
migration share the exact-label catalog mapper; preview never creates vehicles.
Catalog versions, audit capture, closure guards and selected-business request
context reuse their existing owners. See [canonical export](../accounting/EXPORT.md)
for current catalog/reference encoding and original labels.

## Transaction receipts

Choose **Receipts** on a saved ordinary transaction; its accessible name includes
the full record description. The page shows linked files, filename/size/type/integrity inspection and same-origin downloads. Receipt
actions for source-owned mixed-use/depreciation rows remain with their respective
source workflows; this slice does not provide a generic bypass for those rows.

Linked receipts, attachment and unreferenced recovery share section headers and
consistent action controls. Full filenames wrap, metadata disclosure is keyboard
accessible, and unlink guidance states that stored bytes remain. Read-only access
is labeled and retains downloads; unavailable storage has a reload/availability
surface without an attachment control. Selected-file states and partial-failure
guidance remain visible alongside the existing upload/link progress.

Drop supported PDF, PNG, JPG, JPEG, WEBP or HEIC files into **Receipt files**,
or use its styled native file chooser, then **Attach selected
receipts**. The complete selected batch is checked before any upload: nonempty
files, 20 MB per file, 200 MB per selection and at most 100 receipts per target.
The service also validates each upload and the repository enforces the reference
limit at every link mutation. An indeterminate progress indicator and phase text
identify uploading versus linking while controls are disabled.

Each selected file receives a stable portable ID before sending bytes. The
browser first uploads bytes, retains the confirmed document response, then links
its canonical ID. Confirmed links remain attached if another file fails.
An explicit upload retry reuses a freshly confirmed document with matching
canonical safe filename, type, size and checksum; filename normalization is shared
with storage adapters so reserved names also reconcile on retry. Conflicting reuse of its ID is rejected before
sending bytes. An unavailable inventory stops the upload for inspection/retry.
Overlapping HTTP uploads for the same business and document ID are refused with
a conflict while the first request is running; inspect the result before retrying.
The PostgreSQL guard coordinates API workers; it does not roll back remote files
or promise transactionality across storage providers.
Counts distinguish attached and unresolved work. **Retry unresolved receipts** reuses
the same ID/file after an upload failure, or retries only linking after a confirmed
upload. DocumentStore retains responsibility for bounded uncertain-upload
reconciliation. There are no automatic browser mutation retries. **Cancel recovery**
or leaving the page retains already uploaded files in **Unreferenced receipts**;
the unsaved-work guard protects pending selections during navigation.
Partial-transfer and cancellation notices distinguish confirmed uploaded files from selections without a
confirmed upload. Check **Unreferenced receipts** before selecting unconfirmed
files again; an uncertain upload may have stored bytes despite its failed response.

**Remove …** requires acknowledgement and removes only the relationship. The
file remains downloadable and can be linked again from **Unreferenced receipts**,
including to another eligible transaction. Failed links do not commit misleading
database metadata, and failed removes retain the relationship. There is no byte
deletion endpoint or simulated rollback of uploaded files.

The API exposes portable IDs and provenance, never provider locators, folders,
profile keys or open URLs:

- `PUT .../documents/{document_id}/content?filename=...` transfers raw
  `application/octet-stream` bytes using a prepared 32-character lowercase hex ID.
  Buffering is capped at the existing per-file limit. No multipart dependency or
  base64 expansion is required.
- `GET .../documents` lists recoverable unreferenced documents and the count
  requiring integrity metadata.
- `GET .../documents/{document_id}/content` resolves the ID in the authorized
  business and downloads bytes through DocumentStore, with attachment disposition,
  `nosniff` and private `no-store` caching.
- `GET .../transactions/{record_id}/receipts` lists linked metadata;
  `POST` at that path accepts only `document_id` to link;
  `DELETE .../receipts/{document_id}` unlinks it.

All routes use the existing server context, capabilities and unsafe-request
Origin/CSRF checks. IDs do not grant access. Ordinary receipt link/unlink contracts
are not loaded-baseline commands; their repository already performs fresh scoped
target/reference checks. They advance the affected transaction baseline, so a
previously loaded transaction editor cannot overwrite changed receipt links.
No document-metadata edit surface or new concurrency bypass is introduced.
Provider errors are translated to safe typed responses without raw exceptions,
filenames, content or credentials in logs/errors.

The selected runtime must supply the authorized DocumentStore and stable store
kind when composing services. Existing migrated profiles reuse the shared
[trusted authority/document binding](../accounting/CUTOVER.md). Unconfigured storage
returns a visible availability state with upload controls absent; the default
synthetic runtime does not silently configure Drive or invent a byte provider.
Production object-store selection and retention policy remain separately owned.

## Verification

Use a disposable PostgreSQL test administrator endpoint as described in
[PostgreSQL development](../database/POSTGRESQL.md#native-integration-tests). After regenerating
the contract and building `web`, run:

```powershell
npm --prefix web run build
npm --prefix web run lint
npm --prefix web test
$env:TABULA_RASA_REQUIRE_BROWSER_TESTS = '1'
python -m pytest tests/http tests/test_application_documents.py tests/test_mileage_service.py tests/postgres/test_http_mileage_documents.py tests/postgres/test_browser_mileage_documents.py tests/postgres/test_http_baselines.py tests/postgres/test_browser_baselines.py
```

Native tests exercise the compiled browser, real HTTP/service graph and UUID
PostgreSQL databases with a synthetic DocumentStore. They cover desktop/mobile
capture, round-trip/filter/export/detail/delete, loaded-baseline conflicts,
upload progress, partial link recovery without reupload, uncertain upload retry,
download, remove confirmation, relinking, validation, safe failures and two-entity
isolation. Hosted acceptance and real provider smoke testing remain distinct.

Reuse map: inspected MileageService and its adapter/internal forms, ReceiptService
and its recovery/DocumentStore contracts, PostgreSQL receipt targets, the existing
HTTP models/client, ProtectedEditor and the native browser harness. Extended these
owners with thin list/create/detail and canonical document transport adapters;
extracted the mileage field adapter from the old bookkeeping editor. Browser
receipt sequencing handles the HTTP calls while existing application/provider
contracts retain validation, persistence and uncertainty ownership. No parallel
domain, persistence, authorization or retry engine was introduced.
