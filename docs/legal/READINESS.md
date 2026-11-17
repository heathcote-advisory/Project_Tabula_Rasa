# Customer policies and publication

This guide owns disclosure inventory, version acknowledgement and publication
review. [Customer lifecycle](../operations/CUSTOMER_LIFECYCLE.md) owns preservation
and rights handling; [beta admission](../operations/BETA_ADMISSION.md) owns cohort
authority. The [launch EPIC](https://github.com/heathcote-advisory/Project_Tabula_Rasa/issues/224)
under the [platform initiative](https://github.com/heathcote-advisory/Project_Tabula_Rasa/issues/124)
distinguishes internal use, unrelated-customer beta and paid launch. A code merge
or policy acknowledgement supplies none of those release approvals.

## Effective policy versus editorial candidates

`docs/TERMS_OF_USE.md` and `docs/PRIVACY_POLICY.md` remain the effective internal
documents. The contact is heathcoteadvisoryllc@gmail.com, selected by the owner.
Only the contact changed; their September 1 effective date remains the original
document date. No inference of approved external-customer terms follows.
`BETA_TERMS.md` and `BETA_PRIVACY.md` are **unapproved editorial candidates**.
Do not link their repository copies as effective public policies or enable their
publication before appropriate legal/privacy counsel and owner approval.
The candidate deliberately identifies unresolved jurisdiction/liability wording;
resolve it before approval rather than publishing that editorial checkpoint.

Public `/about` describes the app and links its policies. Public `/terms` and `/privacy` render the effective bundle via
`GET /api/v1/public/policies`, without a named session or business read. `/help`
is available signed out and links the policies; the signed-out application
describes sign-in and invitation-only access. Pages use fixed source files, no
user-selected paths or executable HTML. Restricted technical-alpha deployments
retain their existing Basic gate and cannot serve as the public OAuth website.

## Processing inventory

This is an implementation inventory, not provider-contract or hosted-region
certification. Record exact deployments, regions, agreements, access and limits
in restricted operator custody before publication. Never copy credentials,
customer payloads or private provider locators into this guide or GitHub.

| Owner/provider | Data and actual purpose | Access, location and retention boundary |
| --- | --- | --- |
| Render / FastAPI / React | Request processing, in-memory drafts and exports, static application assets | Host/origin guard and named authorization; no HTTP body logging. Exact region/proxy/platform logging and residency need deployed verification. |
| Neon PostgreSQL | Authoritative ledger, separate businesses, identity/membership/session control plane, audit/jobs, lifecycle and acknowledgement records | Server membership checks; managed history is not independent receipt backup. Exact region, vendor security copies and physical purge limits remain provider evidence. |
| ZITADEL | Hosted registration/sign-in, verified identity, authentication factors and provider events | Local login closure does not delete provider identity. Provider retrieval limits do not prove destruction. Confirm hosting, jurisdiction and contract before release. |
| Google Drive / OAuth | Customer-owned receipt bytes, dedicated business folders, connected email and app-file grant | `drive.file`, selected-business custody/recovery binding; no unrestricted Drive access. Disconnect/unlink preserve files. Credential custody is encrypted/operator-controlled and never sent to feedback. Confirm actual regions and customer sharing/grant permissions. |
| Google Maps | Optional transient autocomplete and Routes requests | No persistent Place IDs/polylines or response cache; user-reviewed saved mileage/location text is ledger data. Shared app-wide quota credentials are separate from human Drive grants. |
| Synology NAS / recovery custody | Independent encrypted database archives, receipt-byte copies, restore evidence and operational output | Existing generations retained indefinitely. No new expiry or destruction executor. DB protection alone is not receipt recovery; actual cadence and full-service recovery remain release evidence. |
| Better Stack / host diagnostics | Allowlisted codes, pseudonymous scope references, request/release IDs and security/reliability outcomes | Existing technical-stage retrieval windows do not certify all provider-held copies or beta suitability. No feedback answers or financial payloads. Confirm actual access and retention privately. |
| GitHub / build registries | Source, CI logs/artifacts, images, sanitized defect discussions | No customer financial content, screenshots, receipt attachments or case identifiers. Source/build retention is distinct from customer-record custody. |
| Support email / restricted case custody | Sender address, voluntarily sent support text, optional research choices, legal correspondence | Owner is primary reviewer; verify mailbox delivery, restricted readers, case-folder sharing and response coverage before admission. No automatic purge; apply holds, legal determination and minimized retention review. |
| Browser | Essential session/login/CSRF cookies; scoped onboarding request recovery and cross-tab coordination | HttpOnly cookies, Secure on hosted HTTPS, SameSite=Lax. No session credentials in localStorage. No analytics/advertising SDK or automatic product measurement added. Provider cookies require their own review. |

Heathcote operates the service; customers determine their business-record use and
memberships. Counsel must determine applicable controller/processor/service-provider
roles, legal bases and contracts by purpose/jurisdiction; this technical inventory
does not establish those classifications. Review U.S./Canada requirements,
individual rights, international transfers, mandatory notices, sensitive-data
limits and business versus individual authority before admission. Meaningful
optional consent must explain information, recipients, purpose and consequences;
see [Canadian regulator guidance](https://www.priv.gc.ca/en/privacy-topics/collecting-personal-information/consent/gl_omc_201805/).

## Version and acceptance mechanism

The candidate version is `beta-2026-11-16`. Its digest covers exact Terms,
Privacy, effective date, contact and version. Any byte change yields a different
digest and requires new review; preserve each approved exact bundle in restricted
release evidence/version control. Never silently substitute text under an old
approval. Record approval identity/time, counsel review reference, rendered bytes,
target domain/release and publication decision privately.

1. Resolve wording, provider evidence and retention/rights blockers. Choose the
   owner-controlled public domain and exact final contact. Verify the mailbox.
2. Set `TABULA_RASA_SUPPORT_EMAIL` and ISO `TABULA_RASA_LEGAL_EFFECTIVE_DATE` for
   the candidate. Compute `beta_candidate(contact, effective_date).digest` from
   `tabula_rasa.application.customer_policies`; review the exact rendered text.
3. Only after explicit owner/counsel approval, configure
   `TABULA_RASA_LEGAL_APPROVED_SHA256` to that digest through approved deployment.
   Absent approval configuration serves the internal policies. A configured
   mismatched digest fails startup, rather than publishing changed text.
4. Apply migration `0025_customer_policies` through the maintained schema rollout.
   Grant the named runtime SELECT/INSERT on `customer_policy_acknowledgements`,
   without UPDATE/DELETE/TRUNCATE or ownership. No existing users are backfilled.
5. Verify anonymous pages, HTTPS, version/date and exact content in deployed
   synthetic staging. Exercise explicit acknowledgement by two distinct people,
   CSRF denial, retry after uncertain response, policy update and stale digest.
   Verify unchanged export/closure and membership isolation. Retain sanitized
   references in the owning release discussion; local tests are not hosted proof.

Acknowledgement uses authenticated actor ID, server timestamp, version and digest;
no email, IP, user agent, business ID or financial payload is collected. It is
immutable/idempotent per person and bundle, including across session restart.
It is personal Terms acceptance/Privacy receipt, **not blanket processing consent**,
optional research consent or consent on behalf of every business member.
New business creation and invitation acceptance require current acknowledgement
when beta policies are active. Existing customers see updates without losing
bookkeeping/export/closure rights; invitation decline remains available.
Unknown/stale outcomes require reload, not automated repeated acceptance.

## Branding and final review checkpoint

Choose and verify the public domain before providing homepage, same-domain privacy
and terms URLs. The homepage must describe the application, link policies and be
accessible without signing in. The existing Google project, scopes, origins,
redirects and secrets stay unchanged by this implementation. The Cloud owner must
approve exact identity/support/URL values, verify ownership using an associated
account, complete applicable brand verification and publish only the reviewed
branding. `drive.file` data-access exemption does not establish brand completion.
Recheck [Google's primary requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)
at execution. Keep actual console/readback evidence or the precise applicable
exemption/audience in release custody. No submission or settings change is implied.

Open blockers before external admission: final public domain/identity, qualified
legal review, exact provider/geography/retention evidence, verified contact and
case custody, reviewed legal wording, deployed synthetic page/acceptance tests,
Google brand publication and the release owner's written beta decision. Recovery
objectives remain targets, not verified customer guarantees. The launch gate
consumes these results; do not close legal readiness from local implementation alone.

## Paid-launch delta

Before charging, approve the billable unit and extra-business hypothesis with
actual beta evidence; choose provider, price/currency/taxes, seats/entitlements,
renewal/trial/grace, cancellation/reactivation, refunds and disputes. Reconcile
payment subprocessors, privacy/retention/deletion, support promises and measured
recovery/service levels. Counsel and owner review the exact revised agreement
and existing-user notice/acknowledgement transition before paid release. Beta
acknowledgement does not agree to prices, marketing or later charging.
