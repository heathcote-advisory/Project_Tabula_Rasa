# Operational observability

Safe structured diagnostics correlate request, release, stage and pseudonymous
scope without exposing credentials or business data. This guide covers telemetry
ownership, sinks, alerts and triage. Ordinary local development requires no
telemetry account. Enabling a hosted destination, jobs or commercial recovery
promises remains an explicit operator decision.

## Inventory and ownership

| Existing owner | Reused hook and current gap closed |
| --- | --- |
| `http/app.py`, HTTP foundation | Server request ID, templated routes, safe error envelopes, live/ready/version; now structured level, stage, outcome, operation, error grouping and trusted pseudonymous context. |
| `http/serve.py`, deployment | Disabled raw Uvicorn access logs; now enables the safe INFO console sink. Runtime startup failure has a release-correlated diagnostic signal. |
| Render image/release workflows, deployment | Immutable release ID, deployment failure status, ordered migration/smoke/rollback. Provider notifications and failed Actions runs remain the deploy alert path; startup/readiness do not migrate. |
| PostgreSQL/Neon | Existing connectivity/schema-head readiness and connection limits; pool timeout and PostgreSQL connection exhaustion now have a safe diagnostic classification. Inspect Neon capacity/compute charts and existing migration checks to distinguish outages from wrong schema. |
| Identity and authorization | Reuses login success/failure, logout/local clearing/failure, all-session revoke and identity/authorization denials. Adds cross-entity/capability/CSRF reason without a foreign selector. Provider failure on a callback is a failure even though the browser receives HTTP 303. |
| Document services/HTTP | Existing errors, upload/link/unlink/recovery and tenant isolation remain authoritative. Document errors identify the dependency without object keys, names or bytes. document provider configuration owns the new managed adapter. |
| Browser | Existing `RouteError`, `ErrorNotice`, `ApiError` use safe messages/request IDs. No browser SDK, replay, console harvesting, URL or draft capture is enabled. Client-only crashes retain the existing safe fallback; server diagnostics do not claim to observe them. |
| Jobs/backup/audit | The durable queue/durable attempt diagnostics remain authoritative. Its explicit CLI tick emits shared `job_run` terminal outcomes after durable confirmation, including recurring handlers; the `backup_run` seam remains separate. NAS backup and restore supplies the NAS runner, verified-generation health, production 3-hour/UAT 6-hour status tasks and independent missing-run notification checkpoint; central ingestion does not replace that path. See [NAS scheduling](NAS_BACKUP.md#nas-command-and-schedule) for phases and detection limits. atomic audit history owns immutable, atomic business mutation history separately. |

The shared owner is `tabula_rasa/telemetry.py`; adapters supply only metadata
from registered server actions and already validated context. There is no new
business workflow, transaction manager or retry mechanism.

## Contract and privacy

Each request emits one JSON object, `schema_version=1`, with UTC `dt`,
`event=http_request`, server-generated `request_id`, stage/release, registered
`operation`, route **template**, bounded method/status/duration, safe
`failure_code`, `outcome`, `level`, `provider`/`provider_status`, and optional
security event/reason/action. `error_group` groups operation plus safe code;
request IDs are searchable attributes, never metric/group labels. Exceptions
are tracked through these groups, counts, release filters and correlation;
raw stacks, locals and exception messages are intentionally excluded. This is
log-based error tracking, not a claim that a native exception SDK is installed.

Actor, organization, entity, job and incoming correlation references are HMAC
SHA-256 pseudonyms truncated to 24 hex characters with kind/environment domain
separation. Scope comes from server-resolved context, including the caller's
permitted scope on a rejected cross-entity request, never the requested foreign
ID. A missing key omits references; a different key/environment changes them.
Login success can include its authenticated actor; failures never infer an actor
from an email, cookie or submitted claim. Logout does not perform an additional
session read just to enrich telemetry during an outage.

After fresh membership and connection checks, document-adapter failures (including
unreadable encrypted grants and provider failures) use the existing document-error
boundary: HTTP 503, `provider=documents` and `failure_code=unavailable`. They do not
emit an identity-denial event. Earlier authorization/connection lookup failures keep
their existing identity handling; provider credential revocation still records the
connection's reconnect-required state.

Named login adds fixed `security_reason` rejection categories and optional `login_ref`
to correlate start/callback events. This is an HMAC pseudonym of a separate
noncredential diagnostic reference, never a hash of state, session or login credentials.
Missing scope key/reference omits it. Treat it as a diagnostic hint, never
authentication evidence or a metric dimension. The callback's own server request ID is
safe to show on the signed-out screen for private operator triage. [Named-user failure
recovery](../identity/AUTHENTICATION.md#retained-provider-sessions-and-safe-failure-recovery)
owns the classifications, privacy limits and outstanding retained/clean hosted
verification under retained-provider login recovery and hosted identity rollout.

Never include emails, passwords, tokens, cookies, request/response bodies, raw
URLs/queries, filenames, workbook IDs, complete document keys, record IDs,
ledger values or receipt bytes. Caller correlation headers are pseudonymized,
not trusted as safe merely because they match a regular expression. Unknown
operations, routes, codes and security labels are dropped or mapped to a fixed
fallback. Metric dimensions are only stage, operation, status class, safe code,
provider and outcome. Keep release as a diagnostic filter; never use pseudonyms,
request/job IDs, error messages or raw paths as time-series dimensions.

Diagnostic security logs are mutable/expiring operational evidence owned by the service
operator. They are **not immutable accounting audit history**. atomic audit history owns
same-transaction bookkeeping events, their accounting access controls and separate
retention policy. Telemetry access never grants ledger access.

## Collection, tier and manual setup checkpoint

The default sink is one JSON line to the process console. Render captures it;
local developers can inspect it directly. Do not enable raw HTTP/access logs,
SQL echo, debug tracebacks or unrestricted workspace log forwarding.

The selected optional collection path is a dedicated Better Stack HTTP log
source per environment. It receives exactly the sanitized event sent to the
console. No SDK instrumentation, additional dependency, backend application
framework or paid upgrade is required by this implementation.

Provider evaluation checked October 16, 2026:
[Render log retention](https://render.com/docs/logging) documents 7 days for
Hobby; [Neon monitoring](https://neon.com/docs/introduction/monitoring) supplies
provider-side capacity inspection. [Better Stack pricing](https://betterstack.com/pricing)
lists a personal-project free allowance of 3 GB logs/month with 3-day retention
and 10 monitors/heartbeats with email/Slack alerts. Its applicability to the
operator/account must be confirmed before commercial use. These short windows
are technical-stage limits, not commercial retention commitments. Monitor
volume/headroom and set a spending limit; do not authorize charges or buy a plan
implicitly. Retention, private project access and eligible features must be
read back from the actual account before hosted acceptance.

Operator checkpoint (not performed by source changes):

1. Approve the account/project, region, personal/commercial eligibility, current
   tier, private access and retention. Name a primary operator and alternate.
   Use separate development/staging/production sources and source tokens.
2. Create an HTTP source. Copy its source-specific HTTPS ingestion origin
   ending in `.betterstackdata.com` (no path, port or trailing slash). Place
   `TABULA_RASA_TELEMETRY_ENDPOINT` and `TABULA_RASA_TELEMETRY_TOKEN` together in
   the exact Render service's runtime secret configuration. The token must be
   a source token, not an account-management token.
3. Generate at least 32 random characters for
   `TABULA_RASA_TELEMETRY_SCOPE_KEY`, independently per environment. Store it as
   a runtime secret. Rotation breaks reference continuity intentionally. Do not
   put values in GitHub, images, browser `VITE_*`, command arguments or artifacts.
4. Grant diagnostic read access only to the named operator/alternate. No public
   dashboard/shared log links. Limit account administration separately. Accept
   3-day collector/7-day console retention for this stage or explicitly approve
   a different eligible plan/window. Do not change NAS backup retention here.
5. Set the alerts below in the private dashboard; approve and verify the actual
   email/Slack destination and alternate. No recipient is supplied by code.
   Read back source routing, retention, alert criteria and escalation settings.
6. Deploy only through the existing approved release process. Verify release,
   successful ingestion, denied-request redaction, notification and recovery.
   Save only safe release/request/incident references as evidence. A configured
   token or green unit test alone is not hosted alert acceptance.

[HTTP ingestion](https://betterstack.com/docs/logs/ingesting-data/http/logs/)
accepts JSON batches and confirms them with HTTP 202. A 128-event memory queue,
32-event maximum batch, one worker and bounded network timeouts isolate delivery
from requests. Redirects, environment proxies, cookies and retries are disabled.
There is no local spool or business-data copy. Quota rejection, failed transport,
initialization failure or queue saturation produces a fixed local
`telemetry_delivery` error, at most once per minute. No exporter exception,
response body or credential is logged. Shutdown waits at most four seconds and
may discard queued diagnostics; dropped events cannot be claimed delivered.
Use Render console plus an independent uptime monitor when the collector fails.
Delivery never changes the real HTTP/business result, retries a mutation, or
turns a failed operation into success. Both endpoint/token absent selects
console-only mode; partial/invalid configuration fails startup safely.

## Metrics, alerts and first triage

Use the private source's JSON fields for log-derived metrics. Save request count,
5xx count/rate, latency p95, dependency failures, denial count and readiness
status. For example, in the source SQL editor (select the environment's source):

```sql
SELECT
  JSONExtract(raw, 'operation', 'Nullable(String)') AS operation,
  count() AS requests,
  countIf(JSONExtract(raw, 'status', 'Nullable(Int64)') >= 500) AS failures,
  quantile(0.95)(JSONExtract(raw, 'duration_ms', 'Nullable(Float64)')) AS p95_ms
FROM {{source}}
WHERE {{time}} AND JSONExtract(raw, 'event', 'Nullable(String)') = 'http_request'
GROUP BY operation
```

Use [dashboard alerts](https://betterstack.com/docs/logs/dashboards/alerts/)
with explicit missing-data handling and one incident per stage/signal, not per
request or entity. The following are initial technical-stage settings to
validate and tune, not claimed SLOs:

| Signal / condition | Initial alert and recovery | First action / owner |
| --- | --- | --- |
| HTTP 5xx (`event=http_request`, `status>=500`, excluding probes) | At least 5 in 5 minutes, or >=5% of at least 20 requests; healthy for 5 minutes resolves | Operator: group by safe error code/operation, compare release, find request ID in console. Never retry an uncertain save blindly. |
| External `/health/ready` !=200/unreachable | Check every 60s, incident after 3 failures, recover after 2 successes | Operator: inspect Render deploy/console, Neon connectivity/capacity/schema revision. Missing data is failure. |
| Deployment/migration failure | Existing Render deploy failure email + GitHub failed release workflow; immediate, manually disposition | Release operator: inspect ordered migration/image checks and compatible rollback in deployment guide; a previous live version is not candidate success. |
| `provider=postgresql`, `provider_status=exhausted` | >=1 in 5 minutes; recover after 5 minutes without another | Operator: compare pool cap and Neon connections, investigate leaks/traffic before increasing capacity. |
| `provider=identity`, `failure_code=unavailable` (including 303) | >=3 in 5 minutes, healthy for 5 minutes | Identity operator: check provider availability/configuration; preserve local logout/revocation semantics. |
| `provider=documents`, `outcome=failure`, `failure_code=unavailable` | >=3 in 5 minutes, healthy for 5 minutes | Document operator: inspect binding/provider state; preserve recoverable uploads and receipt bytes. |
| `telemetry_delivery` or ingestion absent during known probe traffic | >=1 notice, or no readiness log for 10 minutes | Operator: Render console, token/quota/source; independent uptime alert remains active if collection stops. Do not alert on ordinary idle tenant traffic. |
| Security denials | >=20 denials in 5 minutes per stage; 5-minute recovery | Operator: compare action/reason/release and pseudonymous caller context, without reconstructing foreign IDs. |
| Recurring/job failure (durable job queue and recurring job execution) | On terminal failure; explicit successful run resolves | The explicit CLI tick emits `job_run`/`job_execute` after confirmed success or terminal failure, with pseudonymous entity/job/worker references. Retryable attempts are not terminal failures. Inspect durable history for attempts and unconfirmed operations; this does not activate a worker. |
| Missing backup (NAS backup and restore) | `wrapper status` runs every 3 hours in production and every 6 hours in UAT; failed/missing/stale/capacity/overdue states exit nonzero. The existing 2-hour grace still controls stale/overdue classification; detection after a state becomes unhealthy can take up to the check interval plus execution/runtime latency. Verified healthy status resolves | NAS operator registers abnormal-exit notification using DSM's separate email sender plus an independent missing-NAS check-in or recorded operator check. Preserve Sunday/Wednesday 22:00 production and 22:30 UAT America/New_York, DST, and the [staggered catch-up/health phases](NAS_BACKUP.md#nas-command-and-schedule); both runner due slots remain 22:00. API startup never stands in for a backup. |

Connection exhaustion retains the PostgreSQL classification through named session
checks, login/logout and readiness. Client responses keep their ordinary safe
unavailable shape. A connectivity outage and identity-provider unavailability
are distinct from exhausted database slots.

Free Render cold starts/suspension and Neon idle compute can exceed probe thresholds.
Record those accepted limitations or choose approved continuous capacity; monitoring
itself can consume free compute hours. Do not mask an outage or claim paid availability
from a personal free allowance. For request alerts, no traffic is zero, not an incident.
For uptime/backup absence it is an incident. The [Incident
runbook](INCIDENT_RESPONSE.md) consumes these safe trails and the existing
[deployment](DEPLOYMENT.md), [NAS
health/notification](NAS_BACKUP.md#nas-command-and-schedule) and
[recovery](DATABASE_RECOVERY.md) procedures. NAS status output contains its protected
source/generation metadata: use only its exit state for notification, never forward its
whole JSON or backup config to the HTTP collector. Keep its stdlib-only image
independent of the web runtime. A future central adapter must use the shared allowlist
and a separate source, without changing backup results.

## Release workflow triage

During an approved release window the primary release operator watches the
selected Actions run and Render deployment until a terminal result; arrange an
alternate or record the uncovered interval before starting. Existing Render
failure notifications and GitHub Actions notifications remain the release
signal path. Better Stack readiness and application alerts supply service
context; they do not prove image-build, migration or queued-workflow success.

The image-build job has a 20-minute timeout and promotion a 30-minute timeout.
Those timeouts apply to executing jobs, not time spent queued or awaiting environment
approval. If a release has not started after the operator's agreed queue
deadline, inspect the environment concurrency holder, GitHub runner/approval
state and provider activity, and notify the named release owner. An older
healthy service can leave readiness green while a release is stalled. Do not
claim automatic stuck-workflow notification from an execution timeout or uptime
monitor alone; record any unverified delivery/coverage as an open handoff gap.

For a failure, inspect the first failed step and the exact run attempt, then
follow the [deployment stop/retry rules](DEPLOYMENT.md#promotion-failure-and-rollback).
Do not rerun an uncertain provider request while a deployment may still be
active. Record only safe release/digest/schema, run/deployment links, UTC signal
and acknowledgment times, recipient acknowledgment and final disposition.
Actual notification delivery needs recipient evidence, including recovery
where applicable; a configured destination or failed check alone is insufficient.

## Local and staging exercise

Local regression coverage uses synthetic services and a fake exporter:

```powershell
python -m pytest tests/http/test_observability.py tests/http/test_transport.py tests/http/test_authorization.py
```

On restricted synthetic staging only, privately export its ordinary HTTP/alpha
settings and smoke password as for the existing deployment smoke. Run:

```powershell
python -m tabula_rasa.deployment.observability_smoke --release EXPECTED_RELEASE
```

The command checks `/version`, triggers a controlled foreign-synthetic entity
403, then reads the permitted synthetic accounts and readiness (200). It prints
only release, statuses and generated request IDs. It never writes, dumps
responses or adds a public crash endpoint. Temporarily set a **staging-only**
alert to one `security_reason=cross_entity` event in a one-minute window,
confirmation 0, recovery after one clear window. Confirm the incident arrives
at the approved recipient, find its request/release, verify only the permitted
scope pseudonym, then observe recovery and restore the normal threshold. Keep
credentials out of evidence. Test 5xx grouping/readiness recovery locally;
a real provider outage/deploy/backup failure rehearsal belongs to its owner and
requires a separately controlled staging procedure, not breaking the live DB.

For named-user staging, reuse named-user authentication and identity security testing's
approved synthetic identity sessions and exercise a denied capability/cross-entity
request plus an allowed read; this alpha-only CLI deliberately refuses named mode.
Verify login success, provider-denied login, unavailable callback, logout and revoke
with their existing tests/runbook. privileged support operations must emit
`privileged_denied` for future backup/restore/admin capability failures, using only
registered safe actions, trusted references and request/release context. It must not
fabricate support authority or log a submitted target. Job and NAS integrations must
emit only confirmed run results; document provider configuration owns adapter-specific
failures; operational readiness verification consumes hosted evidence.

Hosted setup, notification delivery and staging smoke remain an operator checkpoint
until safe evidence is recorded. operational observability foundation tests do not close
platform operations, application transition, operational readiness verification or the
commercial release gate.
