# Visual review and accessibility evidence

Use this guide to capture comparable screenshots, inspect intended visual changes
and record manual accessibility observations. [UI components](UI_COMPONENTS.md)
owns shared presentation; [browser testing](TESTING.md) owns functional coverage.
Functional success, visual review, manual accessibility and release approval are
separate results. Store revision-specific outcomes in the owning issue or PR.

## Repeatable evidence and retention

From the worktree root, with pinned Node 24 dependencies installed:

```powershell
npm --prefix web ci
npm --prefix web run build
npm --prefix web run lint
npm --prefix web test
$env:TABULA_RASA_SMOKE_PORT = '5191' # unused loopback port
npm --prefix web run test:e2e -- --workers=1 --output=test-results/visual-review
git diff --check
git diff origin/main --stat
```

The existing Playwright projects cover Chromium desktop/mobile emulation,
Firefox and WebKit. The suites retain PNGs and `revision.json` for each test,
including failures. Each screenshot identifies its route, actual CSS viewport,
browser engine/version, device scale, capture time, reflow scenario and PNG
SHA-256. Schema 2 manifests record actual Git HEAD, public-source worktree
changes, platform, test
result/retry and a hash of public application/harness sources. It never substitutes
an operator-supplied revision for the observed HEAD. Public-source status uses
the same allowlist as hashing and excludes unrelated receipt/import filenames.
Its explicit scope does not attest repository-wide cleanliness; older schema 1
status was repository-wide. A dirty source worktree is explicitly
development evidence; rerun from the clean candidate for exact-commit sign-off.

720px and 320px exercise effective layout widths equivalent to 1440px at 200%
and 1280px at 400%. These are CSS reflow checks, **not actual browser zoom**.
Real zoom, physical-device touch and manual accessibility remain separate below.
Native API/PostgreSQL suites retain functional/failure/isolation ownership;
intercepted screenshots do not replace those tests.

Keep before and after outputs in different ignored directories. Capture the
untouched baseline before editing and retain its own revision metadata. When overlaying a newer capture harness onto a baseline checkout, record
the harness changes explicitly and verify the application source is untouched.
Compare matching scenarios, engines and widths. Keep both packages when sharing
the result; older manifests must not be relabeled as new-format evidence.

Repository CI uploads per-shard UI evidence in **browser-ui-1** and
**browser-ui-2**, and native evidence in the
[three native results artifacts](../development/CI.md#required-execution-owners),
even on failure. On publication, attach the
sanitized local before/after package or link
its retained artifact alongside the current CI candidate; local ignored files
alone are not a durable hosted handoff. Keep generated validation output out of
Git. Retain the accepted evidence before Actions artifacts expire.

### Intentional screenshot change review

1. Identify baseline/candidate revisions, application/harness changes, platform,
   browser version, device scale, viewport and route. Compare matching captures;
   different engines/fonts/scale are separate observations.
2. Inspect Dashboard/Reports, ledger/selected filters, transaction/journal forms,
   mileage and receipt/recovery at desktop and both narrow widths. Review typography,
   primary/secondary actions, aligned fields and financial columns, ordinary row
   weight, long labels, empty/loading/error feedback and local table overflow.
3. Record every changed appearance as an intended design/user decision or a
   defect with scope, owner and disposition. Functional/axe success is supporting
   evidence, not visual approval. Do not use `--update-snapshots` to accept changes:
   these captures are evidence, with no blindly replaceable golden baseline.
4. Fix material defects, rerun affected checks/captures, and obtain manual
   re-review. Link accepted exclusions individually. Replaced or superseded
   evidence cannot establish current-candidate acceptance.

## Manual review preview and record

```powershell
npm --prefix web run dev -- --config vite.visual-review.config.ts --host 127.0.0.1 --port 5192 --strictPort
```

For phone review on the same trusted network, explicitly use `--host 0.0.0.0`
and the machine's LAN address. This is a local synthetic preview, not a deployed
environment. The explicit preview Vite configuration and adapter reuses existing test responses, disables the
real API proxy and terminates all API requests. Only the exact non-mutating mixed-use copy-draft and allocation POSTs are
admitted alongside supported reads. Writes are rejected, and file/auth
requests never reach a provider. Draft, navigation and error interactions work;
it does not implement persistence, receipt bytes, successful saves or complete
filter-backend behavior. Restart ordinary Vite without the preview config when finished.

| Review item | Required recorded observation |
| --- | --- |
| Candidate and reviewer | Actual SHA plus dirty/source hash if applicable, tester/date, route list |
| Desktop | OS/browser/version, 100%/200%/400% browser zoom; labels/actions remain reachable, no overlap or page overflow |
| Keyboard | Tab/Shift+Tab order, visible focus, native listboxes, table scrolling, selected routes; keep/Escape/opener restoration and successful-navigation focus |
| Phone/device | Actual device/OS/browser/version, portrait/landscape, touch menus/selection/scrolling and pinch zoom; emulation is not device evidence |
| Contrast and state | Manual reading of essential/secondary/disabled/error text and focus/selected indicators alongside automated axe; state is understandable without color |
| Result | Pass, defect with owner, or explicit accepted exclusion; observations that were not made stay pending |

## Evidence and independent release requirements

Keep candidate-specific visual, keyboard, contrast, reflow, device and real-zoom
observations in the owning issue or PR. Historical acceptance is retained in the
[integrated review record](https://github.com/Roklion/Project_Tabula_Rasa/issues/331)
and [implementation PR](https://github.com/Roklion/Project_Tabula_Rasa/pull/371). Those
observations apply only to their recorded revisions and scope.

The current contract is [design.md](../../design.md) and [UI
components](UI_COMPONENTS.md). Every newly delivered surface requires its own
selected-release review. [Hosted identity rollout](../identity/HOSTED_ROLLOUT.md) and
operational recovery supply separate provider-backed evidence. Screenshots and automated
checks alone establish neither comprehensive accessibility nor external admission or
commercial recovery approval. Record untested observations as pending; never invent
device/browser metadata or equate emulation with touch or actual browser zoom.
