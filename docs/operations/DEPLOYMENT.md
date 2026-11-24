# Release deployment and promotion

The same-origin React/FastAPI image deploys to Render with a scoped Neon database.
This guide owns environment bindings, ordered schema migration, immutable-image
promotion, smoke checks and compatible rollback. [Runtime](../browser/RUNTIME.md)
owns packages and topology; [named-user rollout](../identity/HOSTED_ROLLOUT.md)
owns hosted identity verification. Cutover, disaster recovery and commercial
activation require their separate approvals.

## Implemented repository path and evidence boundary

`deploy/commercial/Dockerfile` builds the browser with Node 24 and runs Python
3.12 as a non-root user. One Uvicorn worker serves browser assets, `/api/v1`,
`/health/live`, `/health/ready` and `/version`. `.dockerignore` allows only the
required sources/dependency manifests; settings, local credentials and ledger
artifacts cannot enter the build context. Server secrets are never build args.
The release SHA is visible at `/version`; OCI labels record SHA and Alembic head.
The browser uses relative API URLs and requires no per-environment build.

Named-user rolling promotions pass the verified prior release to the shared
public smoke check. After Render reports the candidate live, the version check
may still reach the prior instance during traffic handover. Only that exact
prior revision may be retried: at most 21 version probes, each with a five-second
request timeout, separated by three seconds. The expected candidate must be
observed before access-boundary checks can pass. An unrelated/malformed revision,
failed readiness or access check, or exhausted handover budget stops qualification.
Preflight checks and protected private-origin maintenance smoke remain strict.
After automatic full-downtime schema maintenance, the shared public smoke also
receives the verified prior revision. It checks version before readiness because
the old application can correctly return readiness 503 against the new schema.
Within the same 21-probe budget, it waits on a version response of 502, 503 or 504,
the known prior version, or readiness 502, 503 or 504 after observing the candidate. It requires
the exact candidate and readiness 200 before running access and sign-in checks.
Unknown versions, other unsuccessful probe statuses, transport failures, failed access
checks and budget exhaustion fail closed. That complete public smoke is the
final public verification for automatic maintenance; protected private-origin
maintenance additionally verifies public ingress after reopening it. Smoke
failures report safe phase diagnostics and HTTP status codes without response details, and failure
keeps the worker fenced and contains the application rather than retrying or
downgrading the committed schema.
This never retries a deployment or migration and does not replace the successful
build, staging job and retained release artifact required for production.

`commercial-image.yml` follows successful **push-to-main Repository CI** and
builds/publishes a `linux/amd64` GHCR image tagged by SHA. Its summary records the
immutable digest and expected schema head. Repository CI also builds/imports
the container without credentials. `commercial-promote.yml` serializes the
whole deployment per GitHub environment and queues pending promotions. Development
follows the validated image. When both deployment flags are enabled, staging
automatically receives the exact digest from the successful image build.
**Promote commercial staging** remains available from main for a controlled
retry or rehearsal with an explicit digest. Promotion runs tooling from the
workflow revision and checks the image's repository, OS/architecture, release
metadata, main ancestry and latest matching main-push CI success. Keep SHA tags
and associated digests needed for rollback; never prune active/rollback releases.

Staging checks the live Render revision before migrating and again before
deploying. A forward promotion must match or descend from that revision. A superseded
candidate fails with a diagnostic and operators should inspect the live release
before retrying. For an intentional rollback, follow the recovery procedure
below. After Render confirms the digest and smoke verifies readiness and version,
the Actions job summary records the staging commit, image digest, schema,
environment, Render deployment ID, deployment start and verification time.

