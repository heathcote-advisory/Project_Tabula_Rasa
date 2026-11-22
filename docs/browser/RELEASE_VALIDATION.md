# Release validation and Streamlit transition

Automated browser journeys exercise synthetic fixtures through the compiled browser,
HTTP application services and disposable PostgreSQL. PostgreSQL owns ledger authority
and DocumentStore owns bytes; workflows must not depend on Streamlit session/rerun state.

Named-user hosted acceptance follows [hosted rollout](../identity/HOSTED_ROLLOUT.md),
including real-provider, rollback and manual browser/accessibility evidence. Owner use
follows [controlled cutover](../accounting/CUTOVER.md). These are separate from automated
regression coverage and the completed historical technical-alpha acceptance.

## Exact default workflow matrix

### Country and bookkeeping-currency acceptance

Before a selected Canadian cohort is authorized, record the exact release SHA,
image digest, database revision (including `0020_book_currency`), browser versions,
viewports and sanitized test outcomes in the owning release discussion. Keep
private cohort/identity/ledger evidence in approved custody, not Git or logs.
The release owner must verify:

- An exact operator-approved CA/CAD request creates one CAD book atomically;
  replay returns the same identity, and changed/forged pairs fail. US/USD and
  existing internal USD books retain identities, memberships and values.
- The initial three-business aggregate cap includes both countries; expansion,
  named-user approval and freezes remain independently enforced. A paused cohort
  retains authorized selected-business reads and customer exports.
- One named user switches between USD and CAD businesses with different roles
  on desktop/mobile. Currency, private drafts/cache, records, receipt access and
  download labels follow the selected business. Direct foreign-business access
  and unauthorized writes/exports fail; no membership is granted by switching.
- Owner/Admin can explicitly change an existing USD/CAD label in either direction,
  while other roles and stale/foreign-business requests fail. Confirm exact numbers,
  current exports/import validation and refreshed peer/switching context, with
  business country, approval and membership unchanged. No separate enrollment
  currency is retained.
- Representative opening balances, income/expense, transfer, journal, recurring
  and mixed-use activity retain exact amounts and signs. Mileage and transfers
  stay outside income/expenses; Assets = Liabilities + Equity remains balanced.
- Currency-free CSV/QuickBooks files are explicitly interpreted in the selected
  unit. Contradictory explicit currency fails preview and commit, including
  replay and paired workbook rows. Retained USD Sheets migration cannot target
  CAD. Financial CSV columns/values remain compatible and delivery identifies
  currency; canonical/customer exports validate CAD metadata and exact values.
- Inspect keyboard, screen-reader labels, narrow layouts and current-revision
  CI/review evidence. Verify a CAD-aware admission-compatible rollback image;
  do not relabel books or roll back to USD-only interpretation.

