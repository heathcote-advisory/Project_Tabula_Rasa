# HTTP and browser foundation

The FastAPI and React foundation owns request context, safe errors, routing,
query state and shared forms. [Runtime](RUNTIME.md) describes package and
deployment topology; [ADR 0002](../adr/0002-commercial-web-runtime.md) records
the stack decision. Domain services retain bookkeeping and persistence rules.

## Delivered scope and subsequent owners

The runnable foundation includes a responsive shell, history routing, a read-only
accounts view, a representative account-create API, generated transport types,
safe errors, health/version routes, request instrumentation, context seams,
query/mutation conventions and loaded-form primitives.

This is a synthetic local technical-alpha foundation. Domain workflow coverage belongs
to domain workflow services. [Concurrency](CONCURRENCY.md) describes loaded-baseline
propagation; [identity](../identity/AUTHENTICATION.md) owns named sessions and
memberships; [deployment](../operations/DEPLOYMENT.md) owns hosted configuration;
[browser testing](TESTING.md) owns wider browser/device checks.
[Bookkeeping](BOOKKEEPING.md), [mileage/receipts](MILEAGE_AND_RECEIPTS.md) and
[secondary workflows](RECURRING_AND_ASSETS.md) describe the supported callers.

## Transport and service composition

- JSON resources use `/api/v1/entities/{entity_id}/{resource}`; commands use an
  explicit action subpath when introduced. IDs are opaque strings. Dates are ISO
  calendar dates; instants are UTC; exact decimal values are strings.
- `GET /api/v1/session` returns safe server-resolved actor, organization, business,
  capabilities, cache scope and CSRF data. This currently describes an explicit
  trusted synthetic context in restricted alpha. Opt-in named mode now reports
  `ready` or authenticated `needs_setup` through the durable membership/session
  resolver; see [Named-user authentication](../identity/AUTHENTICATION.md). The complete
  action-policy and external-release gates remain separate.
- `GET /api/v1/entities/{entity_id}/accounts?active_only=false` delegates to
  `AccountService.list`. `POST` delegates directly to `AccountService.create`
  and returns 201 only after that service confirms the operation.
- Request/response Pydantic DTOs live in `http/models`. Response mapping
  allowlists fields rather than serializing service dictionaries wholesale.
  Handlers are synchronous, keeping an entire service operation in one worker
  thread. SQL, transaction boundaries and accounting rules remain with their
  existing owners.
- `create_app` owns lifespan startup/shutdown. The default runtime owns one
  PostgreSQL pool, verifies the named synthetic entity without creating it,
  composes each request after context checks, and disposes the pool on shutdown.
  Startup never migrates, seeds or reconciles recurring entries.
- `PostgresRuntime` requires an explicit service factory. The default local
  factory uses the fixed `alpha-synthetic` and `beta-synthetic` demonstration
  fixtures for local functional selection. See [Core workflows](BOOKKEEPING.md).
  For migrated entities,
  use `MigratedProfileServices` with a **server-configured** entity-to-profile
  mapping. It calls existing `create_application`, preserving authority cutover authority,
  environment and document-store binding checks. Its optional legacy provider
  dependencies are installed only for that operator composition; the synthetic
  API and schema exporter do not import Streamlit, Sheets or Google clients.
