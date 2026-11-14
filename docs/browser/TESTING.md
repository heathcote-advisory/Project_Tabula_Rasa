# Browser testing

Browser checks cover the shared shell, protected forms, bookkeeping workflows,
responsive layout and accessibility assertions. Controlled transport checks and
native HTTP/PostgreSQL journeys protect different boundaries. Use
[visual review](VISUAL_REVIEW.md) for screenshot and manual evidence, and
[CI ownership](../development/CI.md) for the authoritative full-suite gate.

## Repeatable local and CI gate

Keep the selected Playwright framework from [ADR 0002](../adr/0002-commercial-web-runtime.md).
Node Playwright checks the shell with controlled synthetic transport responses.
Python Playwright exercises the compiled React application through the production
HTTP handlers, application services and native PostgreSQL. Both use pinned
development dependencies; axe-core checks WCAG 2.0/2.1/2.2 A/AA rules with no
violation exclusions. [Playwright's accessibility guidance](https://playwright.dev/docs/accessibility-testing)
describes the automated/manual assessment boundary.

From the repository root in the active Python 3.12 development environment,
explicitly export `TABULA_RASA_TEST_DATABASE_URL` for a disposable test
administrator endpoint, then run:

```powershell
npm --prefix web ci
npm --prefix web exec -- playwright install chromium firefox webkit
python -m playwright install chromium firefox webkit
npm --prefix web run build
npm --prefix web run lint
npm --prefix web test
npm --prefix web run test:e2e
$env:TABULA_RASA_REQUIRE_BROWSER_TESTS = '1'
$native = Get-ChildItem tests/postgres/test*browser*.py | ForEach-Object { $_.FullName }
python -m pytest @native -ra --durations=20 --junitxml=native-browser-results.xml
```

The test endpoint must support creation/removal of disposable databases. Tests
never infer it from the application database, profiles or credentials. Each
native scenario creates a random test database, applies the current migrations,
seeds deterministic Alpha/Beta data and starts an isolated loopback HTTP server.
Fixture teardown stops the server, closes pools and drops that test database,
including after assertion failures. Missing database configuration, compiled
assets or required browser engines fails the required gate; ordinary Python runs
may still skip native browser tests when their environment is not installed.

Native tests request the session-scoped `native_browser` fixture in
`tests/postgres/conftest.py`. `NativeBrowsers` in
`tests/postgres/browser_support.py` starts Playwright once per pytest process
and lazily launches each requested engine once. Each test uses
`with native_browser(engine) as browser:` and creates fresh contexts/pages;
the block closes every context even after assertion failure. Cookies, storage,
routes, downloads and pages never carry into the next test. Session teardown
closes the browser processes before stopping Playwright. The existing per-test
database and HTTP-server fixtures retain their lifetimes; never share a context
or database merely to reduce setup cost. Tests with blocked HTTP work release
it before browser-context cleanup. The standalone deployment-smoke entry point
owns its own Playwright runtime and is tested in a subprocess, avoiding a
nested synchronous Playwright event loop in the shared pytest process.

The native core matrix in `test_browser_journey.py` runs the shared
`browser_journey.py` journey on every existing
engine/viewport pair. Reference creation requires both the successful HTTP
response and the form's `Record created.` status before the next navigation.
The response event alone does not prove the form consumed the result and
cleared its unsaved-changes guard. Delayed-operation and receipt-failure
regressions seed
only their prerequisite synthetic record through the existing services, then
call the same `receipt_workflow` or `delete_journal` step used by the full
journey. These focused cases retain real browser/HTTP/PostgreSQL mutations,
operation-specific safe errors, the beyond-default-wait delay, retained receipt
bytes and other-business isolation without repeating unrelated reference setup,
conflict editing, reports and cleanup flows.

To rerun only the journey shard locally after the same setup, run:

```powershell
$env:TABULA_RASA_REQUIRE_BROWSER_TESTS = '1'
python -m pytest tests/postgres/test_browser_journey.py -ra --durations=20 --junitxml=native-browser-results.xml
```

Each required [native browser shard](../development/CI.md#required-execution-owners)
builds its own SPA and uses its own disposable PostgreSQL service. Each module
runs in exactly one shard, with all its engine/viewport cases. **Full test suite**
requires each shard to succeed on the current head/merge candidate. Shards
retain separate results artifacts for diagnosis. The commercial release-image
workflow consumes successful Repository CI on `main`. No external customer data,
ledger files or customer credentials are required.

`npm --prefix web run test:e2e` remains the complete local Node check: run-once
HTTP/Git contracts followed by all browser/device UI cases. Use `test:contracts`
for browser-independent preview/digest changes, or `test:browser -- --shard=1/2`
and `--shard=2/2` to reproduce CI UI partitions (run both for complete coverage).
[Suite ownership and timing evidence](../development/CI.md) explains why the HTTP/Git
contracts no longer run four times and why real API/database journeys remain
independent required coverage.

## Bounded coverage matrix

| Surface | Automated coverage |
| --- | --- |
| Shell, deep links, loading/error/empty states | Chromium, Firefox and WebKit; 1440, 1200, 900, 390 and 320 CSS-pixel widths |
| Reference setup, transaction lifecycle, balanced journal creation/edit, ledger and reports | Real HTTP/PostgreSQL journeys at 1440x1000 and 390x844 in all three engines |
| Core create forms, shell, reference/ledger/report tables | axe scans at 1440x1000, 1200x900, 900x900, 390x844 and 320x844 in Chromium |
| Keyboard confirmation, focus restoration, business/draft isolation | All three engines at desktop/mobile widths; delete, reload, business switch, local close and blocked route navigation |
| Protected stale edit/delete and missing baseline | Existing protected browser mutations real two-client browser/API/PostgreSQL path, including desktop/mobile independently loaded forms |
| Loading, field validation, unexpected server error and dropped transport | Desktop/mobile; real delayed service read, actual 422/500 handlers and controlled dropped transport; no automatic mutation retry |

Tables remain complete and scroll inside labeled keyboard-focusable regions.
Checks reject page-level horizontal overflow, require modal keyboard wrapping and
Escape cancellation, and check that explicit route changes focus the main region.
Server-declared, unambiguous top-level field errors connect `aria-invalid` and
`aria-describedby` to a generic error message; ambiguous journal-posting leaf
errors remain in the shared alert. Drafts, stable create identities and original
protected baselines remain owned by the existing form/service implementations.

## Alpha acceptance and remaining coverage

The repeatable automated gate above uses disposable databases and synthetic providers.
It does not certify a deployed service or actual-device behavior. [Technical
alpha](RELEASE_VALIDATION.md) defines the retained core workflow matrix and links its
completed historical acceptance and approved screen-reader exclusion. The hosted
synthetic runner is retired; [named-user hosted rollout](../identity/HOSTED_ROLLOUT.md)
retains real-provider, rollback and manual browser/accessibility acceptance. Recurring/mixed-use,
assets and mileage have their own supported workflow guides; Existing-workflow ports are
documented in [Workflow ports](IMPORTS_EXPORTS_AND_INTEGRATIONS.md).

For each new release's accepted scope, record actual browser/device versions,
touch/keyboard, zoom/reflow, focus and accessibility observations or explicit
approved exclusions. Managed engines/emulation do not prove physical-device
behavior or comprehensive WCAG conformance. Keep material findings in a linked
issue; do not weaken axe rules to hide them.

## Evidence and review boundaries

Keep candidate-specific runs, failures and accepted exclusions in the owning issue or
PR. [Visual review](VISUAL_REVIEW.md) defines screenshot provenance, before/after
inspection and manual accessibility observations. Real provider verification follows
[hosted rollout](../identity/HOSTED_ROLLOUT.md); live owner activation follows
[cutover](../accounting/CUTOVER.md). Automated browser success does not supply
physical-device, screen-reader, external-admission or recovery acceptance. Preserve
every supported workflow's required tests and honest gaps.