Synthetic regression coverage is an input, not hosted acceptance. Canadian
legal/privacy/provider eligibility, real identity/Drive/restart and independent
database/receipt recovery remain with their existing owners. The
[external-beta release gate](https://github.com/Roklion/Project_Tabula_Rasa/issues/348)
requires written GO for the exact release/cohort; code completion alone admits
no Canadian or other external customer and authorizes no deployment or migration.

Automated cases use a fixed synthetic identity and byte adapter. Hosted acceptance
applies these workflows to authorized named fixtures and the real configured provider
under hosted rollout; synthetic context or bytes cannot supply that hosted evidence.

| Required workflow | New-stack surface and owner | Release evidence |
| --- | --- | --- |
| Resolve business/reference setup | Server-scoped entity context; Accounts, Categories, Payees, Tags (entity and reference workflows) | Fixed synthetic test context and rejected foreign entity; create two bank accounts, expense/income categories, payee and tag. Local/native two-business switching and isolation remain in browser testing. |
| Ordinary transaction lifecycle | Transactions (core bookkeeping workflows and protected browser mutations) | Expense/income/transfer create, loaded edit, fresh confirmed delete; reference/tag selections; transfer excluded from income/expenses. |
| Journal lifecycle | Add journal/Journals (core bookkeeping workflows and protected browser mutations) | Unbalanced save disabled; balanced posting create, edit, grouped ledger read and confirmed delete. |
| Protected mutations | Existing loaded-baseline forms (protected browser mutations) | Two independently loaded browsers; stale edit and delete rejected, draft preserved, missing edit/delete baseline rejected, explicit latest-record reload. The broader reference/merge matrix remains in browser testing CI. |
| Ledger/core reports | Transactions, Dashboard/Reports (core bookkeeping workflows) | Four complete entries visible; report deltas include journal expense and exclude transfer; Assets = Liabilities + Equity; mobile ledger fits its scroll region. |
| Essential document flow | Saved transaction receipts (receipt workflows) | Synthetic fixture DocumentStore: upload/attach, exact-byte download, reference-only unlink, retained-file relink and final unlink. browser testing CI retains controlled partial-link/uncertain-upload/failure tests. |
| Runtime/release | Version, readiness, deployment (deployment) | Exact release SHA, anonymous restrictions, routing, real DB-backed account read; ordered schema step and compatible application rollback evidence. |
| Browser quality | Bounded browser testing suite plus selected hosted acceptance | Chromium/Firefox/WebKit, 1440x1000 and 390x844; current-candidate CI plus separate hosted results and manual device record. |

Mileage (mileage workflows) is available as an additional manual log, but is not
promoted into the default alpha gate. Recurring/mixed-use (recurring and mixed-use
workflows) and assets/depreciation (asset workflows) remain outside that gate. If a
release or live owner cutover owner matrix needs them, name the additional workflows and
require the corresponding completed slice and evidence.

## Automated journey and supported deployment boundaries

The hosted synthetic staging CLI and **Technical alpha staging gate** dispatch workflow
are retired. Do not restore Basic access on named staging to run historical tooling.
Historical exact-candidate acceptance, provider limitations, receipt persistence and
rollback evidence remain in the [owning technical-alpha
discussion](https://github.com/Roklion/Project_Tabula_Rasa/issues/288#issuecomment-6117319756).
That acceptance is unchanged and does not certify a new release.

The reusable journey lives in `tests/postgres/browser_journey.py` and is exercised by
`tests/postgres/test_browser_journey.py` against disposable real PostgreSQL, the compiled
browser, actual HTTP/services, a synthetic byte adapter and a second entity. Required CI
preserves Chromium/Firefox/WebKit at 1440x1000 and 390x844, bookkeeping and accounting
assertions, stale/missing-baseline mutations, receipt retention, delayed mutations,
failure handling and entity isolation. Helper guards remain covered separately. See
[browser testing](TESTING.md) for local setup and CI ownership. There is no hosted write
entry point in test support. Automated success does not establish real-provider,
retained-data, rollback, physical-device or manual accessibility acceptance.

[Development promotion](../operations/DEPLOYMENT.md) remains supported with restricted-alpha
authentication, its fixed synthetic identity and authenticated read-only smoke checks.
Target binding, explicit migrations and immutable-image verification retain their
existing owners. [Synthetic receipt storage](../storage/STAGING_RECEIPTS.md) remains
explicitly configured per environment with independent grant/profile/folder custody and
isolation/security checks. Native fixtures continue to use `TrustedDevelopmentContext`.
These consumers do not require the retired hosted acceptance runner.

## Compatible application rollback rehearsal

Reuse deployment's [promotion/failure/rollback
procedure](../operations/DEPLOYMENT.md#promotion-failure-and-rollback). Select a
verified retained prior digest with the same schema head and passing main CI, and
authorize a controlled staging maintenance window. Record current and prior
digests/release IDs, then dispatch the normal staging promotion to the prior compatible
digest. Its ordered migration check, registry/Render integrity, readiness and browser
smoke must pass. Promote the selected candidate again, verify the same evidence, and
repeat the selected named-user hosted acceptance checks. Retain both workflow links
and operator observations proving the retained DB/receipt data survived. A real rollback deployment is required;
mocks or a repeated current-image promotion do not rehearse rollback.

Do not introduce a bad bookkeeping write or destructive schema failure just to test
rollback. Do not automatically downgrade/stamp/restore the DB. Incompatible schemas
require the PostgreSQL persistence reviewed bridge/maintenance/forward-repair policy;
database/document disaster recovery remains platform operations and commercial recovery
agreement. An uncertain deploy stops for inspection under existing deployment rules.

## Feature routing and authority during transition

| Workflow | Browser/API availability | Remaining internal/reference path and tracker |
| --- | --- | --- |
| Accounts/categories/payees/tags, ordinary transactions/journals, ledger/core reports | Implemented new-stack routes; no redirect to legacy UI | The Streamlit bookkeeping pages and writers are retired; local operator administration remains.  |
| Ordinary transaction receipts | Implemented with [Owner self-service Google Drive consent](../identity/GOOGLE_DRIVE.md) on configured deployments | Operators configure the OAuth client and encrypted custody, and verify the selected hosted provider flow. Deployments without self-service use an [operator-assisted binding](../identity/CONNECTIONS_AND_CLAIMS.md). Asset acquisition receipts stay on the ordinary acquisition transaction. |
| Mixed-use source receipts | Implemented source-owned browser/API workflow; source-owned upload/link/download/unlink/recovery with protected source baselines | Outside the default alpha; selected grant/folder and hosted verification remain operator work. Generic projections stay read-only and recurring templates do not own receipts. See [workflow ports](IMPORTS_EXPORTS_AND_INTEGRATIONS.md). |
| Recurring schedules and mixed-use sources/projections | Supported operations: create/edit, schedule state changes, explicit reconciliation and source/projection management | Outside the default alpha. Independent copy/conversion is available under recurring conversion and source copying. Durable generation independent of page loads remains platform operations. See [secondary workflows](RECURRING_AND_ASSETS.md). |
| Fixed assets and depreciation | Supported operations: register/detail/edit, schedules, explicit due posting, overrides and retirement | Outside the default alpha. Acquisition transaction owns receipts; generated depreciation has no independent receipt/copy/import action. Additional mixed-use asset behavior is outside this scope. See [secondary workflows](RECURRING_AND_ASSETS.md). |
| Mileage log/filter/CSV/detail and Maps lookup/routing | Manual trip workflow and optional browser/API Maps integration, with explicit review and manual fallback | Configured Maps key restrictions and controlled provider validation remain operator prerequisites; QuickBooks mileage import is available under mileage imports. Mileage bulk actions remain proposed. See [workflow ports](IMPORTS_EXPORTS_AND_INTEGRATIONS.md). |
| Selected-ledger, transactions and journal-postings financial CSV downloads | Implemented; selected complete entries on Transactions and two full financial downloads on `/data` | Outside the default alpha; these existing CSVs do not replace the complete recovery export or an authorized customer portability artifact. See [workflow ports](IMPORTS_EXPORTS_AND_INTEGRATIONS.md). |
| Transaction CSV/QuickBooks imports; ordinary copy/bulk | Implemented, with read-only previews and explicit protected commands | Outside the default alpha; consume the delivering revision’s evidence before live cutover. |
| Category-template import and standalone report downloads | Excluded by the owner's October 11 scope correction | These routes are not planned; these are unimplemented feature ideas, not migration prerequisites. Existing category management and on-screen reports remain supported. |
| Sheets workbook/backup administration and restore | `/data` exposes financial CSVs; no Sheets administration or live-ledger fallback | PostgreSQL persistence and frozen Sheets source contract preserve frozen format-13 internal recovery; platform operations owns operational backup/recovery; complete entity export supplies complete PostgreSQL operator export and external release and customer lifecycle owns customer delivery. |
| Google/profile/local credential administration | Not a commercial browser workflow | identity and tenancy owns native onboarding/authorization; live owner cutover consumes authorized owner bindings. Do not port deployment-password/all-profile access as commercial identity. |

Existing workflow integration includes financial imports/exports, source-owned receipts,
recurring conversion, Maps and mileage imports. The selected owner-use scope requires
the applicable existing workflows before live cutover. The [workflow-port
guide](IMPORTS_EXPORTS_AND_INTEGRATIONS.md) records the delivered behavior; [browser and
API workflows](RUNTIME.md) retains implementation ownership and approved exclusions.
Local implementation and synthetic checks do not establish deployment, real-provider
verification or live-cutover approval.

PostgreSQL bookkeeping uses the supported browser/API surfaces and the existing
cutover authority contract. Retained Sheets tooling supports authorized format-13
source migration, archive recovery and local administration; it does not provide a
bookkeeping runtime. Unavailable workflows remain unavailable until implemented.
Do not route bookkeeping back to stale Sheets to finish an action, dual-write, or
re-enable Sheets after PostgreSQL divergence. Live owner cutover owns private live
execution; synthetic alpha performs no authority activation.

## Streamlit retirement criterion

**Current source boundary:** Streamlit bookkeeping pages, startup/page recurring
generation and obsolete preparation converters are retired. Retained local operator
administration still requires the flag, loopback bind, application password and
reauthentication. Format-13 migration/archive recovery, current PostgreSQL recovery,
Google grants/folders and forward schema history remain supported. This source change
establishes no merge/deployment or commercial release acceptance.


The commercial image already installs the API/PostgreSQL dependencies without
Streamlit; CI verifies HTTP import/schema construction in that image. Retiring
commercial dependence requires every workflow in the selected release/owner
matrix to have a supported browser/API/service path, tested failures/isolation,
configured durable dependencies and documented routing. Unknown required gaps
must stop that release, while explicitly excluded long-tail slices may continue
independently. No hidden session-state or page-load scheduling may remain.

For hosted Streamlit retirement, the deployment owner verifies the exact app
and its recovery dependencies before the approved shutdown. Record the app's
absence in the provider workspace and check the old public URL separately.
See [Streamlit Community Cloud app removal](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/delete-your-app).
The Google Cloud project owner verifies the shared OAuth client and its active
consumers, then removes the approved legacy authorized domain, JavaScript
origin and redirect URI. Reopen Branding and the client settings to confirm all
three entries are absent and the approved staging, production and loopback
entries are present. Preserve the shared client, scopes, credentials, existing
grants and archived source and recovery artifacts. Run the approved staging and
production [Drive connection checks](../identity/GOOGLE_DRIVE.md#troubleshooting-and-hosted-acceptance)
using the authorized test scope for each environment. Record sanitized provider
readback and connection results for the OAuth branding handoff.

Before disabling an entity's old writers, consume the supported migration,
acceptance/export, authentication and recovery boundaries for its release stage. Before
repository-wide removal of Streamlit, separately inventory all remaining internal
users/workflows, migrate or explicitly retire each, preserve source and recovery
artifacts, and update operator/user documentation and tests. technical-alpha validation
does not authorize package removal, source deletion or a big-bang cutover. The
[capability retirement
evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/377) records active
callers, owners, eligible cleanup, retained regression contracts and concrete conditions
for deferred removals. It consumes this routing matrix without adding a release or
owner-cutover prerequisite.

## Restricted-alpha support and retirement

Retiring hosted synthetic acceptance does not retire independently supported
development and test paths.
Provider recovery and wider security/operational acceptance remain separate.

| Capability | Current callers and retention condition |
| --- | --- |
| `http/alpha.py`, `alpha_documents.py` | `http/app.py` gates restricted hosted requests; `http/composition.py` loads the synthetic document binding. Retain focused HTTP/native document tests until the affected environments and document consumers have an explicit replacement or retirement decision. |
| `TrustedDevelopmentContext` | Composition supports loopback development and gated hosted synthetic scope; native API/browser fixtures reuse it. Retain development and isolation coverage until a supported replacement or explicit retirement. |
| `tests/postgres/browser_journey.py` and its native/helper tests | Reusable disposable-fixture journey; preserve all native cases and CI partitions. Hosted synthetic orchestration and dispatch are retired. |

The [single-use staging schema bridge](../operations/DEPLOYMENT.md#retired-staging-bridge-0012-to-0014)
has retired separately. Actual multi-profile authority and legacy writer retirement
still require controlled cutover evidence; synthetic handoff supplies no live authority.

## Acceptance record and outstanding evidence

Use this template for each newly selected candidate; it is not a status board. The
accepted technical-alpha result and approved exclusions are recorded in [technical-alpha
validation
evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/288#issuecomment-6117319756).
Do not carry those results forward as acceptance of a different release or stage.

| Evidence field | Required record for the selected candidate |
| --- | --- |
| Candidate | Reviewed release SHA, current required CI/reviews, immutable image/index/runtime digests and forward schema revision |
| Deployment | Exact approved environment/binding reference, promotion/smoke links and observed version/readiness |
| Drive custody | Independent staging grant and managed folders, protected installation and preflight; private bindings stay private |
| Receipt journey | Authorized upload/attach, exact-byte download, reference-only unlink, retained-file relink and restart persistence |
| Browser coverage | Current-head native CI for every engine/viewport; separate authorized named-user hosted workflow results under hosted rollout |
| Compatible rollback | Prior/candidate compatible image/schema, maintenance window, both smoke links, retained-data observations and candidate hosted checks repeated |
| Manual quality | Tester/date, actual browser/device versions, touch/keyboard, zoom/reflow, focus and the accepted accessibility scope; record exclusions explicitly rather than inventing observations |
| Provider/recovery limits | Actual plan/region/capacity/quota/access, DB/byte protection, observed limitations, accepted gaps and responsible owner |
| Routing | Every required workflow has a verified implementation/selected-runtime path or an explicit approved exclusion with an issue owner |
| Owner handoff | Applicable live owner cutover workflow/auth/recovery scope, operator authorization and evidence references; alpha is not live migration |

For each selected release, collect live-owner, hosted-identity, operational and
External-admission evidence separately. Keep results and pending decisions in the owning
release discussion, including accepted exclusions. Publication, deployment, provider
writes, live cutover and customer admission each retain their explicit authorization
boundary.

## External beta release decision

The invitation-only, noncharging beta needs a separate decision for an exact
deployment and cohort. Use the existing workflow matrix above, [admission
controls](../operations/BETA_ADMISSION.md), [hosted identity
acceptance](../identity/HOSTED_ROLLOUT.md) and [incident
drills](../operations/INCIDENT_DRILLS.md); this procedure introduces no new
admission service, test runner or deployment path. Keep the completed matrix,
private evidence references and human decisions in the owning release discussion,
not in a repository status report.

### Reconcile prior evidence first

Read the owning issues' current bodies, completion comments and linked reviewed
changes before requesting work. Record what was implemented, what was actually
observed, the accepted stage/revision/environment, and any transferred remainder.
An open issue can contain completed implementation and accepted internal operation;
a closed issue can retain a narrower acceptance boundary. Preserve both facts.

Reuse completed owner cutover and scheduled recurrence within their approved
scope. Do not repeat migration, reopen frozen Sheets writers or demand a fresh
cutover solely because external admission is being reviewed. Likewise reuse
completed lifecycle, policy acknowledgement, export, UI, support and provider
work. Review changes since the evidence revision and identify the specific gap
that requires a new check. Historical recovery drills establish their measured
outcomes; they do not establish a different environment's current protection.

For every row below record the accountable person, evidence reference, observed
revision/environment/date, result and remaining action. Use **PASS**, **FAIL**,
**UNVERIFIED**, **ACCEPTED NONCRITICAL EXCEPTION**, or **NOT APPLICABLE**. A pass
needs evidence covering the selected release, or an explicit rationale for reusing
unchanged evidence. Failed, missing or stage-mismatched evidence remains a gap.
Exceptions need owner approval, risk/rationale, mitigation and a review trigger;
not-applicable decisions need a scope explanation. Neither status can conceal an
essential security, recovery, customer-rights or legal protection.

### Required evidence matrix

| Gate and technical owner | Selected-release acceptance |
| --- | --- |
| Scope and cohort — [admission](../operations/BETA_ADMISSION.md) | Name the target environment, private approved identities/businesses, US/Canada eligibility and initial three-business aggregate cap; every collaborator needs named approval. Record actual program revision, freezes and `approval_required` mode. Expansion and self-service each need separate approval. Disclose included workflows and exclusions; advanced depreciation needs explicit qualification. |
| Candidate and runtime — [deployment](../operations/DEPLOYMENT.md) | Bind reviewed SHA, current required CI/reviews, immutable image/index/runtime digests, schema, actual deployed version/readiness, worker identity/compatibility and feature configuration. Check the selected target rather than assuming staging and production match. A successful manual recovery is not successful unattended release acceptance. |
| Identity and business isolation — [authentication](../identity/AUTHENTICATION.md), [authorization](../identity/AUTHORIZATION.md) | Verify Create Business/My Businesses without a Workspace step, exact invitations, distinct roles in two businesses, switching, forbidden direct reads/writes/exports/receipt bytes, stale tabs/caches, membership removal, logout/revocation and restart. Consume accepted adversarial tests and require applicable real-provider/session-store/configuration-epoch failure/restoration evidence for the selected topology. |
| Account and administrator recovery — [hosted rollout](../identity/HOSTED_ROLLOUT.md) | Verify usable user password/factor recovery, mail delivery or a tested supported alternative, administrator recovery/custody and actual plan limits. An accepted synthetic-staging lockout risk is not beta recovery acceptance; no provider purchase alone proves recovery. |
| Bookkeeping and usability — workflow matrix above and [browser testing](TESTING.md) | Qualify selected transactions, transfers, journals, recurring/mixed-use, receipts, mileage/imports and reports. Preserve accounting signs, separate mileage/transfers from income/expenses and verify Assets = Liabilities + Equity. Consume delivered table/hierarchy/navigation/accessibility improvements; record current desktop/mobile, keyboard/touch and failure-path observations without repeating unrelated UI implementation. |
| Drive connectivity and bytes — [Google Drive](../identity/GOOGLE_DRIVE.md) | Consume accepted browser Connect/Reconnect/Disconnect and two-business receipt/restart evidence; verify applicability to the target client, custody backend and release. Preserve original account/folder/receipt bindings. Include authorized exact-byte download, reference-only unlink and retained-file recovery; a Connected badge alone is insufficient. |
| Portable export — [customer delivery](../accounting/EXPORT.md) | Reuse the implemented authorized customer handoff; verify selected-business bookkeeping and document-inclusive delivery, missing-byte failure and revoked/foreign access denial. Respect documented audit exclusions. Do not rebuild the exporter or label metadata-only output as complete receipt recovery. |
| Retention, rights and policies — [lifecycle](../operations/CUSTOMER_LIFECYCLE.md), [publication](../legal/READINESS.md) | Consume implemented closure/export/recovery windows, privacy intake, immutable evidence and policy-version acknowledgement. Obtain outstanding legal/owner decisions, accurate retention/backup limits, reviewed public Terms/Privacy and deployed acknowledgement. Verify applicable OAuth branding/publication; preserve existing NAS generations and frozen sources. No unimplemented destruction or immediate erasure promise. |
| Independent protection and restore — [database recovery](../operations/DATABASE_RECOVERY.md), [receipt recovery](../storage/RECEIPT_RECOVERY.md) | Record actual operating DB, receipt-byte and credential-ciphertext/key protection separately, successful capture ages, independent custody, isolated restore integrity and measured full-service recovery time. The approved targets are DB RPO ≤24h, receipt-byte RPO ≤24h and full-service RTO ≤48h; do not claim them from SQL-only dumps, Cloud Sync alone, or a restore command's duration. Wednesday/Sunday archives have 72/96-hour intervals and cannot alone meet 24h RPO. An unresolved essential protection or unsupported recovery promise blocks GO; any revised objective needs an explicit owner decision and corresponding disclosures/evidence. |
| Capacity, cost and support — [observability](../operations/OBSERVABILITY.md), [feedback](../operations/BETA_FEEDBACK.md) | Record selected provider plans/regions, quotas, headroom and budget exposure, backup failure/staleness and NAS-offline detection limits, incident/recovery delivery, named primary/alternate or accepted coverage limits. Reuse verified mailbox receipt/reply and restricted custody; support targets are best effort, not a 24/7 SLA. Consume accepted Maps restrictions/probes and explicitly recorded deferred checks. Do not invent real customer research or cost measurements from synthetic exercises. |
| Pause, incident and rollback — [admission](../operations/BETA_ADMISSION.md), [incident response](../operations/INCIDENT_RESPONSE.md) | Verify separate creation/invitation freezes and per-business suspension while existing authorized reads/exports remain available. Treat security containment separately from enrollment pause. Record incident communication and a verified compatible rollback preserving current schema, admission enforcement, identities, receipts and financial history; no Sheets fallback. |
| Authority and final owner decision — [controlled cutover](../accounting/CUTOVER.md) | Preserve completed trusted owner authority and its accepted limits. Identify whether the proposal activates commercial-production authority and obtain applicable recovery/operator/cutover sign-offs before that activation. Record legal, security and recovery reviewers' dispositions plus explicit owner GO/NO-GO for this cohort only. Free-beta approval does not authorize charging. |

### Decision and execution handoff

Record the decision's owner/date, exact target/release/schema and private cohort
reference, approved workflows/data/geographies, all evidence and exception
dispositions, support contacts, stop conditions and rollback/resume authority.
Unknown target or cohort, unresolved essential protection, unreviewed policy,
missing required current-revision checks or missing explicit admission approval
means **NO GO**. Hold admission freezes; do not reinterpret readiness smoke or
trusted internal operation as permission to invite external customers.

Before closing the gate, review the retained identity and Drive download fault
launchers after the applicable drills are accepted. Inspect callers/shared helpers
and preserve ordinary provider-failure and two-business isolation coverage. Remove
launchers, exclusive test helpers and launcher-only instructions when no consumer
remains; otherwise record the concrete recurring consumer, responsible owner and
reconsideration trigger. A selected deferred removal needs a linked cleanup issue.
Historical drill evidence and recovery artifacts remain preserved. Source cleanup
does not authorize fault injection or deployment.

After written GO, use existing operator controls only within the authorized target
and cohort, record the actual admission outcome and monitor the agreed stop
conditions. A material release/configuration/cohort change requires review of the
affected evidence and renewed authorization before expansion or resumption.