- `open_migrated_runtime` supplies the shared lifecycle for accepted migrated
  profiles. It validates all server-configured authority/document bindings and
  schema readiness at startup, requires the named membership resolver in
  production/named-user mode and matching identity/ledger database/environment,
  refuses synthetic contexts and disposes failed pools. Lasting owner use keeps
  the existing non-commercial cutover policy in a normal production app; see the
  [owner-operated procedure](../accounting/CUTOVER.md#owner-operated-production-procedure).
  Named identity and workflow services have separate owners; this factory's
  synthetic tests do not authorize live use. live owner cutover owns selected-runtime evidence.

The [central API authorization inventory](../identity/AUTHORIZATION.md) owns server
authorization's registered action requirements, route audit, privileged-operation
boundaries and before-binding denial tests. Future private operations consume that
guard.

## Identity and request protection seam

[ADR 0003](../adr/0003-commercial-identity-and-tenancy.md) selects the named OIDC
identity, durable session, role and server-membership contract. Follow [Commercial
identity](../identity/PROVIDER_SETUP.md) for its staged implementation, operator
checkpoints and testable threat model. Named-user session DTOs support users without an
entity. The implemented server authorization registered action-policy guard requires
explicit capabilities while retaining Origin/CSRF; see [API
authorization](../identity/AUTHORIZATION.md). Restricted synthetic alpha remains a
separate supported mode, not commercial membership authorization.

Inject a `runtime_factory(settings)` into `create_app`. Its resolver receives a
Starlette request and returns the immutable `RequestContext`. identity and tenancy must
resolve active membership and capabilities server-side on every call before service or
credential binding. Supply a nonsecret cache scope that changes across sessions and
membership contexts, and a session-bound CSRF token. The browser's selected entity,
route/body/profile key, baseline or header never proves authorization.

`resolve_context` checks entity agreement and the registered route's action capability;
unsafe operations additionally require the exact configured Origin and `X-CSRF-Token`.
API schema/docs use the same read context guard. An absent resolver denies access with
401. The default trusted resolver requires a loopback client and explicit
development/test configuration. deployment adds a fixed hosted synthetic scope behind
its environment-specific HTTP Basic gate; public HTTPS configuration, credentials and
bound Neon connectivity fail closed. Production rejects synthetic context and alpha
credentials. [Technical deployment](../operations/DEPLOYMENT.md) owns the operator
procedure. [hosted identity rollout](../identity/HOSTED_ROLLOUT.md) tracks selected
hosted named-user verification; external/customer access never uses the alpha gate as
membership authorization.

HTTP settings are read only from process environment. Database settings retain
their existing loader. `TABULA_RASA_PUBLIC_ORIGIN` is the exact browser origin,
without a trailing slash. Vite forwards the original Host on `/api` calls;
direct data/schema requests to API port 8000 therefore need the configured Host.
Host names are compared case-insensitively. Minimal health and release probes
(`/health/live`, `/health/ready`, `/version`) are exempt from Host checks and
expose no business data.
No CORS or browser API-origin override is enabled.

## Errors, request IDs and operational hooks

Errors have the transport shape:

```json
{
  "error": {"code": "stale", "message": "The record changed since it was loaded. Review the latest record."},
  "request_id": "server-generated-reference"
}
```

`error.fields`, when present, contains declared field names, machine codes and
safe generic instructions. Input values, unexpected field names, SQL, provider
errors, cookies, credentials and draft values never enter errors or request logs.
The code enum is generated into the browser contract.

| HTTP | Machine codes |
| --- | --- |
| 422 | `validation`, `reference`, `invariant`, `required`, `baseline_required`, `baseline_invalid` |
| 404 | `not_found` |
| 409 | `conflict`, `duplicate`, `concurrent_write`, `stale` |
| 401 / 403 | `unauthorized` / `forbidden` |
| 503 / 500 | `unavailable` / `unexpected` |
| 405 | `method_not_allowed` |

Every response gets a generated `X-Request-ID`; supplied request IDs are never
adopted. Optional `X-Correlation-ID` values are pseudonymized with the configured
environment telemetry key and omitted without one; they never grant identity.
[Operational observability](../operations/OBSERVABILITY.md) owns the sanitized request/security
contract, console/optional collector configuration, retention and alert setup.
Raw Uvicorn access logging is disabled by the factory; unexpected request
exceptions are consumed into generic safe responses.

`/health/live` checks process liveness. `/health/ready` checks the existing
bounded database connectivity probe and exact current Alembic head, returning
503 on unavailable/incompatible schema without migrating. `/version` exposes
only a bounded safe release identifier. Private JSON/schema responses use
`Cache-Control: no-store`; compiled hashed assets use immutable caching, and
the SPA entry uses revalidation. Only GET/HEAD HTML navigation can fall back to
the SPA; unknown API paths, non-HTML requests and missing assets retain errors.

## Browser ownership and form conventions

[Commercial UI foundations](UI_COMPONENTS.md) documents shared UI components's design
audit, shared tokens/action/header/table hooks and representative consumers in the
browser design system. These presentation primitives extend the owners below without
replacing form, query, context or workflow state.

`src/api/schema.d.ts` is generated from committed OpenAPI; feature code imports
these types rather than redefining transport records. `createApi` uses relative
same-origin requests, includes credentials, propagates read cancellation and
CSRF, and replaces raw network failures with safe `ApiError` values.

`resourceKey(session, resource)` includes the server session cache scope and entity ID.
`ScopeBoundary` gives each context its own QueryClient and remounts feature drafts when
server session/entity/capabilities change. It cancels reads and clears old private
caches on teardown. identity and tenancy will feed refreshed membership context and
unmount this boundary on logout. The visible business label comes from the server; the
shell does not provide a client authorization override.

Reads retry unavailable failures at most once. Mutations never retry
automatically. `useCreateAccount` invalidates only that context's accounts
after confirmed success or failure, including an uncertain network result.
Features should invalidate their known affected resources and stop for explicit
inspection of uncertain writes, rather than using a second client recovery loop.

`useLoadedForm({values, metadata})` initializes React Hook Form's editable
values and a cloned, frozen load-metadata snapshot separately. Rerender/refetch
does not replace either draft or baseline. Its explicit `reload(next)` resets
both together; a feature calls it only after the user chooses to discard or
review changes. Never obtain a fresh baseline during submission. Validation,
conflict or unexpected errors leave the form intact. `UnsavedChanges` blocks
router navigation while dirty with keep/discard actions and requests the
browser's native before-unload confirmation.

Shared loading, empty, validation/conflict/error, success and labeled-field
primitives use semantic roles. Connect a field's input `aria-invalid` and
`aria-describedby="<id>-error"` to the supplied field error. Conflict state
offers an explicit review action; no error generates a success message.

## Validation and generated contract

From root with the development environment active:

```powershell
python -m pytest tests/http tests/postgres/test_http_foundation.py
python -m tabula_rasa.http.export_openapi --output contracts/http/openapi.json
npm --prefix web ci
npm --prefix web run api:generate
git diff --exit-code -- contracts/http/openapi.json web/src/api/schema.d.ts
npm --prefix web run build
npm --prefix web run lint
npm --prefix web test
npm --prefix web exec -- playwright install chromium
npm --prefix web run test:e2e
```

Native tests require the explicit disposable `TABULA_RASA_TEST_DATABASE_URL`; they never
infer it from the runtime target. HTTP tests prove service delegation, stable safe
errors, context/capability/Origin/CSRF boundaries, pool lifecycle, schema access and
offline deterministic generation. Native tests cover read/create/domain failures and
two-entity isolation. Browser tests cover typed requests, mutation retry/invalidation,
session/entity cache isolation, loaded metadata retention, unsaved navigation and
reusable states. Playwright smoke tests use intercepted synthetic responses at 1440,
1200, 900, 390 and 320 CSS-pixel widths. This is Chromium smoke evidence, not
real-device or full cross-browser certification; browser testing owns that wider gate.
If a development preview occupies port 5173, set `TABULA_RASA_SMOKE_PORT` to an unused
loopback port before `npm --prefix web run test:e2e`. The smoke runner starts its own
server and does not reuse or stop the existing preview.

[Repository CI](../DEVELOPMENT.md#authoritative-ci-and-required-check) exports and
regenerates both contract artifacts, rejects drift, compiles the consumer and
runs the browser foundation checks. Its application and PostgreSQL Python jobs
partition API/native PostgreSQL tests without a duplicate foundation pytest invocation. A dedicated
Windows job covers platform-specific setup tests. The aggregate **Full test
suite** check gates the current PR candidate while CI and advisory review proceed
concurrently; publication and review completion require no ritual full local run.
