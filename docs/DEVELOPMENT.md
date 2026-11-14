# Development

This guide covers local development, validation, smoke testing, and the
human-facing pull-request workflow. Repository-wide agent and review rules
live in [AGENTS.md](../AGENTS.md); Gemini-specific reviewer behavior lives in
[.gemini/styleguide.md](../.gemini/styleguide.md).

## Environment

Use Python 3.12 or newer and install runtime and development dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

The Codex-managed Windows environment intentionally requires Python 3.12 so
worktrees use one interpreter series. Its environment setup fails clearly when
that interpreter is unavailable.

The optional PostgreSQL development runtime, migration policy, environment
variables, and real-database test setup are documented in
[PostgreSQL development and operations](database/POSTGRESQL.md). Development dependencies
include the database tools; the Sheets runtime does not require them or a DB URL.

## Browser/API development

[ADR 0002](adr/0002-commercial-web-runtime.md) defines the React/FastAPI stack. [Runtime
development](browser/RUNTIME.md) covers Python 3.12/Node 24, package ownership,
Vite/API/PostgreSQL topology and generated API contracts. [Foundation
conventions](browser/FOUNDATION.md) describes request context, safe errors, query/cache
ownership and shared forms. Application services own business rules; transport code must
not duplicate them.

[Deployment](operations/DEPLOYMENT.md) covers explicit environment and secret
configuration, schema migration, image promotion, smoke checks and rollback. [Hosted
identity rollout](identity/HOSTED_ROLLOUT.md) verifies real provider configuration and
named sessions. [Release validation](browser/RELEASE_VALIDATION.md) defines the selected
workflow matrix and Streamlit retirement boundary. Synthetic tests do not replace hosted
acceptance or operator approval.

[Identity](identity/AUTHENTICATION.md) and [authorization](identity/AUTHORIZATION.md)
own session and membership boundaries. [Protected forms](browser/CONCURRENCY.md) carry
loaded baselines into mutations. [Imports and
integrations](browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md) and
[recurring/assets](browser/RECURRING_AND_ASSETS.md) reuse those owners. Generation and
depreciation posting are explicit commands; page loads remain read-only. Durable
execution belongs to [jobs](operations/JOBS_AND_AUDIT.md).

[Receipt storage and recovery](storage/RECEIPT_RECOVERY.md) uses Drive and PostgreSQL;
normal operation requires no AWS setup or S3 SDK. Optional managed storage requires a
separate provider-adoption decision. Verify real grant/folder and Maps key restrictions
before live use.

[Incident response](operations/INCIDENT_RESPONSE.md), [secret
operations](operations/SECRETS.md), [support operations](operations/SUPPORT.md) and
[controlled drills](operations/INCIDENT_DRILLS.md) define reusable operator procedures.
[Customer lifecycle](operations/CUSTOMER_LIFECYCLE.md) owns closure, privacy requests
and the distinction between effective preservation and proposed retention deadlines.
Keep actual outcomes and gaps in approved evidence custody. [Resource
retirement](operations/RESOURCE_RETIREMENT.md) requires a fresh private inventory and
exact-target approval; preserve frozen sources, indefinite NAS retention and recovery
obligations.

