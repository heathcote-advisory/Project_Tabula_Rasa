# Business enrollment and beta admission

Admission is a small program control plane, independent of login, business RBAC,
bookkeeping authority and commercial billing. React/FastAPI/PostgreSQL remain the
active product. A named identity can own several independently permissioned
businesses; a private organization continues to contain exactly one business.
[Business lifecycle](../identity/WORKSPACES.md) owns creation and invitations;
[authorization](../identity/AUTHORIZATION.md) owns actual access and export.

## Policy and persistence

Forward migration `0019_beta_admission` creates a single database-scoped program,
request/admission records, per-business named-user approvals, expiring operator
grants and append-only `beta_events`. Environments must retain separate databases
as required by deployment policy. Admission tables are excluded from canonical
financial exports; full operational database recovery must preserve them.
No migration activates a beta release or sends an invitation. The initial default
has both invitation and business-creation freezes enabled, a three-business cap
and **no numeric initial user allowance**. Every collaborator still needs an
individual named-user approval for the exact business.

Existing active owners at the upgrade are retained in `beta_internal_users` as
the internal owner baseline, without changing their memberships or claiming any
ledger. Review this inventory privately before migration: the existing deployment
must still be the separately authorized internal service. These explicitly
internal owners retain internal creation/invitation operations outside beta
counts; an external user cannot obtain the exemption through login or membership.
No tenant route provisions internal users or operators. Do not classify external
customers as internal to bypass enrollment. Existing ledger access, jobs,
connections, retained Sheets sources and export remain unchanged.

In the default `approval_required` mode, an external Create Business request records `pending` with its stable
request ID, authenticated applicant, exact name and country. The submitted initial
bookkeeping unit must match the creation-country rule; admission does not store
a separate currency. It creates
no ledger or membership. The applicant can see only their own requests through
`GET /api/v1/beta/status` and the existing creation receipt endpoint. A changed
payload cannot reuse an approval. Operator approval reserves one business and its
named owner for 48 hours. Explicit retry of that same request activates the
reservation atomically with the existing ledger/defaults/bootstrap transaction.
Duplicate/concurrent retries return the same existing receipt. Expired approvals
must be explicitly reapproved; they never reactivate automatically.

Approved, unexpired business reservations and **all ever-created beta businesses**
consume the aggregate business cap. Suspended or closed active businesses keep
their allocation: this is the total program cohort, not concurrent subscriptions.
Denied/withdrawn/expired *unused* reservations release capacity while keeping
history. Customer closure/deletion remains a separate lifecycle responsibility.

Named approvals bind persisted user IDs, never an email-only identity or sibling
business membership. The recipient must first sign in so the operator can approve
the exact named account. Existing invitation email/fresh-sign-in checks remain;
the server additionally matches the exact approved identity. An invitation binds
and consumes one approval, with expiry no later than that approval's 48-hour
window. Parallel/duplicate invitation issuance cannot spend it twice. Decline or
revocation releases unused approval; expiry stops counting automatically without
a cleanup scheduler. A new offer requires explicit reapproval. Acceptance checks
current program/admission state and the membership version captured at issuance:
an old offer cannot undo a subsequent membership revocation or role change.
An accepted offer preserves an existing active role. Inactive membership
reactivation also requires fresh program approval. Membership/incident restrictions
remain separately authorized through their existing owners.

Expansion is a distinct, recorded operator action: `expanded` may set **at most
10 businesses and 20 distinct named users**. The initial stage accepts at most
three businesses and requires `user_cap: null`. One person in several businesses
counts once; each business counts once. Active named users remain in the total
cohort after withdrawal. Unused approved/invited users count while their reservation
is valid; shared approval in another business continues to count after one offer
expires or is withdrawn. Changes below allocated totals fail atomically. There
is no date-triggered expansion or automatic 20-user initial entitlement.

