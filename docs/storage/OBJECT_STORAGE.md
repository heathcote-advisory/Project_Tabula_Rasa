# Optional object storage (deferred)

This guide describes deferred S3 adapter groundwork.
**Drive remains the active provider; normal operation requires no S3 setup.**
Follow [Drive storage and recovery](RECEIPT_RECOVERY.md) for the current path.
[Provider adoption review](https://github.com/Roklion/Project_Tabula_Rasa/issues/382)
tracks the separate commercial decision.
The remaining instructions apply only after separate provider/target approval;
they are not deployed acceptance or permission to incur charges.

The optional adapter also needs `pip install -r requirements-object-storage.txt`
in a separately approved runtime image/environment. Normal Drive API dependencies
do not install Boto3. Keep S3 credentials/configuration absent until adoption.

## Current behavior and authority

`documents` owns canonical identity, integrity metadata and the current provider
location; `document_links` owns bookkeeping relationships. S3 locations pin an
exact provider version. `document_locations` retains original locators after an
explicit promotion. Full DB backups retain these; the v1 26-collection export
contains current locations, not historical locators, credentials or receipt bytes.

The existing same-origin API bounds incoming streams to 20 MB per file. The SDK
receives a file-like upload and downloads use bounded reads with exact size/hash
verification. Browser DTOs contain canonical IDs, never credentials or signed
bearer links. Legacy-shaped PostgreSQL transaction receipt projections use
canonical IDs for S3 and preserve Drive IDs for Drive. Unlink is reference-only.

Every named-user byte operation checks fresh server authorization and external
connections membership/capabilities before selecting an encrypted
environment/org/entity/provider-bound grant. An explicit S3 connection wins;
missing/revoked/disconnected S3 never falls back to Drive. Existing Google
connect/disconnect UI continues to manage Google only. Managed setup/recovery is
operator-only. Migrated-profile authority checks retain the original approved Drive
scope; byte migration neither changes bookkeeping authority nor rewrites cutover
evidence. Originals and Google grants remain.

## Manual provisioning and approval checkpoint

Before creating accounts/buckets/credentials, incurring charges, changing IAM or
production, copying live data or running the real staging drill, obtain approval
for the exact environment, account/region, buckets/prefixes, entities, credentials,
cost/retention limits and maintenance window. Code merge/fake tests are not this
approval. Keep private manifests/decisions outside Git; publish safe counts/status
and evidence hashes only.

1. Inspect effective Render region, actual AWS account usage/storage/quotas,
   version-byte growth, transfer costs, availability and alert thresholds. Use
   the ADR budget assumptions and current regional calculator rates. For retained
   Drive inspect Google storage headroom, project quota regime, grant lifecycle
   and shared-account/client blast radius. These effective settings are unverified.
2. Provision separate staging and production **buckets and credentials**. Use
   separate random roots `staging/<32 lowercase hex>` and
   `production/<32 lowercase hex>`. Isolated recovery uses another staging
   bucket/prefix/credential. Stage separation must include credentials, not just
   prefixes. No production write credential in staging.
3. Enable all four S3 Block Public Access flags, bucket-owner-enforced ownership,
   versioning and default SSE-S3/AES256. Require TLS in bucket policy. Activation
   preflight checks public-access blocks and versioning. PUT explicitly requests
   AES256; every location requires a non-null version. Do not suspend versioning.
4. Runtime IAM: `s3:ListBucket` restricted by `s3:prefix` to the exact
   namespace/entity-hash prefix; `s3:GetObject`, `s3:GetObjectVersion`,
   `s3:PutObject` on that prefix only; `s3:GetBucketVersioning` and
   `s3:GetBucketPublicAccessBlock` on its bucket. Enforce conditional PUT
   `s3:if-none-match = *` in bucket policy. No ACL/policy/lifecycle management,
   unrestricted enumeration or `s3:DeleteObjectVersion`. Ordinary runtime needs
   no delete permission. A separately approved drill operator may create delete
   markers for exact synthetic targets, never delete versions. See
   [AWS conditional-write policies](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html).
5. No S3 CORS/public domain is needed: browser traffic stays on the authenticated
   API. Server AWS requests are signed; forged/expired provider URLs are not an
   access path. Inject existing `TABULA_RASA_CONNECTION_SECRETS_DIR` and
   `TABULA_RASA_CONNECTION_SECRETS_KEY` through approved secret custody. Keep
   credential payloads and keys outside Git/logs; disable SDK HTTP/debug logging.
   Rotate with a new encrypted envelope for the same binding, verify privately,
   then separately revoke the old credential. Keep original Drive grants.
6. Inspect/approve effective lifecycle/version retention. Initial policy is no
   automatic expiry/prune of versions, markers, orphans or original Drive files.
   Object Lock, replication and independent byte archives require separate
   approval. external release and customer lifecycle and audit retention policy own customer deletion policy; NAS backup and restore's indefinite SQL NAS
   retention is unchanged. No irreversible cleanup without approved exact targets.

## Private binding and empty-entity installation

Run from repository root in the API operations environment. Apply forward schema
revision `0014_document_locations` normally; it preserves the canonical v1 export
inventory. Prepare a private JSON file outside Git with these exact fields
(placeholder values describe the schema):

```json
{
  "environment": "staging",
  "organization_id": "approved-organization",
  "entity_id": "approved-entity",
  "provider": "s3",
  "profile_key": "approved_profile",
  "scope_key": "s3:us-east-1:approved-private-bucket:staging/<namespace>/<entity-sha256>/",
  "s3": {
    "bucket": "approved-private-bucket",
    "region": "us-east-1",
    "namespace": "staging/<32-lowercase-hex>",
    "access_key_id": "private-server-credential",
    "secret_access_key": "private-server-secret"
  }
}
```

Derive `scope_key` from the configured adapter's property; the entity hash is
SHA-256 of the exact UTF-8 entity ID. Configuration is server-only. Supply the
existing private `TABULA_RASA_DATABASE_URL`/TLS and encrypted-grant settings.

```powershell
python -m scripts.manage_document_storage bind --environment staging --input C:\private\managed.json
```

Dry-run returns a plan hash and safe counts. Review the exact private binding,
target DB/entity, provider preflight and costs. `bind` refuses nonempty receipt
metadata.

Every plan also binds the actual database endpoint/database fingerprint. An
otherwise identical snapshot in another database cannot reuse that approval.

After exact operator approval:

```powershell
python -m scripts.manage_document_storage bind --environment staging --input C:\private\managed.json --apply --expect-plan <reviewed-plan-sha256> --approval-sha256 <private-decision-sha256>
```

Revision defaults to zero for new managed connections; use the current managed
row revision for an approved existing binding. Ordinary bucket rebinding is
refused. CLI access is privileged server/operator access, never an HTTP endpoint.
It creates no accounts, buckets, credentials, Google grants or authority. There
is no ambient AWS credential discovery or provider fallback.

For credential rotation use `rotate` with a new private payload, the **same**
installed scope and current revision. Dry-run verifies all retained bytes with
the new credential; approved apply replaces only the encrypted secret reference.
Verify approved staging write access separately before revoking the old key.
Changing a nonempty entity's scope requires copy/restore rather than rotation.

## Optional Drive migration and restart

1. Obtain the user/operator's explicit election to migrate this entity. Record
   exact source folder/grant, target, DB binding and failure/maintenance decision.
   Freeze bookkeeping/job/receipt writers. Preserve workbook, receipts and grants.
2. Reconcile source inventory with canonical metadata, including unlinked rows.
   Missing bytes, unverified integrity and unregistered provider files block copy.
   For an explicitly accepted orphan, freshly verify its reference/bytes and
   register it via existing `PostgresReceiptRepository.register_document` before
   re-planning. Historical integrity backfill remains explicit tooling; never
   invent checksums or delete files to pass.
3. Run `copy` with the target binding and the same arguments as `bind`. Dry-run
   verifies every canonical source receipt without uploading or changing SQL.
   It uses the connected entity-owned external connections Drive grant, never another profile.
4. After reviewing the plan, repeat with exact apply/approval arguments. Stable
   IDs conditionally create/reuse targets. Size, checksum, exact bytes and full
   filename/type/identity metadata are verified. One SQL transaction compares
   fresh canonical metadata, retains originals in `document_locations`, switches
   all locations and installs the S3 connection. Installation checks source
   connection revision/org/scope. Document IDs, link rows and financial history
   stay intact. Encryption-envelope creation before SQL may leave inert ciphertext
   on failure; uploaded bytes remain available for inspection.
5. Before promotion, failure leaves Drive authoritative. Restart reuses stable
   destination identities. A changed plan/metadata/grant requires re-planning.
   After an uncertain promotion result, repeat **dry-run**: an already-managed
   exact binding is verified without recopying. Do not apply a stale successful
   plan. Successful API responses are authoritative; failed PUTs receive one
   readback and at most one retry only after confirmed absence.
6. Run `verify` with the target binding, restart/rebind the intended runtime and
   exercise upload/download/link/unlink/relink and entity denial before releasing
   maintenance. Original retirement needs separate explicit approval. SQL-link
   failures keep uploaded receipts recoverable; unlink never deletes bytes.

## Byte protection and isolated recovery

DB snapshots/PITR (managed database recovery), SQL NAS archives (NAS backup and restore)
and bookkeeping exports do not contain receipt bytes. Version pins allow the older DB
point to retrieve exact versions after later writes or delete markers. Missing/expired
versions, revoked access, wrong provider/scope and checksum/size mismatches block
verification. Never silently substitute latest bytes, another profile or a successful
status.

Versioning is not independent protection against privileged version deletion, account
loss or lifecycle expiry. Retain approved independent byte copies and private
canonical-ID/location/version/size/hash manifests before promising that recovery.
Explicit verified isolated copies are implemented. Scheduled/off-account/ NAS byte
archives and monitoring remain **pending**, separate from NAS backup and restore SQL
backup scheduling/indefinite retention. Preserve orphan files on uncertainty.

1. Confirm the authorized primary and alternate recovery operators in the
   private handoff using the [operator handoff procedure](../operations/INCIDENT_RESPONSE.md#ownership-severity-and-alert-intake).
   Approve the exact source/target, costs, grants, recovery keys and retention.
   Select a retained metadata/byte point; measure actual loss/time against the
   stage's approved RPO/RTO. Plan pricing and fake tests supply no recovery guarantee.
2. Restore DB to an isolated target with writers disabled using
   [the canonical DB recovery guide](../operations/DATABASE_RECOVERY.md). Verify the expected
   complete export, schema/audit point and actual isolation **before** location
   promotion. Block production callbacks/browser access; activate no authority.
3. Supply read-only source S3 credentials for retained versions and a different
   staging destination binding/credential for the same canonical entity/org:

```powershell
python -m scripts.manage_document_storage restore --environment staging --input C:\private\recovery-target.json --source-input C:\private\retained-source.json --source-database-sha256 <recorded-source-fingerprint> --target-database-sha256 <isolated-target-fingerprint> --revision <restored-managed-revision>
```

Target fingerprint must match the DB and differ from source; production mode is
refused. Fingerprints check bindings, not restore lineage. Operator inspection
must establish actual isolation. Restored canonical version pins define the set;
newer source files absent from that point are intentionally excluded. Missing
pins block restore. Dry-run writes nothing. Reviewed plan/approval arguments
copy/verify bytes and atomically promote only the isolated DB's connection and
locations, retaining old pins. No source/version is automatically removed.

4. Run `verify` on the restored binding; prove IDs, links, metadata, exact bytes
   and two-entity isolation. Exercise approved synthetic delete-marker/version
   retrieval, missing-version/revoked-provider failure, partial-copy restart and
   stale-metadata refusal. Actual service promotion remains an operator decision.
5. Retain private manifests, effective provider/lifecycle/access/capacity settings,
   DB fingerprints, schema/release, checksums, observed durations/counts,
   discrepancies and human acceptance. Publish only sanitized status/counts and
   private evidence hashes. Keep isolated artifacts until exact cleanup approval.

## Validation and handoff

Automated coverage includes native PostgreSQL promotion/link/original retention,
named-user HTTP bytes, foreign/forged access, logout/disconnect, type/size/hash/
version loss, bounded PUT uncertainty, partial restart and a real isolated
synthetic DB snapshot clone plus byte restore with fake S3 boundaries. Boto3
Stubber validates the actual SDK model without live credentials.

The opt-in real-provider harness runs the same two-entity DB/byte drill with real
S3. Provision approved synthetic-only source/target staging scopes for `alpha`
and `beta`, with source `DeleteObject` permission for an approved delete marker,
never version deletion. Private JSON contains exactly `source` and `target`, each
with the five `s3` configuration fields above. No real customer scopes:

```powershell
$env:TABULA_RASA_S3_STAGING_BINDING = 'C:\private\synthetic-s3-drill.json'
$env:TABULA_RASA_ALLOW_S3_STAGING_DRILL = 'approved'
# Supply TABULA_RASA_TEST_DATABASE_URL for a disposable DB, never the application DB.

python -m pytest tests/postgres/test_s3_staging.py -q -s
```

It retains object versions and prints safe two-entity isolated-recovery status.
Run from the approved hosted staging environment to prove Render-to-provider
behavior; a local provider run alone does not prove hosted binding. The harness
does not prove managed DB PITR, independent archive protection, continuous RPO,
full-service RTO or alternate-operator handoff. Default tests skip it pending
manual provisioning/approval.

Actual provider selection, approved setup and hosted verification status belong to
[Provider adoption review record](https://github.com/Roklion/Project_Tabula_Rasa/issues/382), with [operational readiness
verification](../operations/AUDIT_RECOVERY.md) collecting stage-specific recovery
evidence. Keep AWS quota/access/lifecycle and exact-byte versioned recovery evidence
there if this optional provider is adopted; unused S3 setup is not a blocker to the
selected Drive scope. [live owner cutover](../accounting/CUTOVER.md) retains live owner
bindings/execution, while external release and customer lifecycle and commercial
recovery agreement retain customer/commercial approval. This guide defines the optional
procedure, not a duplicate list of currently pending tasks.
