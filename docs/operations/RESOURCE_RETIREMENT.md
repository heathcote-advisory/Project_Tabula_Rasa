# Resource inventory and approved retirement

This runbook covers one-time resource inventory, exact-target previews and
separately approved retirement. It provides no automatic purge or blanket cleanup
authority. Code retirement, operational replacement evidence, customer retention
and commercial activation remain separate decisions.

## Inventory and retention policy

Keep dated resource inventories, approvals and execution/postcheck reports in
the [resource disposition discussion](https://github.com/Roklion/Project_Tabula_Rasa/issues/339).
Those reports are historical observations, not current provider state. Before
any operation, build a fresh private inventory with exact identities, authority,
owners, references, recovery obligations and preservation evidence. Unresolved
resources remain retained; issue closure supplies no deletion consent. Owner-reported
housekeeping is distinct from verified target-by-target postchecks. Each later target
set requires its own current preview and exact approval.

Preserve frozen Sheets sources, last verified legacy snapshots, complete exports,
checksums, receipts/document bytes, audit history, production/staging/UAT, recovery
evidence and deployed forward Alembic history. frozen Sheets source contract retired
format compatibility; it did not authorize deleting files or records. NAS backup and
restore retains every successful NAS generation indefinitely, including mislabeled and
drill generations. Capacity pressure triggers an operator decision, never implicit
pruning. The tool refuses NAS generations, Sheets workbooks and schema history even if
mislabeled disposable.

A future retention proposal must state exact scope, current obligations, remaining
verified recovery points, legal/hold/customer effects, new duration, custody, capacity
and recovery risks. Obtain separate platform operations policy and external release and
customer lifecycle and commercial recovery agreement decisions, update canonical
runbooks and tooling, then seek exact-target deletion approval. This procedure does not
change the existing Sheets/Drive backup policies in [Backup and
recovery](../BACKUP_RECOVERY.md), or authorize using them to purge a protected final
source/last snapshot.

## Prepare an operator inventory

Work outside Git in protected operator storage. Build an inventory using the schema
below and inspect
the applicable provider consoles/read-only APIs, NAS and configuration for the
**selected stage**: `technical_alpha`, `owner_operated`, `external_beta` or
`commercial`. Record that stage explicitly; an inventory asserts no live authority
switch. No whole-program completion is required to begin inventory.

1. Enumerate exact Neon project/branch/database/endpoint IDs, parent/child
   branches, managed snapshots and restore state; distinguish Production, UAT,
   development, shared test and isolated recovery targets. Display names alone
   are insufficient. Enumerate local/CI fixture databases through their existing
   harness, rather than widening its teardown privileges.
2. Inspect Render bindings, GitHub environment/workflow references and secret
   **names**, deploy/CLI/operator scripts, scheduled jobs, test callers and
   retained migration rehearsals. Inspect effective bindings privately; never
   dump environment variables or secret values. Inventory every endpoint and
   dependent DB/file/binding affected by a provider's parent deletion.
3. Reconcile protected NAS complete/incomplete/quarantined generations, Sheets
   source profiles, Drive folders/backups/receipts, historical artifacts and
   private exports/manifests/oracles with managed database recovery, NAS backup and restore, receipt byte recovery and operational readiness verification evidence. Record
   existence, custody, retention reason and evidence handle, never ledger rows,
   document names/bytes, private exports or credentials.
4. Obtain the environment owner's disposition: retained source/last verified
   snapshot (`retained_source`); active or recoverable target
   (`active_recoverable`); required drill evidence (`required_evidence`);
   disposable synthetic target (`disposable_synthetic`); or unresolved/needs
   approval (`unresolved`). A completed migration or closed ticket cannot supply
   disposition. Keep deferred resources unresolved or retained with a named
   owner and re-review condition in the protected operator record.
5. For each candidate, confirm synthetic-only contents and no active, CLI, CI,
   staging, rollback, audit/retention or recovery consumer. Obtain the recovery and retained-resource owners’
   owner/evidence reference for this exact capability; operational readiness verification must establish the
   selected stage's replacement recovery guarantee where relevant. Database
   dumps do not protect document bytes. Link the accepted evidence with `handoff`.

Use only reviewed safe handles in the JSON. Its strict schema rejects unknown
fields, URLs, paths, duplicate keys/IDs/identities and dangling resource edges.
Handles can still encode sensitive information: manual sanitation is required.
Use opaque references to private evidence, not personal/business names. Untrusted
input is rejected before any contents are printed; fixed error codes omit paths
and raw parser/provider errors.

Every resource has a local `id`, `kind`, exact `scope` (provider project/container),
`provider_id`, `environment`, `owner`, `disposition`, `authority`, `protections`,
`references`, `depends_on`, `last_backup`, `handoff`, `rollback`, `evidence` and
`effects`. A family row with a null provider ID is an inventory gap; split it into
exact rows before proposing disposal. `last_backup` links a verified preserved
backup; for reproducible empty fixtures it may link a reviewed recreation/absence
record. `rollback` links the tested recovery/recreation procedure.

`references` lists live/retained consumer handles; any entry blocks disposal.
`depends_on` lists resource IDs consumed by this resource (e.g. a database depends
on its parent branch). `effects` lists **every exact resource affected by deletion**,
including itself. An empty list means unreviewed effects. Add affected endpoints,
children, branch-local roles, files and config references as separate inventory rows; no cascaded
effect may be silently omitted. All effects must be selected and independently
eligible, and any consumer outside the set blocks the operation. Do not clear
references without inspecting/removing obsolete bindings through their normal
reviewed workflow and then rechecking effective configuration.

The top-level `observed_at` is the UTC instant when the whole scoped inventory
and its references/effects were inspected. Set `coverage: operator_verified`
only after complete enumeration of those dependencies and owner review. This is
an attestation, not automatic cloud discovery or authorization. Missing resources
cannot be detected by an offline tool; retain `unverified` if access is incomplete.

## Retire legacy provider access and bindings

Use [provider custody and transfer](SECRETS.md#provider-account-custody-and-transfer)
before retiring a legacy provider account, access grant, credential, registry
package or service binding. The inventory preview below covers its listed resource
kinds. Review account, access and package retirement separately in the authorized
provider consoles.

1. Verify business administrators and independent recovery access to the new
   provider accounts. Inspect the old and replacement bindings, including
   federated sign-in and OAuth clients or grants, billing and subscriptions,
   notification and backup routing, jobs, release automation and retained
   recovery procedures. Check who can administer, pay for and recover each
   retained resource after the old identity leaves.
2. Transfer or securely export retained assets and records into approved custody.
   Verify they are accessible and meet retention, privacy and hold obligations.
   For registry changes, follow the [registry procedure](DEPLOYMENT.md#registry-ownership-changes)
   and verify each affected environment's live binding, release path and pullable
   compatible rollback digests. Test operation and recovery without using the old
   identity. Retain the old resource while any dependency, retention obligation
   or recovery check remains unresolved.
3. Prefer a reversible archive or quarantine where the provider supports it.
   Obtain separate approval for each exact retirement and its effects. Delete an
   account only after clearing its dependencies and retention obligations and
   obtaining explicit approval for that account and any cascaded effects.
   Record irreversible effects and preserve a reviewed return path where feasible.
4. Check current state immediately before the approved action. Verify the result
   and recheck retained service health, access, release bindings, backups and
   recovery. Stop on uncertainty or regression and follow [incident response](INCIDENT_RESPONSE.md).
   Use the reviewed return path only while it is safe and available.

Record the approved scope, observed results and remaining dependencies in the
[platform ownership record](https://github.com/heathcote-advisory/Project_Tabula_Rasa/issues/413).
Keep credentials, private inventory and recovery details in protected storage.

## Inventory JSON schema

The top-level object has exactly five required keys: `version` (integer `1`),
`stage` (one of the four stages above), `observed_at` (ISO-8601 UTC timestamp),
`coverage` (`unverified` or `operator_verified`) and `resources` (a nonempty array).
Every resource requires all fields listed above; extra fields are rejected.

| Field | Accepted values |
| --- | --- |
| `kind` | `postgres_branch`, `postgres_database`, `postgres_endpoint`, `postgres_role`, `managed_snapshot`, `nas_generation`, `sheets_workbook`, `drive_artifact`, `local_artifact`, `deployment_binding`, `fixture`, `schema_history` |
| `environment` | `unknown`, `development`, `test`, `uat`, `staging`, `production`, `archive` |
| `disposition` | `retained_source`, `active_recoverable`, `required_evidence`, `disposable_synthetic`, `unresolved` |
| `authority` | `unknown`, `active`, `inactive`, `not_applicable` |
| `protections` | Array containing any of `source`, `live`, `recovery`, `drill_evidence`, `legacy_snapshot`, `nas_indefinite`, `document_bytes`, `schema_history`, `customer_retention` |

Handles use 1–160 ASCII letters, digits, underscores, periods or hyphens and start
with a letter or digit. `id`, `scope` and `owner` require handles; `provider_id`,
`last_backup`, `handoff`, `rollback` and `evidence` accept handles or JSON `null`.
`references`, `depends_on` and `effects` are arrays of unique handles, as is
`protections`. Resource IDs and scoped provider identities must be unique;
`depends_on` and `effects` must reference resources in the same file, and a resource
cannot depend on itself. Null or unknown identities/evidence may describe a gap,
but cannot qualify a target for retirement.

This complete synthetic example illustrates the structure, not a provider inventory
or approval. It deliberately remains blocked because coverage is unverified.
Replace the timestamp and facts only after the required private inspection; never
promote these placeholders into observed evidence.

```json
{
  "version": 1,
  "stage": "owner_operated",
  "observed_at": "2026-01-01T00:00:00Z",
  "coverage": "unverified",
  "resources": [
    {
      "id": "synthetic-fixture",
      "kind": "fixture",
      "scope": "synthetic-test-scope",
      "provider_id": "synthetic-fixture-id",
      "environment": "test",
      "owner": "test-operator",
      "disposition": "disposable_synthetic",
      "authority": "inactive",
      "protections": [],
      "references": [],
      "depends_on": [],
      "last_backup": "private-recreation-record",
      "handoff": "private-owner-review",
      "rollback": "private-recreation-procedure",
      "evidence": "private-inspection-record",
      "effects": ["synthetic-fixture"]
    }
  ]
}
```

## Preview and manual approval

Prepare a fresh private `inventory.json` using the schema above; keep it outside
Git and replace `EXACT_RESOURCE_ID` with a reviewed inventory ID. From the repository
root, using the active Python development environment:

```powershell
python scripts/preview_resource_retirement.py --inventory inventory.json --target EXACT_RESOURCE_ID
```

Unverified or protected sets return exit 1, `blocked`, `applied: false`. For a
privately verified disposable set, repeat `--target` for **each** exact target. There is no
wildcard, default-all, execute, force or approval switch. Exit 2 is invalid input;
exit 0 means `requires_manual_approval`, never deletion authorization. All modes
only read the inventory; no provider/network/database/filesystem mutations occur.

The preview lists exact scoped identities, deletion effects, incoming consumers,
references, authority, last backup, recovery and retained-resource handoff, rollback/evidence and blockers.
It hashes the entire inventory and selected plan for the approval/audit record.
Changing any inventory fact changes the plan. Observation must be no older than
24 hours and not future-dated. That bound does not eliminate stale state: inspect
the actual provider/reference state again immediately before manual execution.

An authorized human operator must approve the concrete set **after** reading the
preview and evidence. In protected custody record:

| Approval field | Required content |
| --- | --- |
| Scope | Stage, owner, exact scoped provider IDs, complete cascade/effects set |
| Plan | Inventory and plan SHA-256, observation instant, reviewed evidence |
| Decision | Explicit delete approval for this set, human operator, UTC time and validity window |
| Preservation | Last backup/recreation evidence, retained live/source/evidence checks, recovery and retained-resource handoff |
| Authority | Current authority and retention/customer-hold review, separate policy decision if needed |
| Recovery | Recovery route and irreversible limitations; no promise that deleted provider IDs can be restored |
| Execution | Authorized executor, method, immediate preflight and planned post-checks |

Ticket creation, a passing preview, a checkbox, capability retirement inventory closure
or approval for another set never satisfies this approval. Keep blocked targets
retained; a generic confirmation cannot override a blocker.

## Execute only a separately approved set

1. Capture sanitized prechecks plus private independent verification baselines
   for retained production/staging/UAT: exact bindings/authority, health/schema,
   full relevant content/integrity and audit state, frozen source/export/checksum
   inventories, NAS generations and document bytes/links. Use existing
   [PostgreSQL recovery](DATABASE_RECOVERY.md), [audit recovery](AUDIT_RECOVERY.md),
   [NAS](NAS_BACKUP.md), [document recovery](../storage/RECEIPT_RECOVERY.md)
   and [cutover](../accounting/CUTOVER.md) tools in their authorized read-only modes.
   Preserve per-profile/entity identity; never compare only local IDs or rebind a
   verifier to a fallback environment. Do not put private manifests in GitHub.
2. Immediately re-read provider IDs, parents/cascades and effective references.
   Regenerate the preview if any fact changes; obtain fresh approval for its new
   hash. Stop on uncertainty, active references, retained consumers or unmet
   retention/recovery needs. Do not interrupt live systems to make them eligible.
3. With the approved provider's normal operator console, delete only the exact
   reviewed disposable set. No blanket SQL, schema reset, recursive artifact
   purge, branch-name pattern or automatic retry after an uncertain result.
   If status is uncertain, inspect read-only and stop for operator reconciliation.
   Existing database runtime test-harness teardown remains independently scoped to its own
   newly created fixtures; this procedure grants no extra harness authority.
4. Read back absence of exact approved targets and compare retained baselines.
   Verify unchanged live/staging/UAT bindings, authority, data/integrity/audit,
   document bytes/links, final source/exports/checksums, required recovery
   branches/snapshots/NAS generations and deployed migration files/revisions.
   Record each as observed pass, fail or unverified; commands/tests alone do not
   establish actual provider postconditions. Stop and invoke incident response
   on a mismatch. Do not claim success with missing postchecks.

For an inadvertent **config/reference removal**, suspend the cleanup and use
[deployment rollback](DEPLOYMENT.md) / [incident response](INCIDENT_RESPONSE.md)
to restore the exact reviewed config/secret-store binding and compatible image.
Recheck health, identity, authority, schema and authorized access before resuming.
Never print secret values or auto-activate writers. For a deleted DB/artifact,
configuration rollback cannot recover data: escalate to the existing isolated
restore/recreation runbook, preserve incident evidence and seek authorization
before any live rebinding. Keep migrations forward-only; never squash deployed
history or overwrite the retained source as a cleanup repair.

Retain a sanitized audit record with target/plan hashes, owner approval reference, UTC
start/end, exact operation/result, pre/post-check outcomes, retained inventory hashes,
recovery limitations and remaining owner/re-review conditions. Keep private results
separately protected. If no set is approved, record **retained; no deletion executed**
and leave actual-resource acceptance pending. operational readiness verification
consumes the evidence; external release and customer lifecycle and commercial recovery
agreement decide stage/release acceptance.

## Developer validation

```powershell
python -m pytest -q tests/tooling/test_resource_retirement.py
python -m ruff check tabula_rasa/deployment/resource_retirement.py scripts/preview_resource_retirement.py tests/tooling/test_resource_retirement.py
git diff --check
```

Synthetic tests cover eligible preview, retained/live/ambiguous targets, cascade
effects and consumers, two-scope isolation, stale inventory, malformed/unsafe
inputs, retention invariants, deterministic plan hashes and read-only CLI results.
They do not establish live inventory completeness, operator approval or successful
cleanup. Current-head CI remains the full-suite publication/merge gate.