Verify a retained UAT target's endpoint, forward schema and runtime and migration
roles before connecting staging. Preserve its existing records. Follow
[PostgreSQL connection configuration](../database/POSTGRESQL.md) for supported
connection options. Enable `COMMERCIAL_DEPLOY_ENABLED=true` after the operator
checkpoint below. Unattended production requires one-time owner activation.
Emergency manual production requires explicit release authorization. Entity
authority remains governed by [controlled cutover](../accounting/CUTOVER.md).
The [original development and staging rollout](https://github.com/heathcote-advisory/Project_Tabula_Rasa/issues/279)
records the historical UAT selection and promotion evidence.

## Release matrix and operator handoff

Use this matrix for each selected release. Record actual outcomes in the owning
release discussion, with UTC timestamps and sanitized run links. A successful
local test, configured notification or earlier release does not prove a new
hosted execution. Keep credentials and recovery contents in private custody.

| Stage | Required result and evidence | Stop condition / next action |
| --- | --- | --- |
| Pull request | Current-head **Full test suite** succeeds and review findings are dispositioned under [Development](../DEVELOPMENT.md#authoritative-ci-and-required-check) | Missing, pending, skipped or failed required checks prevent merge readiness. |
| Main CI and image publication | Successful main-push Repository CI; image build publishes an immutable GHCR digest with release SHA, schema and verified linux/amd64 runtime digest | Failed CI/build supplies no eligible release. Inspect the failed job; do not deploy a moving tag. |
| Automatic staging | Enabled staging job verifies the candidate and prior live image, checks supersession, checks the bound schema, applies any supported forward path, restarts and passes named-user smoke | Inspect the exact failed step and live provider state. A successful build or healthy prior image does not prove candidate success. |
| Staging handoff | Record release SHA, requested/runtime digests, schema, run ID/attempt, Render deployment, origin, verification time and successful staging artifact | Production requires evidence from successful immediate staging promotion, no older than seven days, with the same immutable image still live. A same-image redeployment must pass current version/readiness checks. Manual staging or rollback alone is insufficient. |
| Schema-changing promotion | Staging starts immediately. Enabled production starts at the next eligible 03:00 Eastern window. Maintenance applies a reviewed forward path after isolating and draining writers. | A missing safeguard blocks promotion. Once maintenance starts, inspect the failed phase and use the incident procedure. |
| Production activation | Owner approves unattended promotion once after hosted verification and configuration review. Routine releases use the exact staging-verified digest. | Keep unattended promotion disabled until authorized. Review any failed attempt before rearming. |
| Emergency manual production promotion | Dispatch on protected main with the exact staged digest, automatic staging run, expected live production image and recovery-record hash | Preflight must pass again with the candidate's verified code before migration/deploy. Changed staging or production state requires fresh review. |
| Production verification | Provider requested/runtime digest relationship, expected version, readiness and named-user smoke all pass; retain summary and timestamps | A live but unhealthy named candidate requires operator containment and the reviewed compatible rollback/forward-repair decision. No automatic schema downgrade. |

Before the release window, name a primary operator, alternate or explicitly
uncovered interval, alert recipient and recovery-evidence custodian. Have the
operator follow the release and [compatible rollback procedure](#promotion-failure-and-rollback)
from approved access; assistant execution alone does not demonstrate operator
readiness. Record acknowledgment, any unclear step and the final observed state.
Application promotion does not grant customer admission, broader recovery
acceptance or commercial activation.

## Hosted Maps configuration and credential rotation

For each environment, use the [Maps restriction matrix](../GOOGLE_MAPS.md#hosted-key-restrictions)
before binding a key. The Render operator owns the service, origin and outbound
CIDR inventory. The Google Cloud credential owner owns the restriction,
quota and budget changes. Agree on an alert recipient and rollback owner.
Keep staging and production bindings and live acceptance evidence separate.

1. Refresh the live release, browser origins and **Connect > Outbound** lists.
   Repeat this check before a service moves region or adopts different egress.
   Coordinate the new restrictions with the credential owner before routing
   traffic from the new service.
2. With separate authorization for Google key changes, create restricted
   replacement keys and verify their settings. Identify all existing callers
   and preserve the last working credentials in protected custody.
3. With separate authorization for Render secret changes, set
   `TABULA_RASA_MAPS_BROWSER_API_KEY` and `TABULA_RASA_MAPS_ROUTES_API_KEY` under
   the target web service's **Environment** or its approved secret source.
   In the service's [environment-variable save menu](https://render.com/docs/configure-environment-variables#setting-environment-variables),
   choose **Save only** to defer activation until the next deploy. The other
   save options trigger a deploy, so stop if **Save only** is unavailable and
   deployment is not yet authorized. Use the selected immutable image for the
   authorized deploy and verify the live deploy ID and `/version` before testing.
4. Run [controlled Maps validation](../GOOGLE_MAPS.md#controlled-validation)
   and record the outcome for that environment. If the new binding fails,
   diagnose the restriction or runtime configuration. With operator approval,
   restore the previous safe binding and deploy the compatible image. Verify
   the recovered binding after deploy. Manual mileage remains available if
   Maps must stay disabled.
5. Wait for provider metrics to confirm expected traffic on the replacement
   and that known callers have moved off the superseded key. Revoke it only
   with separate operator approval. For suspected compromise, notify the
   credential owner immediately, inspect key/API metrics and billing, and
   coordinate containment and restricted replacement. A compromised key
   cannot serve as the rollback credential.

## Manual operator checkpoint

Complete this private setup record before enabling deployments; do not paste
secret values into issues, commits, logs or chat. Provider/resource creation,
DNS, secrets and deployment execution require the operator's account access.
No subscription purchase is implied.

For owner-operated migrated profiles, the standard image additionally accepts
`TABULA_RASA_MIGRATED_BINDINGS_FILE`, pointing to private server-mounted binding
metadata. Follow [the owner runtime configuration](../accounting/CUTOVER.md#container-configuration-for-migrated-owner-profiles)
and the existing named-user/tenant-grant setup. Use the explicit migrated binding
path for owner-operated migrations; the ordinary native workspace runtime does not establish their
source/authority/document acceptance. This configuration changes neither the
separate staging service nor the commercial activation policy.

1. Select two independently configured Render image web services and their
   canonical HTTPS origins. Review `deploy/commercial/render.yaml` as an operator
   template, replace its bootstrap image tags with a tested immutable digest,
   and configure a Render private-GHCR credential with `read:packages` access.
   Validate the edited Blueprint through Render before syncing it. Disable
   Blueprint auto-sync; future syncs must preserve the selected release digest.
   Confirm geographically close Render/Neon regions and PostgreSQL 18 support.
2. Select a distinct Neon development endpoint and confirm the exact binding of
   the retained UAT endpoint approved above for staging.
   Preserve its deployed forward revision chain and existing synthetic UAT
   records. Production/recovery endpoints are excluded. Separate roles/secrets
   are required for each environment; shared Neon project administration/capacity
   remains a documented stage limitation. Use direct, unpooled URLs with
   `sslmode=require`. Follow [PostgreSQL operations](../database/POSTGRESQL.md) for runtime
   DML privileges versus a separately held migration role with schema ownership.
3. Record each endpoint fingerprint using the existing opaque identity contract
   (`host`, port and database; credentials do not participate). Set the matching
   `TABULA_RASA_DEPLOY_DATABASE_SHA256` in both Render and the GitHub environment.
   This binds the intended target and does not prove provider isolation. Do not
   infer a target from a filename, production branch name or legacy profile.
4. For a new **blank** endpoint only, run the image's explicit migration command
   with `--bootstrap`. It refuses a database containing public tables or an
   unknown retained revision. Current retained endpoints use `migrate` without
   this flag. Run `seed` once to add `alpha-synthetic` and its zero-balance
   `Deployment connectivity` account. The seed uses the existing entity/account
   services in one transaction and refuses an existing fixture; inspect rather
   than delete/reset an existing fixture. Nothing seeds/migrates on API startup.
5. Generate a separate nonempty operator password hash per environment with
   `python -m tabula_rasa.http.alpha` (hidden password prompt). In each Render
   service set the runtime URL, endpoint fingerprint, public origin, environment,
   fixed trusted entity, username and hash. Use a small pool (template: two
   connections, zero overflow) and account for both services, tests/migrators and
   provider connection limits. Never inject the migration-role URL into Render.
6. Create GitHub environments named `development` and `staging`, restrict their
   deployment branches to main, and use reviewer protection where available.
   Configure the following separately in each environment. The repository-wide
   `COMMERCIAL_DEPLOY_ENABLED` variable stays unset/false until both are ready.
   Keep the repository-wide `COMMERCIAL_AUTO_STAGING_ENABLED` variable unset/false
   until the operator has confirmed the staging Render, Neon UAT, and applicable
   Zitadel bindings, the main **Full test suite** merge requirement, and staging
   environment protections. Automatic promotion requires both variables set to
   `true`. If the staging environment requires approval, retain that gate and
   record the approval requirement when validating hosted automation.
7. Record the selected plan/capacity, cold-start/idle-suspension behavior, monthly
   limits and billing headroom. Free tiers are permitted for this technical
   stage when the real persistent topology fits those limitations; they do not
   promise continuous availability or commercial recovery. Use continuously
   available capacity when the intended usage requires it. Provider selection
   remains Render/Neon; no SQLite or in-memory staging substitution is supported.

| GitHub environment variable | Value |
| --- | --- |
| `RENDER_SERVICE_ID` | Exact service ID for this environment |
| `TABULA_RASA_PUBLIC_ORIGIN` | Exact canonical HTTPS origin, no trailing slash |
| `TABULA_RASA_ACCESS_MODE` | Explicit `restricted_alpha` or `named_user`, matching Render; missing/unknown stops verification before migration |
| `TABULA_RASA_DEPLOY_DATABASE_SHA256` | Approved target fingerprint |

| GitHub environment secret | Custody/use |
| --- | --- |
| `TABULA_RASA_MIGRATION_DATABASE_URL` | Migration container step only |
| `RENDER_API_KEY` | Render preflight and deployment steps only; authorized workspace access |
| `TABULA_RASA_ALPHA_USERNAME` | This environment's temporary operator gate |
| `TABULA_RASA_ALPHA_PASSWORD` | Smoke runner only; Render receives its scrypt hash |

The workflow's short-lived `GITHUB_TOKEN` pulls the private package and reads CI
evidence. Render keeps a separately configured registry read credential. Render
stores `TABULA_RASA_ALPHA_PASSWORD_HASH`, never a browser build constant.

Essential receipts additionally require a service-specific secret file at runtime,
selected through `TABULA_RASA_ALPHA_DOCUMENTS_FILE`. Follow
[Hosted synthetic receipt storage](../storage/STAGING_RECEIPTS.md) for the exact
TOML contract, independent staging grant/root and read-only preflight. The API
image includes the existing Drive client's `requests` dependency and runs in
group 1000 to read Render's runtime secret mount. Keep Drive grants out of
GitHub deployment/gate credentials and browser assets. This optional binding
does not alter database promotion or authority policy; configured invalid
bindings stop startup, while absent bindings leave receipts unavailable.

To compute the non-secret binding from an explicitly exported URL (without
loading `.env.local`), run from root:

```powershell
python -c "import os; from tabula_rasa.infrastructure.postgres.config import DatabaseSettings; from tabula_rasa.infrastructure.postgres.runtime import Database; from tabula_rasa.infrastructure.postgres.identity import database_identity; db=Database(DatabaseSettings.from_environment(dict(os.environ),required=True)); print(database_identity(db)); db.dispose()"
```

For initial operator setup, export the migration URL as
`TABULA_RASA_DATABASE_URL`, the approved fingerprint and the environment into
the process. Run the exact tested image (represented by `$releaseImage`):

```powershell
docker run --rm -e TABULA_RASA_DATABASE_URL -e TABULA_RASA_ENVIRONMENT -e TABULA_RASA_DEPLOY_DATABASE_SHA256 $releaseImage python -m tabula_rasa.deployment.database migrate --bootstrap
docker run --rm -e TABULA_RASA_DATABASE_URL -e TABULA_RASA_ENVIRONMENT -e TABULA_RASA_DEPLOY_DATABASE_SHA256 $releaseImage python -m tabula_rasa.deployment.database seed
```

Omit `--bootstrap` for retained current-schema targets. Remove the migration
credential from the operator shell after setup. Ordinary pytest requires its
separate disposable administrator URL; never point the test suite at retained
Neon staging/production. The one-time fixture is synthetic and deployment-wide,
not a migrated business or an authority switch.

## Named identity rollout

[ADR 0003](../adr/0003-commercial-identity-and-tenancy.md) selects ZITADEL hosted OIDC
plus durable opaque PostgreSQL sessions. [Commercial
identity](../identity/PROVIDER_SETUP.md) owns the approved
provider/callback/mail/recovery contract. Named login, membership enforcement, switching
and entity-owned connections are implemented. [Authenticated
rollout](../identity/HOSTED_ROLLOUT.md) owns the operator setup, retained-staging
rehearsal, named-image rollback and cross-handoff. [hosted identity
rollout](../identity/HOSTED_ROLLOUT.md) records current hosted provider results and
outstanding acceptance. The setup and Blueprint above describe restricted synthetic
alpha; they remain supported with explicit `TABULA_RASA_ACCESS_MODE=restricted_alpha` in
Render and GitHub. Named promotion uses `named_user` in both and injects no Basic
credentials. Follow the rollout checklist to remove synthetic settings and provision
separate named credentials and grants. Lasting owner use and external beta retain their
independent gates.

## Ingress, alpha access and session boundary

Render terminates HTTPS and owns external HTTP-to-HTTPS routing. Confirm that
boundary at setup, including custom DNS/TLS and canonical Host handling. The
container port is reachable through Render ingress; do not expose an additional
public bypass listener. Uvicorn has `proxy_headers=False`: X-Forwarded-For,
X-Forwarded-Host and X-Forwarded-Proto never grant authorization, change trusted
scope or relax Host/Origin checks. The application uses configured HTTPS origin
for policy, not the internal hop's scheme. Its minimal probes accept internal
health-check Hosts; private paths require the exact configured Host.

Restricted-alpha hosted development/staging refuses startup without explicit HTTPS,
`alpha-synthetic`, an operator username, a valid scrypt password hash, direct
TLS database URL and matching endpoint binding. HTTP Basic protects shell,
assets, API, documents and schema paths; only minimal health/version probes are
public. Hosted responses, including compiled assets, use `Cache-Control: no-store`
so shared caches cannot bypass the gate. Empty passwords are rejected by both
the hash generator and authentication boundary. Password verification runs
outside the async request loop with at most two gate workers per process;
public probes bypass those workers. Unsafe requests still require
exact Origin and the shared context's
CSRF token. The browser receives the fixed synthetic entity, never a scope
override. Different environment credentials and connections remain independent.
Secrets and private request/draft data never enter safe HTTP error/log output.

This temporary gate uses browser HTTP Basic authentication and a process-lived synthetic
CSRF context. It issues no commercial session cookie. identity and tenancy owns the
ADR's host-only HttpOnly/Secure/SameSite cookie, durable sessions, memberships and
revocation, now implemented by named-user authentication and existing entity claims and
local administration; production rejects alpha credentials/context entirely. Restrict
the temporary runtime to trusted synthetic use. Hosted mode must be explicit; missing
named configuration never falls back.

## Promotion, failure and rollback

[incident response](INCIDENT_RESPONSE.md) provides severity/ownership, containment, safe
diagnostics and cross-provider recovery decisions. Its [controlled staging
drill](INCIDENT_DRILLS.md) reuses this lifecycle and preserves the named-mode, schema
and uncertain-deployment boundaries below.

The ordered lifecycle is mode/image verification -> bound `current`/`upgrade`/`health`
in a separate migration container -> Render digest deployment -> readiness ->
real browser smoke. Restricted-alpha smoke checks gate restrictions, release identity,
deep-link/static/error routing, session scope and the rendered synthetic account
read from PostgreSQL. It creates no new records, retains no traces/screenshots,
and records only safe release metadata. Repeat a successful staging promotion
and retain both workflow-run/digest references as repeatability evidence.
Named smoke instead verifies the public sign-in shell and anonymous/forged-Basic
denial without provider login or private reads; readiness verifies DB connectivity.
It does not complete the named hosted rehearsal. Results identify `access_mode`.

Development and ordinary manual promotions require the deployed database to
already match the candidate image's schema. In development, preflight also
requires an active Render service. If Render suspended it, check the service
dashboard for the reason. For a Free usage suspension, wait for Render to
restore capacity or use the account owner's approved capacity plan. Once the
service is active, verify the existing image and schema before promoting the
latest validated main image. A schema mismatch requires a reviewed maintenance
or compatibility procedure before retrying.

Automatic staging and scheduled production can apply reviewed forward schema
changes under the [approved forward release procedure](#schedule-an-approved-forward-release).
Unknown or unsupported revisions stop before migration. PostgreSQL `upgrade`
owns forward migrations. Startup and readiness never run it. Bootstrap is
manual and initial only.

### Retired staging bridge: 0012 to 0014

The one-time retained-staging bridge completed its approved forward upgrade and
compatible-image promotion. The [protected maintenance run](https://github.com/Roklion/Project_Tabula_Rasa/actions/runs/39187928399)
and [staging handoff evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/321)
preserve the source/target revisions, immutable image and recovery-checkpoint
reference. The dedicated `commercial-staging-maintenance.yml` workflow and
`staging_maintenance` command are retired. Do not dispatch or reuse historical
revisions for another upgrade; each new transition requires a reviewed procedure.
Canonical Alembic revisions and the ordinary forward-upgrade CLI remain intact.

Current schema releases use the guarded release procedure above. Migration tests
independently preserve two-business ledger/isolation and disabled-accounting-policy
checks from `0012_operations` to the current head. Bridge-specific pinned-image
and workflow tests retire with their single-use entry points.

Restricted-alpha routes, synthetic contexts and their deployment/tests remain
supported independently. A named staging handoff does not retire those callers.
Streamlit bookkeeping and its Sheets writers are retired. Retained source migration
and recovery tools follow [controlled cutover](../accounting/CUTOVER.md) and the
[format-13 source contract](../SHEETS_SOURCE_CONTRACT.md).

### Retired support-schema bridge: 0014 to 0015

The schema-specific support maintenance workflow and command are retired. They
required an exact schema-0015 image and are not a supported route for current
releases. Preserve the canonical Alembic revisions, runtime-grant requirements
in [Support operations](SUPPORT.md), retained checkpoints and historical execution
evidence. Never rerun an old workflow revision to migrate a current environment.
Use the [approved forward-release procedure](#schedule-an-approved-forward-release)
with the current migration safety contracts, recovery checkpoint and compatible
image verification. Startup never performs migrations.

### Immutable image verification

Image verification uses authenticated Buildx registry descriptors to distinguish
the requested OCI index (or Docker manifest list) from its single `linux/amd64`
runtime manifest. It pulls and inspects that immutable platform manifest for
release/schema labels and OS/architecture; attestation and other-platform
digests do not qualify. A direct image manifest is also supported. Promotion
submits the original build-output or operator-supplied immutable digest to Render.
Both Render `image.ref` and `image.sha` must identify that digest or its verified
runtime manifest in the configured repository. A SHA is accepted only as
`sha256:` plus 64 lowercase hexadecimal characters or the same 64 characters
without the prefix. Missing, malformed or unrelated metadata stops before smoke
or automatic recovery; a Live status or matching `/version` alone is insufficient.

Before deployment, the tool finds the current live Render image and verifies
its immutable `image.ref`, its registry relationship to `image.sha`, pullability
and matching schema head. Rollback retains this verified reference instead of
constructing a pull reference from Render's SHA. A prior tag-only or unverifiable
deployment stops before a new deploy: inspect it and pin the verified retained
release by immutable digest through the operator procedure first.
A live restricted-alpha candidate whose smoke fails is replaced by that prior compatible digest
and checked with the same registry/Render integrity comparison before smoke.
The workflow still fails, preventing claims of promotion
success. An initial deploy with no prior image stops for operator inspection.
Render terminal deployment failure stops; inspect the retained previous live
deployment. An uncertain POST, poll failure or timeout stops for inspection
without retrying the deploy or racing it with rollback.
Named smoke failure stops for operator inspection rather than restoring an older
auth implementation automatically. Follow the [named-mode rollback checklist](../identity/HOSTED_ROLLOUT.md#compatible-application-rollback-and-incidents),
keeping named access and forward schema throughout.

### Index and runtime digest verification

An OCI image index and its selected platform runtime manifest can have distinct digests.
Validate the registry index-to-platform relationship and compare the provider's resolved
runtime image as well as its requested reference. Accept only that proven relationship;
unrelated and attestation digests must still fail. Keep the exact
index/runtime/release/schema evidence for each promotion. [deployment
evidence](https://github.com/Roklion/Project_Tabula_Rasa/issues/279) records the
original diagnosis and completed hosted promotion; old image IDs or a local readiness
observation cannot validate a new release.

For manual recovery of bad application code, pause automatic staging promotion
and let in-flight promotions settle. Inspect the exact service's live revision,
select its retained schema-compatible image and dispatch that digest through the
staging workflow with the full live SHA in `rollback_from`. The workflow checks
that staging is still on that SHA and that the selected target precedes it.
Follow the [incident rollback procedure](INCIDENT_RESPONSE.md) for dispatch and
verification. Development automation deliberately refuses superseded
main images; a manual development rollback instead selects the exact retained compatible
digest in the Render Dashboard, after pausing automatic development promotion. Verify
readiness/release and run `python -m tabula_rasa.deployment.smoke --release
EXPECTED_SHA` with this environment's explicit access mode and origin exported privately
(alpha smoke also needs its credentials). Named rollback additionally requires the
reviewed auth/epoch compatibility checklist above. Resume automatic development
promotion only after a validated corrective main release exists. Do not change/delete
bookkeeping records as rollback. If the schema is incompatible, stop, restrict
traffic/drain writers and use PostgreSQL persistence's reviewed forward repair or
[recovery guide](DATABASE_RECOVERY.md). Database restore/PITR and commercial recovery
acceptance stay with platform operations and commercial recovery agreement.

## Registry ownership changes

The canonical GitHub repository is `heathcote-advisory/Project_Tabula_Rasa`.
Render development, staging and production use the private package
`ghcr.io/heathcote-advisory/project_tabula_rasa/commercial-release`. Verify the
effective image binding and registry access for each service before promotion
or recovery. Development and staging can promote automatically. Production uses
the explicitly authorized manual or scheduled path. Follow [provider custody and transfer](SECRETS.md#provider-account-custody-and-transfer)
for account and credential ownership.

The image workflow and promotion checks use the same GHCR
repository derived from `GITHUB_REPOSITORY` with the `/commercial-release` suffix.
Verify that the destination package is private and that GitHub Actions can publish
to it before merging a registry change. Image publication can run while
promotions are paused. Configure Render with a credential that can read the
private package. After the cutover, automatic development and staging releases
use those configured credentials without additional operator steps.

Transferring the source repository can change the expected image location while
retained images and Render services still use the previous location. Repository
redirects do not migrate packages or preserve Actions and Render access.
Keep restricted images in a private destination package.

Treat a registry change as a separately approved operating procedure. Inventory
live and rollback index/runtime digests, schemas, registry visibility and read/write
identities. Copy the complete selected image objects without rebuilding, preserve
their digests and verify destination pullability and metadata independently. Keep
the original images and references through sign-off. Missing permissions, conflicting
destination tags or changed digests stop the procedure.

Render deployment requests require the image repository to match the service's
configured image. In an approved window, coordinate automatic promotion, update only
the selected service's registry binding and deliberately redeploy the copied same
image bytes. Verify provider identity, readiness, release and mode-specific smoke
before resuming automation. Preserve a reviewed return procedure for uncertain or
failed provider operations. A same-release registry rebind is distinct from the
normal different-release application rollback; keep its guards intact.

Review operator templates and current repository-pinned tooling before the first
new-namespace release. The Blueprint is a setup template, not an instruction to sync
live services. Historical schema-maintenance tooling retains its original transition
boundary. Registry relocation does not authorize schema changes, credential rotation,
production promotion or deletion. Production needs its own exact window and approval.
After setup, verify fresh CI and image publication, automatic promotion for each
development or staging service where it is enabled, and the pullable compatible
rollback path in the destination namespace. A same-release rebind verifies the
registry binding. Use a fresh eligible release and its own staging evidence
for normal production promotion. Record
registry transfer results in the [registry migration record](https://github.com/heathcote-advisory/Project_Tabula_Rasa/issues/455)
and use [resource retirement](RESOURCE_RETIREMENT.md#retire-legacy-provider-access-and-bindings)
before removing any previous package, credential or reference.

## Manual production promotion

**Promote commercial production** is a manual workflow on protected `main`.
It promotes application code to an existing named-user production service.
The automatic commercial image workflow can publish images and deploy to
development and staging. Production dispatch is reserved for release operators
with repository write access. Keep write access limited to operators authorized
to release, use the available main-branch protections, and restrict the GitHub
`production` environment to `main` where supported. This workflow requires no
paid environment reviewer. Follow the [provider custody procedure](SECRETS.md#provider-account-custody-and-transfer)
for ownership and credential handoff.

### Production setup and recovery evidence

The checked-in Render Blueprint provisions development and staging. Before
enabling production promotion, obtain explicit operator approval for the first
live deployment or migration and any provider purchase. Provision and verify
these bindings privately:

| Location | Required setup |
| --- | --- |
| GitHub repository | Keep `COMMERCIAL_PRODUCTION_ENABLED` unset until approved and configured. Set it to `true` to enable production dispatch. |
| GitHub `production` environment variables | `RENDER_SERVICE_ID`, `TABULA_RASA_PUBLIC_ORIGIN`, `TABULA_RASA_ACCESS_MODE=named_user`, `TABULA_RASA_DEPLOY_DATABASE_SHA256`, `STAGING_RENDER_SERVICE_ID`, `PRODUCTION_OIDC_ISSUER` and `PRODUCTION_OIDC_CLIENT_ID`. |
| GitHub `production` environment secrets | `RENDER_API_KEY` with access to the production service, its required environment settings and deployments. `STAGING_RENDER_API_KEY` with read access to the staging service, domains, deploys and its direct TLS runtime database setting. `TABULA_RASA_MIGRATION_DATABASE_URL` for the distinct production migration role using the direct Neon endpoint with TLS. |
| Render production | Separate active image-backed web service. Disable automatic deployment and configure `/health/ready`. Set `TABULA_RASA_ENVIRONMENT=production`, `TABULA_RASA_ACCESS_MODE=named_user`, the exact public origin, production Zitadel issuer and client ID, and the direct TLS runtime DB URL as service-level variables. Keep the OIDC secret and session generation in protected runtime configuration. The public origin must be Render's service URL or a verified non-redirecting custom domain on that service. |
| Neon and Zitadel | Use a separate production Neon endpoint and a production Zitadel project/client. Verify runtime DML and migration ownership are separate, and verify the registered callback, named-user sign-in and session epoch under the [identity rollout procedure](../identity/HOSTED_ROLLOUT.md). |

The head-only promotion gate can use a dedicated login with `public` schema
`USAGE`/`CREATE` and ownership of only `alembic_version`. It needs no ledger DML,
superuser, role/database creation, replication or RLS-bypass privileges. Preserve
the maintenance owner's control of the version table and existing runtime ACLs
when assigning that ownership. Forward schema maintenance uses its separately
held migration owner; the promotion login is for the compatible-head gate.

Before dispatch, privately review and hash a recovery record that names the
production service and database fingerprint, candidate and currently live
digests, effective runtime and migration grants, and identity bindings. Record
the backup or PITR checkpoint, its age and retention, the verified restore path,
and the retained named-mode application rollback image and epoch compatibility.
The dispatch hash references the operator's evidence. The workflow checks the
reference format and live bindings. Backup retention and restore readiness are
verified by the operator under the [database recovery procedure](DATABASE_RECOVERY.md).
Avoid concurrent Render configuration changes, manual deploys, or other migrators
during the release window.

### Plan a forward schema maintenance window

Before interrupting service, use the actual operator terminal and existing
maintenance credential for a read-only check. Confirm the direct database
fingerprint, connected role, expected source revision and required migration
privileges. Compare the connection's endpoint fingerprint with the approved
production binding using the [same identity calculation as
deployment](../../tabula_rasa/infrastructure/postgres/identity.py). From that
terminal, connect with the maintenance credential using your approved private
credential input and run a read-only SQL check:

```sql
BEGIN READ ONLY;
SELECT current_user, current_database(),
       has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
       has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create;
SELECT version_num FROM public.alembic_version;
ROLLBACK;
```

Compare the results privately with the approved role and source revision. In
the same read-only session, verify the additional privileges and ownership
required by the specific migration. Keep connection strings out of command
arguments, shell history and evidence. Resolve connection or input failures
from the actual operator terminal while the current release is serving traffic.

The approved plan must name the staging-verified digest, the new schema grants,
all application and worker writers, and the way ingress stays restricted during
the image switch. Drain the writers, verify a fresh recovery point, and confirm
the current revision again before running the locked forward upgrade. Afterward,
check the committed revision, effective runtime and worker grants, and continued
separation of the promotion role before serving traffic on the new image.

On Render, saving an image URL and resuming a service are separate from verifying
the image currently serving traffic. A resumed service can still show the prior
deployment as live. Plan a way to keep traffic restricted until the approved
digest is actually live. For example, a maintenance rule on an existing upstream
gateway can restrict traffic if direct access to the service is also blocked.
Before the migration, verify the restriction from outside the operator access
path on every public hostname, including the direct Render URL. If the plan
cannot prevent an incompatible image from serving, stop and use a reviewed
compatibility bridge or another approved procedure. Verify the exact digest,
readiness, version and mode-specific smoke through the approved operator access
path before reopening ingress.

If a migration attempt stops, read back the database revision and grants before
deciding how to recover. With the original compatible state confirmed, use the
approved recovery steps to restore the prior image and apply the
[compatible rollback verification checks](#promotion-failure-and-rollback).
Once the new schema has committed, keep incompatible images out of service and
use reviewed forward repair or the [isolated database restore
procedure](DATABASE_RECOVERY.md).

### Schedule an approved forward release

A successful main CI run and immutable image build trigger staging immediately.
A schema-changing staging release takes the maintenance path in the same job
and queue. The production workflow checks daily at 03:00
`America/New_York`, including daylight saving changes. It considers the
currently live staging digest only when that exact image has a completed
successful build and staging job, a new Render deployment and post-restart
readiness, version and named-user smoke evidence by the cutoff. If staging
changes to a different digest during preflight or finishes verification after the cutoff, defer to a later
window. The start window defaults to 15 minutes. If the owner accepts later
downtime to accommodate GitHub scheduling delays, set the production environment
variable `PRODUCTION_RELEASE_START_WINDOW_MINUTES` to an integer between 15
and 180. The 03:00 Eastern eligibility cutoff still applies. The job skips
when it starts outside the configured window or is a GitHub rerun. The current
healthy production digest is skipped.

A staging restart or redeployment of the same immutable image does not reset
its original eligibility time or require the original Render deployment to
remain live. Verify the current live deployment's requested and runtime digests,
staging origin and isolated database binding. If its deployment ID differs from
the successful staging artifact, also check current `/version` and
`/health/ready` through the existing bounded, non-redirecting probe. Missing live
deployment, competing deployment, digest mismatch or failed probes block
promotion. Retain both the original verified deployment ID and current live
deployment ID in the production release record. These current health checks do
not qualify an image whose original staging verification missed the cutoff.

Before enabling unattended releases, verify the following in each applicable
environment.

- Keep Render auto-deploy off and bind the exact image service, HTTPS origin
  and direct Neon TLS database fingerprint. Use named-user access and a
  separately scoped migration owner. The production target also verifies
  Zitadel issuer and client settings and uses separate staging Render read access.
- Schema changes use full service downtime in staging and production. Rehearse
  suspension, empty instance and job lists, public 503 responses, updating the
  pinned image while suspended, and resuming only the new image on an isolated
  service with equivalent Render settings and tier. Record the approved target
  in `WINDOW_VERIFIED_SUSPEND_SERVICE_ID` after verifying those capabilities.
  Repeat verification when the provider tier or deployment behavior changes.
  An unverified target blocks before suspension or database mutation.
- Set `WINDOW_MIGRATION_DATABASE_URL` to the bound migration owner, or use
  the environment's existing scoped migration secret. The environment
  variable `WINDOW_EXCLUSIVE_RENDER_WRITER=true` records an inventory proving
  the service owns runtime writes during schema maintenance. External timers
  or writers require a separately reviewed drain plan before enabling this mode.
  In production, if the only external writer uses one dedicated login, set
  `WINDOW_WORKER_ROLE` to that login and leave the exclusive Render setting
  unset. Verify the role is dedicated to this deployment. On the bound production
  database, an administrator provisions [the scoped login function](../../scripts/provision_worker_login_fence.sql)
  with `psql -X -v worker_role=<dedicated-worker> -v migration_role=<migration-owner> -f scripts/provision_worker_login_fence.sql`.
  Connect with the administrator's protected connection settings. Review the
  exact role bindings before execution. The function can toggle only the
  selected worker login and grants execution to the migration role without
  making that role a role administrator. Scheduled production releases require
  installed-worker compatibility evidence from the qualifying staging run,
  as described below. The fence's schema list is derived from that evidence;
  `WINDOW_WORKER_COMPATIBLE_SCHEMAS` is no longer an unattended-release proof.
  The workflow disables login before suspending Render, waits for existing
  worker sessions to exit, and enables login after validating the restarted
  application and target schema. If the release fails with the worker fenced,
  inspect the deployed image, committed schema and role login before restoring
  access. Only restore access once the deployed worker is safe on that schema.
  Keep the NAS timer and backup task definitions in place.

- Configure `NEON_PROJECT_ID`, `NEON_BRANCH_ID`, `NEON_ENDPOINT_ID` and the
  project-scoped `NEON_RELEASE_API_KEY` secret in each environment that uses
  automatic checkpoints. The workflow verifies the exact read-write endpoint
  on the bound branch, at least one hour of configured recovery history, and
  historical schema access at the bound database's WAL position. A child branch
  must have a recovery point after its fork from the parent. It verifies
  a fresh recovery point after maintenance drain before applying migrations.
  Rehearse restoring that point to an isolated target before go-live. The
  independent NAS backup schedule and custody database have their own controls.

Before setting the repository variable `COMMERCIAL_AUTO_PRODUCTION_ENABLED=true`,
obtain explicit owner go-live approval, successful hosted staging evidence,
and agreement on the 03:00 window, allowed downtime and failure notification.
`COMMERCIAL_PRODUCTION_ENABLED` must also be true. Configure the production
environment to permit the trusted main schedule without a per-release reviewer
gate. Preserve separate customer activation and commercial acceptance decisions.
Manual production dispatch remains available for reviewed emergency use.

The shared downtime path suspends the service, checks instance and job drain
and public unavailability, verifies recovery, selects the new pinned image,
and applies the forward path in one locked transaction. It resumes the pinned
candidate. On Render Free, if resume leaves only the existing deployment and
no deployment is in progress, the workflow confirms the configured digest and
requests one explicit digest-pinned deployment. It checks the exact new
deployment and public readiness, version
and named-user smoke. Render may restore public traffic on resume before
smoke completes. A failed restart or smoke triggers containment by suspension.
A failed migration or interrupted deploy
supplies no success evidence. Follow the
[incident procedure](INCIDENT_RESPONSE.md#scheduled-schema-window-failure).

Production records an attempt before mutation. The same digest cannot be
attempted automatically again in that recovery generation. After diagnosing
and validating recovery, an authorized operator may increment the environment
variable `PRODUCTION_RECOVERY_GENERATION` to permit a fresh scheduled decision.
Use the GitHub Actions failure notification and the retained deployment status
to reach the release operator.

**Commercial schema attention** checks the bound schema after staging finishes.
It records one assigned issue per blocked transition, including unsupported
contracts and production activation needs. Configure
`SCHEMA_ATTENTION_ASSIGNEE` and an environment-scoped, direct Neon
`SCHEMA_ATTENTION_DATABASE_URL` with read access to `alembic_version`.
Close an existing alert after the associated live acceptance is verified.

**Hosted commercial schema rehearsal** runs the automatic release command from
main against an isolated Render image service and Neon database. Configure a
dedicated GitHub `schema-rehearsal` environment with the workflow's
`REHEARSAL_*` variables and secrets. Bind only disposable resources, start with
the pinned compatible old image serving, then dispatch with the tested main
candidate commit and its immutable image digest. Review the application
restart and post-restart smoke result in the rehearsal artifact. Repeat from
a fresh disposable source for another forward trial.

**Simulated commercial schema window** runs pinned images with disposable
PostgreSQL, local public/private routes and simulated provider controls.
Hosted Render and Neon acceptance requires a separate authorized staging run.

### Installed NAS worker qualification

Before enabling qualification, export the **installed** NAS worker image and
audit its complete configuration, build history and every layer (including
deleted files) for credentials and private data. Environment-key inspection
alone is not an audit. Publish only an approved artifact to a CI-readable
private registry repository; preserve the installed effective configuration
and RootFS layers. Do not rebuild current source and call it the installed
worker. Image publication and NAS upgrades require separate authorization.

Configure `NAS_WORKER_IMAGE` as a `ghcr.io/...@sha256:...` registry digest,
`NAS_WORKER_CONFIG_SHA256` and `NAS_WORKER_ROOTFS_SHA256` consistently for
staging and production. The fingerprints are SHA-256 of UTF-8 JSON using
sorted keys and compact separators: the export's `config` object and its
ordered `rootfs.diff_ids` array respectively (Docker inspection exposes these
as `Config` and `RootFS.Layers`). This compares the installed image content
even when registry tooling normalizes the outer image configuration. Never
print the full config or environment values. Record the installed/export
identity and audit decision in the protected operational record. Give the
workflow's package-read token access to that exact registry artifact.

Set staging's `NAS_WORKER_BASE_SCHEMA` to the earliest supported production
schema. Automatic staging qualification uses the candidate's reviewed linear
Alembic path to test that schema and every revision through the candidate
head. It uses candidate-image migrations and synthetic fixtures with the
unchanged ARM64 worker, emulated on the CI runner. Each revision gets a new
disposable PostgreSQL instance on an internal Docker network with no host
ports, production credentials or external network access. Probes check recurring
generation, repeated enqueue/execution, cross-business claim rejection and
separate amounts, and bounded retry/backoff/dead-state behavior. These probes
test the worker's repository/queue behavior with a synthetic runtime role;
they do not establish production grants, NAS scheduling or monitoring health.
Existing production privilege checks and login fencing remain required.

The staging `release.json` retains the probe results, candidate digest/schema,
installed-worker pin/fingerprints and qualifying run/attempt alongside existing
restart/readiness/sign-in-shell/access-boundary smoke. Production checks that
proof on selection and revalidation, requiring both its current schema and
the candidate schema before migration. Missing proof, failed checks, a different
worker pin or a stale attempt blocks promotion. There is no manual schema-list
fallback. With no staging worker pin, staging can still deploy and retain
application smoke, but that evidence cannot qualify a production worker.

Provision the audited registry pin and matching environment variables before
merging/enabling this contract for an unattended production worker. This setup
does not change the immediate-staging/03:00 Eastern production schedule,
authorize worker installation, or waive legal/customer release gates. After
an approved worker upgrade, update both environment bindings from the newly
installed export and obtain fresh staging qualification before promotion.

### Select and promote a release

1. In **Commercial release image**, select the completed automatic run whose
   **build** and **staging / deploy** jobs succeeded in the same attempt. For a
   schema-window release, use the completed **Commercial schema release window**
   run with a successful **staging / schema-window** job and restored traffic. The
   staging job summary lists the immutable image and its release/schema metadata.
   Use the run ID from its URL. The named-user staging evidence expires after
   seven days and must match the live staging image. A same-image redeployment
   additionally requires current version/readiness checks; its new deployment ID
   does not invalidate the original evidence. A staging rollback or manual staging
   deploy alone does not supply production evidence.
2. In **Promote commercial production**, select `main` and enter that immutable
   image, its staging run ID, the full expected live production image reference,
   and the SHA-256 of the reviewed recovery record. Choose **Run workflow** to
   authorize the promotion. Production runs serialize on the shared production
   environment lock. If staging advances or production's prior release changes
   while queued, select and review the new evidence before dispatching again.
3. Inspect the run summary and Render deployment. The dispatch-time code first
   verifies the candidate, then checks out its verified image commit and repeats
   preflight with that release's code and dependencies before any mutation. A
   release without production promotion support fails before migration or deploy.
   Preflight checks CI, registry
   metadata, live staging evidence, the isolated staging and production runtime
   database bindings, the intended production service and identity settings, and the current application's readiness and
   named-user boundary. The tested image runs the explicit migration gate
   against its bound database, with the Alembic lock. The current head-only
   runtime requires the live image, candidate and database to agree on schema,
   so the upgrade at head applies zero revisions. Schema transitions require a
   separately reviewed forward maintenance or bridge procedure.
4. Success requires the exact staged digest live in Render, expected version and
   database readiness, and a named-user smoke that checks the sign-in shell and
   denial of anonymous and forged legacy access. The summary records the staging
   proof, prior and promoted images, schema, deployment identity, timestamps,
   Render link, and outcome. The smoke does not sign in as a customer or access
   ledger data.

If the run fails, inspect the failed step and exact Render deployment before
retrying. If the candidate went live but failed smoke, restrict ingress and use
the [named-mode rollback checklist](../identity/HOSTED_ROLLOUT.md#compatible-application-rollback-and-incidents).
After queued promotions settle, select the exact retained compatible digest
in Render and verify provider digest, version, readiness and named-user access.
Preserve the forward schema and retain the failed workflow record. If the prior
image or identity epoch is incompatible, keep ingress restricted and use the
reviewed forward repair procedure.

## Hosted deployment verification

Record safe origin, target fingerprint, image digest, release SHA and schema
after a successful deployment. Retain the migration and browser smoke results
and the compatible rollback evidence for the selected environment. Verify
runtime and migration role separation and the actual ingress and access gate.
Keep credentials and private drill evidence in operator custody.

Use [release validation](../browser/RELEASE_VALIDATION.md) for the workflow matrix,
and [named-user hosted rollout](../identity/HOSTED_ROLLOUT.md) for provider,
rollback and manual acceptance. Keep current provider plan and capacity
observations with the release evidence.

## Operational telemetry

[Operational observability](OBSERVABILITY.md) owns the sanitized telemetry contract,
optional collection setup, tier/retention/access checkpoint, alert thresholds, staging
smoke and platform operations integration responsibilities.

## Stage-specific operational readiness

The release operator assembles an evidence matrix before changing the supported
stage. Keep dated results, acceptance checkboxes and approval references in the
owning operational discussion, not in this guide. Record the exact selected
release, schema, topology, target binding and observation time for every result.
Main CI, an image build, a deployment record and an observed running service are
distinct evidence. A later main commit does not refresh an earlier live drill.

| Minimum | Restricted synthetic technical alpha | Trusted owner-operated bookkeeping | Unrelated external beta / commercial production |
| --- | --- | --- | --- |
| Access and scope | Explicit synthetic scope and protected ingress; named mode or the supported restricted gate; no unrelated customer admission | Named owner, exact authorized business/document bindings, fresh membership and cross-business denial; preserve divergent PostgreSQL authority | Selected-release named identity, business roles, revocation and browser isolation; usable account/admin recovery before admission |
| Recovery | Explicitly accepted synthetic loss/availability limits; distinguish DB and document-byte coverage and disclose unperformed checks | Achieved PostgreSQL protection plus separate byte archives, compatible metadata/links, retained source/history, owner custody and a usable recovery procedure | Accepted stage RPO/RTO, retention, capacity, recovery points, full-service boundaries and operator/escalation coverage, with independent alternate recovery when commercially required |
| Jobs | No timer is implied by a successful manual probe; approve each synthetic mutation/window separately | Exact business/time zone, bounded durable worker, observed scheduled execution and failure/remediation, missed-run/terminal monitoring and retained attempts | Representative selected-cohort operation, retry/deduplication and delivered incidents meeting accepted cadence/capacity; separately approved activation |
| Provider limits and privacy | Inspect effective region/tier/headroom/shared consumption, idle behavior, logging and grant limits; accept bounded technical gaps explicitly | Recheck those limits against actual owner workload, secret/key custody, alert recipient and backup-health responsibilities | Inspect selected provider and downstream/proxy/sink access/retention, usage eligibility, quota/cost headroom and incident response; free tiers pass only if achieved outcomes meet the accepted stage |
| Validation and approval | Current-revision authoritative CI/review plus applicable synthetic checks; no automatic release from a repository pass | Consume exact owner-cutover evidence and approved protection limits; operations work must not disturb the working owner service | Consume security and operations evidence; separate beta admission, commercial authority activation and paid-release decisions remain with their human release owners |

For each minimum, record: acceptance item; pass/fail/unperformed/not applicable;
immutable evidence URL and tested revision; actual resource/topology reference;
remaining work and dependency; responsible human/component owner; and explicit
stage approval or the exact missing decision. An owner named as the requested
approver is not evidence that they have accepted. Deferrals require scope, a
specific owning issue, responsible owner and completion/reconsideration trigger.
Do not waive an essential protection by labeling it cosmetic or deferring it to
paid launch when beta already needs it.

Inspect actual Render/Neon region, compute/tier, project-wide consumption,
connection budget, suspension/history and remaining capacity. Inspect selected
document storage headroom, effective Cloud project quota, grant lifecycle and
shared account/client failure boundary. Console values, operator attestations and
published limits must be identified separately; templates and account locality
do not establish effective settings. Provider purchases and configuration changes
retain their separate approval checkpoints.

Use [controlled drills](INCIDENT_DRILLS.md#dependency-and-job-drill-preflight)
for execution and [incident evidence](INCIDENT_RESPONSE.md#timeline-evidence-and-closure)
for sanitized handoff. Consume [hosted rollout](../identity/HOSTED_ROLLOUT.md#hosted-rehearsal-and-expected-results),
[observability](OBSERVABILITY.md#metrics-alerts-and-first-triage),
[job operation](JOBS_AND_AUDIT.md#operator-entrypoints-and-manual-hosted-checkpoint)
and [support](SUPPORT.md) at their proven scope. A configured alert is not a
delivered incident; email settings are not delivered email. Verify recipient
acknowledgment and actual recovery separately, including manually resolved job
incidents. Keep a real provider fault distinct from source tests or simulated logs.

### Recovery replacement and handoff

Before retiring a recovery capability, inventory its live, manual, test and
operator consumers. Classify each as retained, replaced and eligible, or deferred
to a specific issue with owner and trigger. Reuse the
[resource retirement procedure](RESOURCE_RETIREMENT.md); replacement evidence
must match the selected stage's guarantee, not just share a backup label.

Verify the [database restore](DATABASE_RECOVERY.md),
[audit history/protections](AUDIT_RECOVERY.md), and separate
[receipt archive/restore](../storage/RECEIPT_RECOVERY.md), including compatible
metadata/link identities, available recovery points, scoped credentials and key
custody. Database archives do not contain external receipt bytes. A manual byte
archive does not establish a document NAS scheduler, continuous protection or
alternate-operator recovery. Record the DB/bytes capture boundaries and any
unrecoverable interval; do not combine unrelated historical passes into a new
whole-service pass. Verify restored state before forward migration and apply the
canonical return-to-service/authority rules before any separately approved rebind.

Preserve archived divergent Sheets sources and disabled old writers, receipt
bytes, NAS generations/checksums and deployed schema history. Never reactivate
stale Sheets after PostgreSQL divergence. Capability retirement provides no data
disposal authority: each resource deletion needs its own exact-target approval.
Issue closure, technical handoff and recovery verification supply no beta,
commercial cutover or charging approval.

## Customer Drive OAuth

See [customer Google Drive setup](../identity/GOOGLE_DRIVE.md) for the optional
`TABULA_RASA_CUSTOMER_DRIVE_OAUTH_FILE`, exact callback, forward migration and
persistent writable encrypted grant custody. Read-only secret-file mounts alone
cannot support self-service customer grants. Configure and verify approved staging
before customer admission; no production setup is implied.

### Production credential custody and recovery

Customer Google Drive OAuth requires durable, writable encrypted credential custody.
The application supports a dedicated PostgreSQL credential database, separate from the
 bookkeeping database.

#### Required configuration

| Setting | Purpose |
|---|---|
| `TABULA_RASA_CONNECTION_SECRETS_DATABASE_URL` | Dedicated PostgreSQL credential database connection |
| `TABULA_RASA_CONNECTION_SECRETS_KEY` | Encryption and decryption of stored credentials |
| `TABULA_RASA_CUSTOMER_DRIVE_OAUTH_FILE` | Server-side Google OAuth client configuration |

The credential database must use a dedicated restricted runtime role with CONNECT on the
credential database, USAGE on the connection_custody schema, and SELECT/INSERT on connection_custody.grants.
Do not grant UPDATE, DELETE, DDL, role administration, or bookkeeping database access. Use a 
TLS-verified connection. Runtime access must not include schema administration, credential 
overwrites/deletion, or bookkeeping privileges.

Use only one credential-storage backend. Do not configure the legacy file-directory backend
alongside the PostgreSQL backend.

The encryption key must remain available to the running application and be recoverable 
separately from the hosting provider.

#### Credential recovery scenarios

**1. Google authorization expires or is revoked**

When the existing encrypted grant and connection metadata remain intact:

1. Have an authorized business Owner initiate Reconnect.
2. Reauthorize using the original Google account.
3. Verify the existing managed folder bindings.
4. Confirm receipt access without modifying existing document links.

Do not substitute another Google account or create replacement folders.

**2. Credential database becomes temporarily unavailable**

1. Preserve the bookkeeping database and document metadata.
2. Diagnose database connectivity, TLS, permissions, and provider availability.
3. Restore access to the existing credential database.
4. Confirm connections can read their original encrypted grants.
5. Validate an existing receipt download before declaring recovery complete.

Do not fall back automatically to historical file-based credentials.

**3. Encryption key becomes unavailable**

1. Preserve all encrypted credential records.
2. Recover the matching encryption key from approved separate custody.
3. Restore the required application configuration.
4. Verify decryption and an existing receipt operation.

Do not generate a replacement key for existing ciphertext or overwrite encrypted records. If the encryption
key is permanently lost, treat it as a permanent credential database loss and follow Scenario 4.

**4. Credential database is permanently lost**

Recovery depends on the deployed application's recovery capabilities and the available recovery artifacts.

1. Preserve the bookkeeping database, connection metadata, original Drive folders, and receipt links.
2. Inventory available ciphertext backups, historical grant files, and matching encryption keys.
3. Determine whether the deployed schema and application support credential-loss reconnection using 
independently retained, verified original-account and managed-folder bindings.
4. Verify that every affected connection was enrolled in that recovery mechanism before its grant 
was lost.

   Connections established before the recovery-binding schema revision must complete the documented enrollment
procedure while their original encrypted grants remain readable. Deployment of the recovery feature does
not automatically enroll historical connections.
5. If supported, restore a compatible custody database with the schema and tables pre-provisioned (since
the runtime role lacks DDL privileges) and follow the documented recovery-authorization workflow using the
original Google account.
6. Otherwise, use a separately approved credential restoration or technical remediation procedure.
7. Verify original document bindings and receipt-byte access before restoring service.

Ordinary Reconnect must not be assumed to recover a connection whose encrypted grant has been permanently lost.

Historical grant files may not contain credentials issued after migration to database-backed custody.
Restoring an incomplete historical snapshot must not silently replace newer connection records.

Initial folder selection is not an existing-connection recovery mechanism.

#### Backup and rollback safeguards

- Maintain the approved protection level for the applicable operating stage.
- Evaluate credential ciphertext, the encryption key, bookkeeping metadata, and external receipt bytes
as separate recovery dependencies.
- Preserve opaque credential references and environment/business/provider bindings during restoration.
- Never restore an older credential store without reconciling references created after its recovery point.
Compare the bookkeeping database's connection references against the restored credential records and
identify missing or mismatched references.

  For each affected connection, restore its original compatible grant or use the documented credential-loss
recovery procedure if the deployed schema and connection enrollment support it. Otherwise, retain the
connection as unavailable pending approved remediation. Never silently rebind or replace the original
managed folders.
- Do not revoke Google grants or delete Drive folders as an application rollback shortcut.
- Do not treat a successful database restore as proof that receipt bytes or Google authorization have
been recovered.
- Verify recovered access through an original-account connection and an existing receipt.

For any operating stage that accepts reduced credential-backup protection, record the authorization,
recovery limitations, and residual risk in the applicable operational acceptance record. Such an exception
does not automatically extend to another stage.

#### Recovery acceptance

Before enabling a new operating stage, confirm:

- The selected credential backend and restricted runtime role are correct.
- The encryption key is separately recoverable.
- Available credential and receipt recovery protections satisfy that stage's approved recovery objectives,
or an explicitly permitted exception is recorded.
- Current deployment and database schema support the intended recovery procedure.
- Existing connected businesses retain their original document bindings.
- Recovery and rollback responsibilities are assigned to authorized operators.

A successful deployment, Connected status, or historical restore exercise alone does not establish full-
service recovery readiness.

External customer admission and commercial activation require their own stage-specific recovery and release
approvals.