Eligible request pairs are US/USD and CA/CAD. Forward migration
`0020_book_currency` supports independent CAD books. Approval binds the exact
country/name; creation validates the initial US/USD or CA/CAD pairing and
explicit retry creates that bookkeeping unit in the same
transaction as defaults, membership, admission activation and creation receipt.
Both countries share the same aggregate program caps, named-user approvals and
freezes. Existing internal USD operations remain unchanged; the internal-user
creation exemption remains US/USD only. An explicit Owner/Admin [business-settings command](../identity/WORKSPACES.md#business-currency-settings)
may later change the bookkeeping label without changing this admission record,
country or approval. Admission stores no separate currency; the business root
is the current currency owner. There is
no FX, numeric redenomination or cross-currency consolidation.
Actual Canadian enrollment still requires selected-release verification and
written cohort GO; implementation removes the code hold, not the release gate.

## Self-service creation

Forward revision `0025_enrollment_drive_setup` defaults every program to
`approval_required`. Its existing caps, freezes, operator approvals and internal
USD exemption continue to apply. Self-service is an explicit operator decision
available in the `expanded` stage. For the currently supported US/USD and CA/CAD
pairs, a verified authenticated identity can submit a fresh request and own the
resulting independent business. The same aggregate business and distinct-user
caps apply to manual and self-service admissions. Each self-service success
records an admission, owner allocation, and audit event tied to the policy
revision in the same transaction as workspace creation. Business creation
honors the creation freeze and program suspension. Collaborator invitations
remain separately approved and retain their independent freeze.

A previously submitted request ID retains its original state and approval
requirements. A pending request can be manually approved, an expired one can
be reapproved, and an approved request can be retried within its original
window. Self-service uses a fresh request ID for a deliberate new submission.
Completed IDs always replay their original creation receipt, subject to existing
access checks. Reverting mode or freezing creation preserves existing
memberships, ledger access, connections and exports.

## Operator access and commands

Use the existing named session, exact Origin, synchronizer CSRF token and recent
sign-in (five minutes). A regular Owner/Admin/Read-only role grants no program
control. A production administrator must separately provision a short-lived
`beta_operator_grants` row for the exact persisted operator user ID and session
environment, with expiry and a private authorization reference. Revoke by deleting
the grant or expiring it; the server revalidates it on every command. Provisioning
is not a tenant API capability and must use existing approved administrator custody
and change records. Never publish credentials, session cookies, private identities,
business names, approval documents or response inventories.

`POST /api/v1/beta/operator` accepts:

- `inspect`: returns current program/revision, business and distinct-user counts,
  admissions (including pending/expiry), per-business named reservations, active
  membership counts and the most recent 100 immutable events. The authorized
  operator can privately query `beta_events` by sequence for older history.
- `approve`: exact `admission_id`; reserve the pending/unused business and owner.
- `approve_user`: exact active `admission_id` and authenticated `user_id`; reserve
  a collaborator. Existing business roles still determine who can issue the offer
  and what membership can be granted.
- `deny` / `withdraw`: reject or release an unused business admission.
- `withdraw_user`: stop future enrollment for that named business/user approval;
  existing memberships are not revoked by program withdrawal.
- `suspend` / `resume`: freeze/resume new admission into an active cohort business.
  Resume requires a suspended business. `close` is terminal for new admissions;
  retained records and existing customer access/rights remain intact.
- `set_enrollment_mode`: set `enrollment_mode` to `approval_required` or
  `self_service`. This command preserves all caps and freezes. Switch back to
  `approval_required` before returning to the initial stage.
- `configure`: explicit stage, caps, independent `creation_paused` and
  `invitations_paused` flags and program `suspended` flag. Program suspension is
  an enrollment freeze, not global authentication or tenant revocation.

Named-user commands require an explicit existing `user_id`; withdrawal of an unknown
approval fails without recording a successful action. Every mutation requires
the current `revision`, a reason and an
`approval_reference`; stale revisions fail. Read the new snapshot before retrying.
Actor and timestamp come from the server session/database. State and immutable
event history commit together. Operators must verify a separate written exact
release/cohort authorization before opening admissions; an approval reference is
an audit pointer, not automatic proof of external-release acceptance.

For example, a **synthetic-only** initial configuration payload is:

```json
{
  "operation": "configure",
  "revision": 1,
  "reason": "Synthetic staging validation",
  "approval_reference": "synthetic-staging-only",
  "stage": "initial",
  "business_cap": 3,
  "user_cap": null,
  "creation_paused": false,
  "invitations_paused": false,
  "suspended": false
}
```

Use actual current revisions from inspection. Later expansion requires a separate
owner/operational decision and reference; never copy this example into production.
For a synthetic self-service rehearsal, configure the expanded stage with the
approved caps while creation is paused, call `set_enrollment_mode` with the
current revision and synthetic approval reference, then open creation using
`configure`. Keep invitations paused unless separately testing them. Inspect
the counts, mode-change event and self-service events. To pause, set
`creation_paused` or suspend the program. To revert, set `approval_required`
and review the resulting freezes before reopening.

Production self-service requires the exact written enrollment decision for the
release, geography, cohort and limits under the [external beta release gate](https://github.com/heathcote-advisory/Project_Tabula_Rasa/issues/348).
Public or paid availability also requires the applicable commercial approvals.
Deployment alone leaves the default mode in effect. Credentials are still delivered only through the existing
private invitation workflow.

## Synthetic rehearsal and release handoff

1. Use a disposable database/current schema, fake OIDC and synthetic named users.
   The real PostgreSQL admission tests and native workspace browser tests supply
   runnable fixtures. Create first/second independent requests under one identity
   plus a separate owner; inspect pending records, approve exactly three total,
   and verify a fourth reservation fails, including concurrent retries.
2. Approve specific shared collaborators per business, exercise independent roles,
   expiry, decline/revoke, reapproval and membership revocation. Inspect counts
   separately for businesses, distinct humans, memberships and pending offers.
   Test expansion only in synthetic staging with a synthetic recorded decision.
3. Freeze invitations and creation separately and together. Confirm existing
   selected-business sessions, authorized reads and full customer export remain
   available. Test per-business suspension/resumption without sibling grants.
   True security incidents use [incident response](INCIDENT_RESPONSE.md) and
   [support operations](SUPPORT.md), rather than treating a freeze as containment.
4. Record exact deployed app SHA/schema, private cohort IDs/country/currency,
   program revision/limits/reservations, private event custody and synthetic
   validation evidence. Do not put identity/financial/receipt data in public CI,
   GitHub or operator logs. Browser checks cover desktop/mobile and accessibility;
   these are not hosted-provider/recovery/legal acceptance.
5. Request the separate [external-beta go/no-go](https://github.com/Roklion/Project_Tabula_Rasa/issues/348)
   under the [launch epic](https://github.com/Roklion/Project_Tabula_Rasa/issues/224)
   and [platform initiative](https://github.com/Roklion/Project_Tabula_Rasa/issues/124).
   The exact release must independently accept identity/isolation, customer-owned
   Drive/receipt/restart evidence, independent DB/receipt-byte recovery, customer
   rights/closure/retention, reviewed Terms/Privacy and acceptance version, support
   contact/escalation and any applicable commercial-authority prerequisites.
   Ordinary real bookkeeping may be admitted only after that written decision;
   neither merged code nor this rehearsal authorizes real invitations.
6. Before any later 10-business/20-person expansion, review measured capacity,
   provider headroom/cost, support load, incidents and the exact proposed cohort.
   Obtain a new explicit owner/operational authorization; then record the operator
   configuration change. Billing and paid launch remain separately gated.

For rollback, first freeze new enrollment. Preserve current database, receipt
bytes, identities, sessions and audit/admission history. Do not delete ledgers,
release active slots, rebind sources, reset membership or downgrade identity schema.
Use only a verified compatible app/configuration rollback that retains admission
enforcement: a pre-control image permits unapproved creation and is not safe while
reachable by beta users. Recovery/containment follows existing procedures with
separate authorization. Resume only after independent resolution checks and a
renewed exact cohort GO. No deployment, purchase, live cohort, data mutation or
commercial authority activation is authorized by implementation completion.

## Customer notices

My Businesses and first-use setup show invitation-only/noncharging status and
private pending/approved/active/paused information. Offers explain expiry and
explicit retry/reapproval. Capacity denials reveal no other customer's identifiers.
Notices exclude tax filing, payroll, banking feeds, FX and unverified depreciation;
state the customer-owned Drive receipt prerequisite; and prohibit unnecessary
secrets, identity, full-card and medical data without claiming content screening.
Best-effort ordinary support targets a first response within two business days
and weekly triage, with no fix-time or 24/7 SLA. Independent DB/receipt RPO of
24 hours and full-service RTO of 48 hours are unverified targets, not guarantees.
Reviewed customer policies, acceptance mechanism, retention/closure and a real
contact channel must be supplied by their release owners; no mailbox or unreviewed
legal promise is invented by the UI.