Preserve PostgreSQL adapter/transport coverage and independent retained operator,
migration and recovery coverage. [CI ownership](development/CI.md) defines required execution; the [capability
retirement record](https://github.com/Roklion/Project_Tabula_Rasa/issues/377) retains
historical caller/evidence decisions.

## Local main-branch push guard

Use `heathcote-advisory/Project_Tabula_Rasa` for current GitHub CLI and API
operations. Check `git remote get-url origin` after a repository transfer and
before publishing. Historical issue and Actions links can retain their original
paths. Confirm the current repository directly when checking permissions or
configuring automation.

The tracked .githooks/pre-push hook blocks pushes that would update remote
main. Git does not activate tracked hooks automatically. After a clone:

```powershell
git config --local core.hooksPath .githooks
git config --local --get core.hooksPath
```

The second command should print .githooks. The guard applies only to the
configured clone and is not a substitute for server-side branch protection.

## Required validation

Run affected tests and applicable fast lint/type/style checks from the repository
root in the active development environment during implementation, after fixes,
and before committing or publishing. Choose coverage by changed behavior,
callers, shared dependencies and relevant failure paths, rather than test-file
names alone. Add regression coverage when behavior changes. For example, a CI
workflow change affects `tests/tooling`; a service change also needs its callers
and adapter/isolation coverage. Run `git diff --check` and check the branch diff
against `origin/main` before publishing. Resolve affected failures before
committing that round.

Publish after those local checks and begin Codex/Gemini reviews while full-suite
CI runs independently. Continue processing feedback during pending CI. A review
fix gets affected local checks and a fresh CI run for the new revision. Full
local runs are exceptional diagnostics for CI failures, global fixtures/test
infrastructure or especially broad changes that affected checks cannot cover;
they are not mandatory before PR creation or at review completion.

### Authoritative CI and required check

[Repository CI](../.github/workflows/ci.yml) consolidates the former PostgreSQL
and commercial-foundation workflows. Its stable merge-gate check name is
**`Full test suite`**. It requires success from all required partitions (seven runner jobs):

- **Python suite (Linux + PostgreSQL)** installs `requirements-dev.txt` with
  Python 3.12, runs `ruff check .` and `python -m pytest -ra` over every test
  discovered by `pytest.ini`, and supplies disposable PostgreSQL 18.6 through
  `TABULA_RASA_TEST_DATABASE_URL`. Missing/inaccessible PostgreSQL must not be
  used to make this gate green. There are no marker filters or deselections.
  It also builds the client-only NAS backup image and runs its isolated synthetic
  TLS/encryption/restore drill; see [NAS backup operations](operations/NAS_BACKUP.md#developer-validation).
- **Windows environment setup** runs the Windows-only
  `tests/tooling/test_codex_environment_setup.py` on Windows with PowerShell and
  Python 3.12. Those tests' existing platform skips on Linux are covered here,
  including the real Python 3.12 setup harness. The remaining suite runs once
  on Linux; no second full Python run is added on Windows.
- **Frontend, contracts and runtime image** owns Node 24/npm OpenAPI/client
  generation and drift checking, TypeScript/build, ESLint, Vitest, browser-independent
  HTTP/Git preview contracts and the commercial Docker image build/import inspection.
  The preview contracts run once without installing or launching browser engines.
- **Browser UI (shard 1/2 and 2/2)** partitions all Playwright UI/accessibility
  cases with Playwright's fully-parallel test sharding, two workers per runner and
  all four existing browser/device projects. Both shards are required; fail-fast
  is disabled to retain diagnostics from both. No browser/viewport or assertion
  is removed. Each shard uploads a uniquely named JUnit/trace/screenshot artifact.
- The three [native browser jobs](development/CI.md#required-execution-owners)
  partition every `tests/postgres/test*browser*.py` module. Each job independently
  builds the SPA and uses its own disposable PostgreSQL service, all three engines
  and required-browser mode. All three jobs are required and upload separate
  artifacts. The CI policy test verifies that every module appears exactly once,
  so new modules cannot be silently omitted.
  The complete Python suite still discovers every test;
  native scenarios skip there because that isolated checkout has no SPA build.
  Native tests execute once across these required shards. Python lint and
  non-browser integration contracts remain owned by the Python job.

Retained operator, source-validation and recovery coverage remains required in Python CI.
See [CI execution evidence and suite selection](development/CI.md) for the cost
baseline, coverage rationale, bounded budgets, focused commands and comparison
procedure. No path-based or scheduled-only selection is introduced.

The suite needs no production credentials, Google OAuth grants or live ledgers.
It uses read-only repository permissions, pip/npm dependency caches, bounded job
timeouts and PR-scoped cancellation of superseded runs. Python jobs print
failure/skip details and retain JUnit results as Actions artifacts. A failing,
cancelled, skipped or missing component cannot make the aggregate check pass.

PRs targeting `main` run on `opened`, `synchronize`, `reopened` and
`ready_for_review`, including drafts. Feature-branch pushes do not launch a
second push workflow. Ready/reopened events intentionally refresh the candidate;
concurrency cancels unfinished superseded runs for the same PR. Pushes to `main`
also run CI after merges. `merge_group: checks_requested` supports a future
merge queue. Checkout uses GitHub's current PR merge candidate (the default
`pull_request` checkout), or the merge-group SHA. Match the run to the current
PR head and candidate; a previous head's green result cannot establish readiness.

#### Optional GitHub enforcement (good to have)

Current-revision CI success and satisfied review conditions are required by
repository policy. Verify any GitHub-enforced branch protection or ruleset on
the current organization repository before relying on a server-side merge
block. Apply the repository's procedural gate while enforcement is unverified
or unavailable.

An authorized administrator can configure and verify enforcement in GitHub:

1. Confirm that the current organization's plan and the administrator's access
   support the intended protections for a private repository. Keep the
   repository private. Arrange an eligible plan only if the owner elects to
   pursue this optional improvement.
2. In **Settings > Branches > Add branch protection rule**, target exactly
   `main`. Enable **Require a pull request before merging**, **Require status
   checks to pass before merging**, and **Require branches to be up to date
   before merging**. Add **`Full test suite`**, selecting **GitHub Actions** as
   the expected source, and prevent admin/role bypass of the rule. Preserve any
   existing required reviews/checks and other protections. A ruleset equivalent
   must be active, target `main`, require the same check/source and strict update
   policy, and have no bypass that defeats the gate. Replace obsolete
   `integration`/`foundation` checks only after the aggregate check is available.
3. Read back the saved configuration. Use the endpoint that matches the
   protection mechanism:

   ```powershell
   gh api repos/heathcote-advisory/Project_Tabula_Rasa/branches/main/protection
   gh api 'repos/heathcote-advisory/Project_Tabula_Rasa/rulesets?includes_parents=true'
   gh api repos/heathcote-advisory/Project_Tabula_Rasa/rules/branches/main
   ```

   For classic protection, confirm `required_status_checks.strict` and the
   `Full test suite` context with the GitHub Actions app identity. For rulesets,
   inspect the active rule's details for the target, status-check source, strict
   policy and bypass list. Use the branch rules endpoint to check which rulesets
   apply to `main`, including inherited rules. A 404 from the classic endpoint
   can coexist with active rulesets. Treat unavailable readback or missing
   checks as unverified.
4. On a controlled synthetic PR, confirm CI and advisory review start without a
   full local run; handle feedback while CI is pending. Commit a deliberately
   failing test and verify the current candidate's gate fails and merging is
   blocked. Fix it with affected tests and a new commit; verify the older green
   result is insufficient while the new run is pending, and completion becomes
   eligible only after that run passes and review conditions are satisfied.
   Do not merge the probe or use production data. The server-enforcement probe
   is optional, requires separate authorization and does not block authoritative CI gate
   closure. Normal hosted CI/review behavior verification remains required.

### CI failures and review completion

Open the PR's **Checks > Full test suite > Details** or **Actions > Repository
CI**. Inspect the failed component's named step and download its JUnit artifact
when present. CLI equivalents are `gh pr checks <PR>`,
`gh run view <RUN_ID> --log-failed` and `gh run download <RUN_ID>`.
Reproduce the affected failure locally and fix it with scoped checks; a new
commit triggers fresh CI. Rerun only a diagnosed transient failure with
`gh run rerun <RUN_ID> --failed` (or Actions' **Re-run failed jobs**), on the
current candidate. If the aggregate job ran during the failure, ensure it is
also rerun after its dependencies succeed; use **Re-run all jobs** once if the
failed-jobs rerun does not recompute the aggregate. Do not loop on an unchanged
code failure or use reruns as evidence for a newer head.

Monitor CI and reviewer states independently. Review may finish before CI or
vice versa. Pending CI continues monitoring; failed/timed-out/cancelled/missing
CI is an actionable incomplete or blocked state, never success. Report the
current head, run/check name and failing job/logs, distinguishing code defects
from infrastructure or account limitations. Missing checks require inspecting
event/base-branch routing, Actions permissions/approval and required-check setup;
do not weaken the gate. Successful review-loop completion and merge readiness
require satisfied review conditions, current-revision required checks passing
and a conflict-free/current candidate. Human approval and merge authority remain
unchanged.

Tests use fake or in-memory services and must not read live ledger data,
application credentials, service-account keys, or OAuth tokens.

## Browser smoke test

Use the React Playwright and native HTTP/PostgreSQL browser checks described in
[CI ownership](development/CI.md). The Streamlit bookkeeping viewport fixtures and
smoke scripts are retired. Retained local administration uses focused AppTest
coverage for login, reauthentication, loopback denial, Google setup and profile
isolation; it never runs bookkeeping generation on startup.


## Live read-only validation

Synthetic tests require no live Google credentials or ledgers. Separately authorized
operator source/recovery validation may use `scripts/validate_google_sheets.py --profile
PROFILE_KEY`; it does not run a dashboard. A selected current source must satisfy the
[frozen format-13 contract](SHEETS_SOURCE_CONTRACT.md). Never use production data to
validate this source cleanup.

## Test architecture

Tests are organized by responsibility rather than by a permanent inventory of
every module:

- normal pytest unit and integration tests cover domain logic, storage,
  authentication, imports, recovery, and page behavior;
- focused Streamlit AppTest scenarios cover retained local administration;
- tests/sheets_fixtures.py seeds synthetic format-13 rows directly for retained
  backup/receipt recovery and read-only validation checks;
- tests/conftest.py provides reusable pytest fixtures for shared concepts;
- tests/drive_fixtures.py provides reusable semantic Google Drive builders;
- tests/tooling/ contains repository-policy, environment-bootstrap, and Git-hook
  checks; these remain part of the full suite and can be run with
  `python -m pytest tests/tooling`;
- Drive, receipt, backup, storage, and profile tests exercise their respective
  failure, retry, and isolation boundaries; and
- Browser viewport checks use React Playwright and native HTTP/PostgreSQL scenarios.

Do not duplicate shared scenario setup in individual test documentation.
Behavior-sensitive changes need success and relevant failure coverage, and
profile-sensitive changes need a two-profile isolation check.

`tests/test_application_boundaries.py` enforces the service boundary: ordinary
application modules cannot import the Sheets facade or infrastructure adapters.
Retained local operator exceptions are explicit. Provider-specific exceptions and
remaining migration boundaries are listed in [Architecture](ARCHITECTURE.md).
Add a new service/repository capability for bookkeeping behavior rather than
passing workbook administration into a browser request.

## Pull requests and review

Codex-authored PRs should use the PR body template in the
`gh-publish-review-loop` skill. Contributors without that skill should include
the same information directly: intent, expected success and failure behavior,
invariants that must not change, validation evidence, and known limitations.
Concise statements are sufficient. The description is review context, not
proof of correctness. Check the implementation against the current code,
tests, canonical documentation, callers, storage paths, retries, and state
transitions.

Codex is the primary repository/remediation reviewer and Gemini is an
independent complementary advisory reviewer. The human maintainer retains
requirements, approval, and merge authority. Follow AGENTS.md for
repository-wide rules and .gemini/styleguide.md for Gemini's specialized
adversarial review focus.

## Documentation maintenance

The root README is the documentation index. Keep one canonical home for each
durable fact and link to it rather than copying the full content elsewhere.
Update the relevant guide when externally visible behavior, configuration,
recovery procedures, or security boundaries change. Keep Mermaid source in
Markdown, use repository-relative links, and never commit credentials, tokens,
private ledger data, receipts, backups, or validation output.

## Commercial browser quality

The [bounded technical-alpha browser gate](browser/TESTING.md) adds
axe scans, desktop/mobile journeys and keyboard/failure checks to the existing
Playwright baseline. Repository CI runs it in the required native-browser job after
compiling the SPA; the guide owns local setup and outstanding release evidence.

## Operational telemetry

[Operational observability](operations/OBSERVABILITY.md) owns the sanitized telemetry
contract, optional collection setup, tier/retention/access checkpoint, alert thresholds,
staging smoke and platform operations integration responsibilities.

## Admission validation

`tests/postgres/test_beta_admission.py` covers synthetic real-database capacity,
identity, eligibility, expiry/replay, pauses and privileged API behavior. Native
beta desktop/mobile/accessibility scenarios extend `test_workspace_browser.py`.
Existing lifecycle fixtures explicitly mark approved internal use; historical
migration fixtures seed prior records directly rather than running current
workflow code on an old schema. [Admission operations](operations/BETA_ADMISSION.md)
owns the supported control contract and separate external-release gate.
