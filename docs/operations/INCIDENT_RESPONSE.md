# Incident response and compatible recovery

Runbook version 1. Use this entry point for incidents; the linked guides own command
semantics. Technical alpha, trusted owner operation (live owner cutover), external beta
(external release and customer lifecycle) and paid launch have separate acceptance
boundaries. commercial recovery agreement owns the human commercial recovery agreement,
alternate-operator acceptance and production activation. Neither this runbook nor a
successful synthetic drill supplies those approvals.

[Support operations](SUPPORT.md) provides scoped metadata/audit triage
and approved access containment; provisioning or wider recovery still needs the
separately authorized administrator and human incident handoff.

## Ownership, severity and alert intake

The primary incident commander is the authorized deployment operator named in
the business's private handoff for that environment. An alternate takes command
after explicitly accepting handoff. Before enabling a stage or transferring
custody, verify the named operators, notification destinations, availability
window and independent access to the relevant providers and secret store. The
[custody procedure](SECRETS.md#provider-account-custody-and-transfer) covers
provider access. No staffed 24-hour on-call or response SLA is claimed. If the
primary does not acknowledge within the stage's agreed window,
escalate to the alternate through the approved private channel. If no authorized
operator is available, keep the affected service restricted and record that gap.

| Severity | Trigger | Initial decision / responsible operator |
| --- | --- | --- |
| SEV1 | Suspected cross-business disclosure, stolen session/provider/admin credential, incorrect or lost committed bookkeeping, widespread write-integrity failure | Commander declares immediately; restrict affected ingress and writers/jobs, involve security/recovery owner, preserve private evidence. Unknown blast radius warrants environment containment. |
| SEV2 | Service/readiness outage, failed deployment, provider/login/document outage, terminal jobs, missing backup or lost receipt bytes without confirmed disclosure | Release, database, identity, document or NAS owner diagnoses with commander; escalate to SEV1 if integrity/privacy is uncertain. |
| SEV3 | Isolated degraded operation with a safe supported workaround and no suspected integrity/privacy impact | Operator records scope, owner and next review; escalate if impact expands or the workaround fails. |

Severity is impact, not an automatic timer or permission to reset data. The
commander can hold several specialist roles; the alternate must have independent
access and be able to execute the procedure without the original implementer.

Intake comes from [operational observability
alerts](OBSERVABILITY.md#metrics-alerts-and-first-triage), Render/GitHub failed
releases, the NAS missing/failed-run path, durable job diagnostics, or an authorized
user's safe request ID. Acknowledge privately; record incident ID, stage, UTC
detection/declaration times, signal, release and impact class. Missing monitor data is
not a healthy service. Collector failure uses Render console plus independent uptime
monitoring; NAS notifications use only exit state, never forward the protected status
JSON/configuration.

## First response decision tree

1. Confirm the exact environment, approved business scope, access mode, current
   release and endpoint fingerprint against the private inventory. Stop if the
   target is ambiguous. Never infer production from a branch name or local file.
2. Suspected disclosure/credential compromise? Restrict ingress immediately,
   stop affected background access and follow [security containment](#security-and-identity-incidents)
   plus [secret rotation](SECRETS.md). Do not wait for a deploy rollback
   or provider password reset to invalidate app sessions.
3. Suspected incorrect writes/data loss? Pause application writers, receipt
   operations and the relevant external timers; drain executing work. Keep one
   authoritative backend. Inspect confirmed/uncertain operations before retrying.
   Retain source, backup/history and receipt bytes; choose forward repair or
   isolated recovery below. There is no automatic broad reset or Sheets fallback.
4. Deployment/readiness failure without integrity loss? Compare live/ready/version,
   Render state, image/schema and safe failure codes. Use compatible application
   rollback when proven safe; schema/configuration failures use maintenance and
   forward repair. An uncertain deploy must be inspected before any new dispatch.
5. Provider/job/document failure only? Contain that dependency or workload using
   the case table below. Preserve healthy businesses and supported ledger reads
   when their safety is established; never borrow another business's grant.
6. Record the containment action/time, owner, next decision and residual risk.
   Keep ingress restricted until the applicable recovery checks pass. A command
   exit, Live status or lack of alerts alone cannot establish full recovery.

Restrict ingress/stop timers through the exact provider resources in the private
inventory; verify the restriction from an independent client. Pausing a job
definition does not stop an executing transaction or synchronous HTTP writers.
If selective containment cannot be demonstrated, restrict the environment.
Provider writes, credential changes and recovery targets require the operator's
explicit scope authorization. Emergency authority and contacts must be arranged
before launch; documentation grants no new provider privileges.

## Safe diagnostic commands

Run from a clean checkout in the declared Python development environment, using
the incident release's tooling where schema compatibility requires it. Inject
secrets privately into the exact process; disable shell transcription/debugging.
Never print environment variables, raw HTTP headers, SQL/exception text or ledger
responses. [Telemetry](OBSERVABILITY.md#contract-and-privacy) owns the allowlist.

```powershell
# PowerShell 7; $origin is the approved exact HTTPS origin.

# Do not render failed response bodies or transport exception details.

foreach ($probePath in @('/health/live', '/health/ready')) {
    try {
        (Invoke-WebRequest -Uri "$origin$probePath" -TimeoutSec 30 -SkipHttpErrorCheck).StatusCode
    } catch { 'Health probe transport failed' }
}
try {
    $versionProbe = Invoke-WebRequest -Uri "$origin/version" -TimeoutSec 30 -SkipHttpErrorCheck
    if ($versionProbe.StatusCode -eq 200) {
        ($versionProbe.Content | ConvertFrom-Json -ErrorAction Stop).release
    } else { 'Version probe unavailable' }
} catch { 'Version probe failed' }

# Metadata only. Inspect specific steps privately; never publish complete logs.

gh run list --repo heathcote-advisory/Project_Tabula_Rasa --workflow commercial-staging.yml --limit 5 --json databaseId,status,conclusion,url
gh run view $promotionRun --repo heathcote-advisory/Project_Tabula_Rasa --json jobs

# Explicit recovery URL only; no .env.local/live/test fallback.

python scripts/verify_postgres_recovery.py inspect

# Explicit TABULA_RASA_DATABASE_URL, environment and entity injected privately.

# PowerShell child processes inherit $env: variables, not ordinary $variables.

# Use the approved secret injector; do not type credential values into history.

python -m tabula_rasa.infrastructure.postgres.job_cli inspect --entity $entity --service-id incident-operator
python -m tabula_rasa.infrastructure.postgres.job_cli attempts --entity $entity --service-id incident-operator --id $jobId
```

Retain only the resulting status/transport classification and release, never
the stored response body. `/health/live` proves process liveness; `/health/ready`
checks connectivity and the release's schema head; `/version` identifies code.
None proves accounting integrity, named login or receipt-byte availability.
Job identifiers and entity scope remain in authorized operator views; public
evidence uses opaque incident references. The job CLI can load `.env.local` if
its process URL is absent: an explicitly injected URL is mandatory here.

## Deployment and startup cases

| Observed case | Required action and success evidence |
| --- | --- |
| Healthy deploy | Existing ordered image/CI verification, bound schema check, Render digest comparison, readiness and mode-specific smoke all pass. Record exact release/index/runtime digest and run; named smoke alone proves no logged-in workflow. |
| Candidate never becomes Live / terminal deploy failure | Inspect the provider's exact deploy and retained live release. Confirm its readiness/version; do not report candidate success. Use the canonical failure procedure, not a second blind deploy. |
| POST/poll/timeout uncertain | Inspect exact Render service/deploy history and immutable image fields once authoritative state is available. Stop while uncertain; do not retry POST or race it with rollback. |
| Live candidate, failed smoke | Restricted alpha may restore a verified compatible prior image through existing automation; the run still fails. Named mode stops for explicit reviewed rollback, preserving named guards and configuration epoch. |
| Startup/readiness failure | Inspect safe startup event, explicit mode/origin, secret completeness, TLS/endpoint binding, pool capacity and DB revision with separate role custody. Correct the specific setting or deploy a compatible fix; readiness/startup never migrates or seeds. |
| Schema mismatch | Stop ordinary promotion. Drain writers, preserve recovery point and review the immutable forward chain under PostgreSQL persistence. Head-only automation cannot bridge old/new revisions. Never stamp, downgrade, bootstrap retained data or restore merely to silence readiness. |

### Compatible immutable-image rollback

Use [deployment promotion/failure/rollback](DEPLOYMENT.md#promotion-failure-and-rollback)
and [named rollback](../identity/HOSTED_ROLLOUT.md#compatible-application-rollback-and-incidents).
Reserve the existing `commercial-deploy-staging` window; check no deployment is
active/queued and pause overlapping operator promotion. Retain the current image
and a pullable prior immutable digest, their release/schema labels and successful
main-push `Full test suite` evidence. Verify that the rollback digest can be
pulled from the private package bound to the target Render service using its
configured registry credential. If the retained digest is only available in an
earlier namespace, use the [registry ownership procedure](DEPLOYMENT.md#registry-ownership-changes)
before dispatching a rollback. Compare Render `image.ref`/`image.sha` with
the registry's index-to-linux/amd64-manifest relationship. A tag or matching
`/version` alone is insufficient.

Confirm the prior image supports the current schema, access mode, identity epoch
and configuration. Named mode must retain named-capable code and all authorization
guards; do not revert to alpha or old session-generation material. Schema-changing
releases require a reviewed compatibility bridge or maintenance/forward repair.
For an approved staging rollback, pause automatic staging promotion by disabling
`COMMERCIAL_AUTO_STAGING_ENABLED` and let active/queued promotions settle.
Verify the live revision, then dispatch the existing workflow on main.

```powershell
# $rollbackImage is the reviewed repository@sha256 digest, never a mutable tag.
# $liveRelease is the full commit SHA confirmed live after pausing promotion.

gh workflow run commercial-staging.yml --repo heathcote-advisory/Project_Tabula_Rasa --ref main -f "image=$rollbackImage" -f "rollback_from=$liveRelease"
# Use the dispatch URL when supplied, or list candidate runs if it is absent.

gh run list --repo heathcote-advisory/Project_Tabula_Rasa --workflow commercial-staging.yml --branch main --event workflow_dispatch --limit 5 --json databaseId,createdAt,status,url
# Verify the dispatch time/actor and requested image in the run's Actions details.

# Select that exact run, not merely the newest run; stop if it is ambiguous.

$rollbackRun = Read-Host 'Verified rollback run ID'
gh run view $rollbackRun --repo heathcote-advisory/Project_Tabula_Rasa --json status,conclusion,jobs,url
```

Workflow source HEAD and the selected image release can differ; record both.
Only proceed after bound schema, registry/Render integrity and mode-specific
smoke succeed. Verify the applicable authorized business read/report/export,
session/isolation and receipt checks from the canonical alpha/named checklist.
If credentials for those checks are unavailable, mark that coverage pending.
Development rollback follows the deployment guide's manual retained-digest path;
the workflow deliberately rejects a superseded development image. Production
restoration requires its separately authorized operator procedure, never the
staging workflow. Rollback changes code, not bookkeeping, receipt bytes or schema.

## Identity-dependency rehearsal preconditions

The [bounded identity launcher](../identity/AUTHENTICATION.md#bounded-operator-identity-dependency-launcher)
is an operator-only alternate process command, not authority to deploy or execute
a hosted fault. Before use, obtain separate approval naming the exact immutable
image (including all other changes it contains), staging service, database and
identity bindings, startup override, absolute UTC window and maximum lifetime,
real synthetic login/MFA checks, independent telemetry and removal procedure.
Confirm the expected image is actually serving: a failed replacement deployment
may leave the healthy retained instance running and proves no provider outage.
No client, callback, account, secret, role or alert change is part of this command.

Before any identity fault, the provider-side operator must GET the actual saved
identity alert and truthfully disposition notification readiness. A historical
export showing query period 60 seconds, aggregation 300 seconds and threshold 3
is context only; it proves neither current settings nor delivery. Do not PATCH
the alert to fit the drill or treat a <=300-second fault as guaranteed to notify.
If saved alert/readback, routing, notification readiness or exact target approval
is unresolved, hold hosted execution. Local synthetic transport tests do not
satisfy these preconditions.

Keep a sanitized packet in the owning incident/PR discussion, with private
bindings stored separately:

- Approval reference, exact image/index/runtime digest, release/schema, stage,
  approved startup command and separately approved removal command/window.
- Current saved-alert GET time and safe settings, notification precondition
  disposition, independent observer and private routing custody reference.
- Process start and first login UTC times, unchanged identity-configuration
  attestation, initially empty discovery/JWKS caches and the actual failing
  discovery call. Record only its safe classification and request reference.
- Same-IP/port unrelated-host control and unaffected dependency checks, existing
  session policy observations, failed new-login event and no session issuance.
- Absolute UTC deadline, monotonic cap, first successful post-expiry resolution,
  real login/MFA recovery and independently observed telemetry/notifications,
  each marked observed, failed or unverified. Never manufacture missing signals.
- Override removal confirmation and residual limits: warm caches/open sockets,
  native-client bypass, shared-host overlap, no guaranteed notification and no
  claim that local evidence proves hosted acceptance.

Public packets must omit hostnames, credentials, callbacks, DSNs, identities,
business data and raw provider responses. Automatic expiry permits future
resolution; it does not remove the deployment override, repair a real outage or
complete the recovery checks. Do not repeat a hosted fault while notification
preconditions remain unresolved.

## Database recovery and safe service binding

Prefer a reviewed forward repair for code/schema faults where committed data is
sound. Suspected data loss/corruption requires the database owner to choose an
available snapshot/PITR/archive point, record expected loss and obtain the
applicable recovery authorization. Use [managed isolated restore/PITR](DATABASE_RECOVERY.md#milestone-a-restore-a-real-managed-backup)
or [NAS isolated archive restore](NAS_BACKUP.md#isolated-retained-archive-restore-drill).
Those guides own provider operations, protected artifacts and complete validation;
do not copy them into a second restore implementation.

1. Preserve the source and requested UTC point, known acknowledged commits,
   expected private exports and full inventory. Check actual history/expiration,
   archive integrity, capacity, TLS and exact isolated target lineage. No restore
   onto production/UAT and no deletion to make room is implied.
2. Restore into a new restricted target. Before upgrade or rebinding, use the
   capture's compatible release and existing verifier for every expected entity:

   ```powershell
   python scripts/verify_postgres_recovery.py verify --entity $entity --expected $expectedExport --source-database-sha256 $sourceFingerprint --target-database-sha256 $targetFingerprint
   # Requested-point PITR also requires the independent later committed state.
   python scripts/verify_postgres_recovery.py verify --entity $entity --expected $expectedExport --later $laterExport --source-database-sha256 $sourceFingerprint --target-database-sha256 $targetFingerprint
   ```

3. Separately validate roles/grants, control-plane identity/membership, authority,
   audit/baseline/watermark, jobs/attempts, recurring authority and period/correction
   state appropriate to that point. The 26-collection bookkeeping match cannot
   prove these, receipt bytes or the missing history tail. operational readiness verification owns remaining
   full recovery acceptance. Preserve evidence of the lost interval.
4. For service/config rebinding, drain all old HTTP/job/receipt writers and block
   ordinary access to the recovery target. Verify its actual lineage and opaque
   endpoint identity using the existing inspect command. Hashes exclude credentials
   and do not prove different provider branches. Use separate restricted runtime
   DML credentials, migration credentials and business-specific document grants.
5. Read [authority and return-to-service
constraints](DATABASE_RECOVERY.md#authority-document-recovery-and-return-to-service).
   A new endpoint does **not** inherit authority merely by changing the runtime
   URL/fingerprint. There is currently no generic active-authority rebind command.
   Stop with a platform operations and operational readiness verification engineering follow-up for an exact reviewed rebind plan;
   do not edit authority rows, clear divergence, rerun migration/activate or disable
   checks. A separately approved provider restoration preserving the original
   endpoint still needs recovered authority verification; it is not authorized
   by an isolated drill. Record database-ready separately from service-ready.
6. Before reopening any restored named environment, invalidate restored sessions
   and pending logins using a **new** generation and the existing environment-epoch
   rotation procedure. Never revive backed-up session credentials. Recheck fresh
   membership, no alpha bypass, two-business isolation, canonical accounting/report/
   export and exact receipt bytes/permissions. Resume one writer/timer owner only
   after the commander and applicable operator accept the checks and loss bound.

Measure database RTO from incident declaration through full database validation,
including access/operator delay. Full-service RTO additionally includes secure binding,
auth, bytes and smoke. Record observed recovery point/loss independently from approved
objectives; proposals in commercial recovery agreement are not accepted commitments. NAS
scheduled captures are Sunday/Wednesday at 22:00 for production and 22:30 for UAT in
`America/New_York`, following DST; the runner's due slot remains 22:00 for both
environments. Use the selected generation's manifest capture time to measure the actual
recovery point, and follow the [NAS schedule and catch-up
phases](NAS_BACKUP.md#nas-command-and-schedule). Successful-generation retention remains
indefinite; no automatic prune or plan purchase is implied.

## Provider, worker and document cases

| Incident | Containment / recovery / verification |
| --- | --- |
| Neon connectivity/capacity | Inspect safe dependency code, actual endpoint/TLS, provider status and connection limits. Compare runtime pool and all timers/migrators before any capacity change. Stop uncertain mutations; restore connectivity, inspect durable final state and use the existing bounded retry contract. Do not point the app at a test DB. |
| ZITADEL/provider outage | New login fails closed; existing valid local sessions may continue with fresh membership checks until expiry/revocation. No Basic fallback. Use provider/private configuration diagnostics, start a new login after recovery and verify named access; intentional session-store failure is a distinct incident. |
| Google OAuth/Drive outage or revoked grant | Preserve ledger metadata and bytes. Inspect the affected entity's connection lifecycle, exact grant/root and provider state; Owner reconnects through the existing connection flow. Ledger reads can remain available if safe. Disconnect locally before separately approved provider revocation; see secret guide for shared-consent impact. |
| Failed/dead worker job | Use `inspect` and `attempts`, classify safe outcome and inspect committed partial recurring effects. Stop the relevant timer/definition if needed; fix the cause, then use existing `retry` with the same job identity and bounded budget. Never delete/recreate runs or manually replay postings. |
| Recurring workload containment | Stop the external timer. If authority must be removed, use the existing `recurring-disable` with exact entity/service/time zone and `--allow-mutations`; verify `recurring-status=explicit`. It drains/fences occurrence writes. Definition pause alone cannot promise that. Do not disable period policy to bypass a locked-period failure. |
| Missing/failed NAS backup | Use the [NAS health and notifications](NAS_BACKUP.md#nas-command-and-schedule) exit state, capacity and due-slot/grace rules. Follow [failed/interrupted backup recovery](NAS_BACKUP.md#failed-or-interrupted-backup-recovery): confirm the old task stopped, preserve quarantined partials and prior generations, correct the cause, then perform one authorized manual run and verify healthy recovery. Missed backup monitoring is not replaced by API readiness. |
| Receipt-byte loss | Restrict affected document operations, preserve metadata, links, originals and unreferenced uploads. Use the same entity's approved Drive/NAS byte recovery; verify exact version/checksum, private permissions and two-business isolation. DB restore alone cannot restore bytes. document provider configuration and receipt byte recovery own provider binding/byte archives and their still-unverified gaps. Never delete newer files or relink another entity's receipt to simulate recovery. |

Mutation commands require explicit target authorization. After cause/target
review, the existing job recovery commands preserve identity:

```powershell
python -m tabula_rasa.infrastructure.postgres.job_cli pause --entity $entity --service-id incident-operator --id $definitionId --allow-mutations
python -m tabula_rasa.infrastructure.postgres.job_cli retry --entity $entity --service-id incident-operator --id $jobId --allow-mutations
python -m tabula_rasa.infrastructure.postgres.job_cli tick --entity $entity --service-id incident-operator --budget 1 --allow-mutations
```

`pause` selects a definition, `retry` selects a dead job, and `tick` may execute
any due eligible job for that entity, not only `$jobId`. Inspect the entire
eligible entity queue before authorizing a tick; otherwise keep the timer stopped.
Do not resume a definition until its cause and other queued work are safe.
Job pause/resume/cancel/retry and recurring fencing semantics remain owned by
[jobs operations](JOBS_AND_AUDIT.md#operator-entrypoints-and-manual-hosted-checkpoint).
An uncertain link/upload keeps its recoverable file; one fresh readback and at
most one demonstrably safe retry remain the application contract.

## Security and identity incidents

Named-user authentication and server authorization revocation and fresh authorization
are implemented; use [named
revocation/recovery](../identity/AUTHENTICATION.md#revocation-and-owner-recovery) and
[action policy](../identity/AUTHORIZATION.md), not a placeholder authentication system.
Immediate local revocation is required for a copied/stolen cookie, suspected account
takeover, provider password/MFA reset after compromise, compromised OIDC configuration,
or suspected cross-business disclosure. Revocation does not undo a disclosure or stop an
attacker obtaining new sessions from a compromised provider; contain access and repair
the provider as well.

| Scope | Existing interface and verification |
| --- | --- |
| User can safely authenticate | Same-origin CSRF-protected `POST /api/v1/auth/revoke-all` invalidates that user's issued sessions; reauthenticate only after provider recovery. Verify an already-issued credential denies and another authorized user remains isolated. |
| Operator-assisted account recovery | Authorized private operator uses trusted `IdentityService.revoke_user_sessions(user_id)` against the verified identity DB, plus approved disable/membership suspension if needed. privileged support operations's support surface must authorize this independently; no public admin endpoint or email-based subject lookup is invented here. Respect last-active-owner constraints. |
| Environment/config/provider compromise or restored DB | Restrict ingress, drain all workers, rotate affected secrets and **new** session generation with `rotate_identity_configuration.py --expected-epoch N`; restart only current workers and verify old sessions/logins deny everywhere. See [secret procedure](SECRETS.md#named-identity-and-restored-session-revocation). |
| Suspected cross-business leak | SEV1: stop affected access/background work, preserve minimized private evidence and revoke affected sessions. Verify the server's membership/capability/connection boundary with synthetic two-business reproduction before reopening. Unknown scope warrants environment revocation. Track business access policy and business-first navigation's approved independent-business model; old shared-workspace behavior is not a beta requirement. |

Local cookie clearing during a DB outage is not durable revocation; maintain containment
until the database confirms it. Never publish copied credentials as proof. Logs are
mutable diagnostics, not immutable audit or complete disclosure evidence.
Security/privacy owner assesses applicable external release and customer lifecycle and
privacy and notification policy notification/legal obligations privately; do not invent
a statutory deadline, notify customers from automation or publish sensitive evidence in
GitHub.

## Timeline, evidence and closure

Keep the full incident record in the approved restricted evidence store. Include
private environment/resource and business mappings, authorization, primary/alternate,
UTC occurrence/detection/declaration/containment times, decisions and command exit
states, requested/actual point and loss interval, DB-verified/service-verified
times, credential-revocation confirmation, residual gaps and next owner/action.
Record each recovery check as pass, fail, pending or not applicable with a reason.
Have the alternate follow the procedure and record usability/access gaps.

The public/redacted format is deliberately narrower:

```json
{
  "runbook_version": 1,
  "incident_ref": "synthetic-drill-340",
  "stage": "staging",
  "severity": "SEV2",
  "kind": "compatible_image_rollback",
  "status": "pending",
  "release_before": "reviewed-release-sha",
  "release_after": "reviewed-release-sha",
  "declared_at_utc": null,
  "contained_at_utc": null,
  "service_verified_at_utc": null,
  "observed_recovery_seconds": null,
  "workflow_urls": [],
  "checks": {"deployment_boundary": "pending", "named_fixture": "pending"},
  "private_evidence_ref": "opaque-approved-reference",
  "gaps": ["operator acceptance pending"]
}
```

Only reviewed release/digest/schema, UTC times/durations, fixed status/code,
safe request IDs and workflow links belong publicly. No raw logs, URLs with
queries, screenshots, HARs, headers, cookies, secrets, tenant/user/job IDs,
provider locators, bookkeeping/export contents or document bytes. An evidence
digest establishes integrity, never approval. Keep observed times distinct from
SLOs, database-only results distinct from full-service recovery, and successful
code rollback distinct from lost-data recovery.

Use the [controlled staging drill](INCIDENT_DRILLS.md) and its linked sanitized record.
Close the incident only after the commander accepts recovery and follow-up ownership.
platform operations and operational readiness verification own implementation/protection
gaps, document-byte recovery gaps, identity and tenancy and hosted identity rollout
identity integration, and external release and customer lifecycle and commercial
recovery agreement human recovery/alternate/release acceptance. Send the handoff for
human review; record accepted or improvements requested only after an actual response.
Source implementation or a Git commit does not claim that commercial recovery agreement
has accepted it.
