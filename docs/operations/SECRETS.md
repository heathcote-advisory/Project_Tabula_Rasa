# Secret custody, rotation and revocation

Use with [incident response](INCIDENT_RESPONSE.md). Commercial recovery
acceptance, release admission and privacy obligations remain separate decisions. No credential value,
encrypted grant, recovery key or private inventory belongs in Git, command
arguments, browser assets, logs, error text or issue/comment artifacts.

## Access and private inventory

The deployment owner is accountable for custody; before stage enablement name a
primary and independent alternate for each row below in a private inventory.
Record environment/resource alias, secret-manager reference (not value), purpose,
scope/privileges, dependent services/jobs, custodian/alternate, creation/last
rotation/review date, expiry if applicable, revocation method, recovery-access
reference and last verified exercise. Record absent/unused secrets explicitly.
Review before releases, operator departure, provider changes and incidents.
For named support grant provisioning/revocation and bounded session containment,
follow [Support operations](SUPPORT.md); support staff receive no SQL
or secret-store access.
Do not claim a working alternate merely because the inventory has a blank field.

| Secret/control | Least privilege and custody owner | Rotation/revocation boundary |
| --- | --- | --- |
| PostgreSQL runtime URL | DB owner; distinct environment DML role for app/job needs, no DDL/schema ownership/BYPASSRLS or direct audit mutation. Runtime receives no owner URL. | Change exact role credential in private provider/store and all consumers; terminate old connections if compromise requires immediate cutoff. Password change alone does not terminate a stolen live DB connection. |
| Migration URL | DB owner; separately held schema/function-owner credential, explicit bound maintenance/Actions migration step only. | Revoke/replace separately; remove from operator process after use. Never inject into Render runtime. |
| Audit inspector/recovery access | Recovery/security owner; separately provisioned inspector SELECT and target-scoped restore role, temporary elevation only where needed. | Revoke temporary grants/access after evidence acceptance; preserve audit/history and retained artifacts. |
| Render API key | Release owner; narrowest available service/workspace administration boundary, restrict custody independently from app runtime. | Replace in exact deployment environment, revoke old token at provider; verify old token refusal without recording its value. If provider scope is broader than one service, record that blast radius. |
| GHCR pull / GitHub automation | Release owner; Render uses an authorized `read:packages` credential for the [private commercial release package](DEPLOYMENT.md#registry-ownership-changes). GitHub Actions uses its short-lived `GITHUB_TOKEN` with declared permissions and package access. | Replace and verify the registry credential at each bound service before revoking the old credential. Human GitHub/provider administration uses separate MFA-protected accounts. Keep long-lived admin tokens out of the image. |
| OIDC client secret/configuration | Identity owner; environment-specific confidential ZITADEL client and exact issuer/callback; no browser secret. | Provider secret rotation plus durable app epoch rotation; resetting provider credentials alone leaves local sessions valid. |
| Session generation | Identity owner; independent random value of at least 32 characters per environment in runtime secret store. Cookies are opaque; there is no cookie signing key. | New generation plus compare-and-swap configuration epoch invalidates issued sessions and pending logins. Never restore an old generation to recover sessions. |
| Google OAuth grants / connection key and directory | Connection owner with affected business Owner approval; environment/entity-bound encrypted grants in private storage; operator mounts may be read-only, customer OAuth requires persistent writable shared custody; Fernet key held separately. Narrow existing scopes; ZITADEL tokens grant no Drive access. | Disconnect locally first, coordinate shared consent impact before provider revocation. Reconnect through configured [customer consent](../identity/GOOGLE_DRIVE.md) or retained operator provisioning; key rotation stages grants in a new directory/key coherently. Preserve old protected recovery material until separately approved disposition. |
| NAS DB backup service / age recovery key | NAS/recovery owner; source backup access only as required by canonical NAS runbook, protected service/password files; decrypt identity separately accessible to authorized recovery operators. | Replace source credential without changing archive retention. New encryption recipient protects future generations; retain keys needed for historical archives, verify old/new archive recovery privately. Compromised decrypt key does not become safe by re-encrypting only future backups. |
| Telemetry source token / scope key | Diagnostics owner; independent per-environment ingestion token, no collector administration at runtime; >=32-character HMAC scope key. | Replace token and matching endpoint together; revoke old source token. Scope-key rotation intentionally breaks pseudonym continuity. Check sanitized ingestion plus independent uptime, never forward raw NAS reports. |
| Restricted-alpha gate | Release owner, synthetic environment only; separate hash at Render and smoke password in authorized runner. | Generate hash via hidden `python -m tabula_rasa.http.alpha` prompt, update exact consumers and verify gate. Never introduce alpha credentials into named/production recovery. |
| Legacy app password / shared Google client / Maps quota credential | Internal deployment/local administrator; app password and OAuth client are intentionally deployment-wide, profile human grants remain separate, Maps quota monitoring remains separate. | Use existing loopback-only administrator with reauthentication and [Google setup](../GOOGLE_DRIVE.md). Review shared consumers before rotation; no per-profile password authorization is implied. |

Separate accounts and roles for diagnostics, release, recovery and billing where
the provider allows it. A role described as runtime must not own its tables or
functions. Confirm effective privileges rather than trusting a role name. Broad
provider/project scope, shared capacity and lack of finer provider controls are
private accepted-stage limitations; do not assert technical tenant isolation at
the provider administrator boundary. Commercial acceptance must evaluate them.

## Provider account custody and transfer

Use business-controlled provider accounts, organizations and workspaces for
production and shared operational resources. Prefer a supported transfer of
existing resources into business custody when it removes reliance on personal
accounts and preserves access, service and recovery. If transfer is unavailable
or unsafe, plan an approved migration or replacement. Document any development
or staging exception privately with its owner, scope, cost, isolation or risk
rationale, and review condition.

Provider custody covers administrative access, infrastructure, credentials,
billing and recovery. Application [tenant membership and Owner roles](../identity/WORKSPACES.md)
govern access to business data. Preserve customer identities, application roles
and [customer Google grants](../identity/GOOGLE_DRIVE.md) during provider transfers.
Use the applicable identity or connection procedure for separately approved changes
to those bindings.

Before an approved transfer, inventory the current and destination accounts,
resources and consumers. Record primary and independent alternate administrators,
effective permissions, MFA and recovery custody, billing responsibility, and
alert destinations. Verify each independently through authorized access and keep
unverified items open in the private handoff. For GitHub, check organization and
repository access, effective rules, Actions/environment permissions and package
access. For Render, check workspace administration, service access, image binding,
registry credentials and release access for each environment.

Keep the original access and recovery route available until the replacement is
verified. Follow the [registry procedure](DEPLOYMENT.md#registry-ownership-changes)
for package and service changes, and the [retirement procedure](RESOURCE_RETIREMENT.md#retire-legacy-provider-access-and-bindings)
before removing legacy access or bindings. Keep dated transfer decisions, evidence
and gaps in the [platform ownership record](https://github.com/heathcote-advisory/Project_Tabula_Rasa/issues/413).

## Break-glass access

Arrange an independently controlled provider/secret-store administrator with MFA
and protected offline recovery factors before launch. Access must survive primary
operator absence or OIDC outage. Record who can authorize use, how the alternate
retrieves access, exact scope/time limit, and where access events are retained.
Test access without exposing factors or touching live ledger data.

When invoked, declare the incident and authorization privately, restrict ingress,
use the least privilege temporary role, record fixed actions/times, and have the
alternate/owner review results. Revoke temporary grants and rotate any exposed
material after recovery, verify normal access, and record custody handback.
If independent access is unavailable, escalate and keep service restricted; do
not create a synthetic app identity, bypass membership, disable MFA/CSRF or
replace a production named resolver with the deployment password. An app Owner
role is not provider-console authorization. Do not remove the last active Owner
as a recovery shortcut; use the existing explicit suspension/ownership policy.

## Rotation sequence

1. Confirm exact environment/resource/credential and all consumers from the
   private inventory; get authorization for the change. Compromise uses immediate
   containment/revocation, even if availability is lost. Routine rotation can
   stage a replacement before revocation where the provider safely permits it.
2. Preserve authorized recovery access and current non-secret configuration
   references. Disable command tracing/transcription and prevent terminal/browser
   recordings. Retrieve/generate material through the approved secret store or
   hidden prompt; never paste into shell arguments, GitHub text or build inputs.
3. Create the replacement at the provider with no broader rights. Stage runtime,
   workflow, timer and operator consumers coherently; drain workers/pools when
   necessary. For DB credentials, preserve endpoint identity and role separation;
   rotation is not permission to rebind to another database.
4. Apply the owner-specific procedure below, reload/restart only authorized
   consumers, and verify readiness plus the applicable safe operation. Verify
   revocation of the old credential privately; record only pass/fail. A successful
   deploy does not prove copied sessions/tokens or open DB connections are dead.
5. Update the private inventory, remove secret material from process scope and
   revoke old/temporary access. Retain required protected recovery keys/artifacts;
   disposal, provider purchases and retention changes require separate approval.
6. If a provider response is uncertain, inspect its actual state before any retry.
   Keep ingress restricted until credential/session state is known. Do not revert
   a compromised secret, old session epoch or an authentication guard to regain
   availability. Record partial results and the next owner/action.

### Named identity and restored-session revocation

Use [Session revocation](../identity/AUTHENTICATION.md#revocation-and-owner-recovery)
and [Hosted identity
checks](../identity/HOSTED_ROLLOUT.md#hosted-rehearsal-and-expected-results). These
interfaces now exist; incident response does not wait for identity and tenancy as a
whole or add a second session store. Targeted account compromise uses the existing
trusted `IdentityService.revoke_user_sessions(user_id)` or authenticated user's
CSRF-protected revoke-all operation. The internal user binding must be verified
privately; email or a browser selector is not authority. Provider account reset and app
session revocation are separate required actions.

For environment-wide compromise, OIDC issuer/client/secret/origin/generation
change, or **any restored identity/session database**:

1. Restrict ingress and drain every HTTP/job worker using that identity DB.
   Confirm no old worker can issue sessions. Repair compromised provider access.
2. Privately read the current `identity_environments` epoch for the exact approved
   environment. For a restore use its restored epoch, not the old live value.
   Use a new random session generation and intended named settings from the secret
   store. Confirm DB lineage/fingerprint with the incident binding procedure.
   Inject explicit `TABULA_RASA_DATABASE_URL`; the rotation tool bypasses local
   database fallback. No URL/secret is an argument.
3. From the compatible release run the existing command with the observed epoch:

   ```powershell
   python scripts/rotate_identity_configuration.py --expected-epoch $observedEpoch
   ```

   Failure is a stop: it rejects stale epochs, unchanged configuration and
   uninitialized environments. Inspect current durable state and whether this
   attempt committed before deciding what to do next. Do not blindly repeat a
   compare-and-swap or edit its stored hash/epoch to force success.
4. Start only workers with the new configuration. Verify pre-rotation/restored
   session and pending-login copies deny on all workers/restart; an old-config
   worker must fail closed. New named login must succeed with fresh authorization,
   including permitted/denied synthetic business checks. Keep copies private and
   destroy test copies according to evidence policy. Record only outcomes.
5. Reopen only after the identity owner confirms provider recovery, local
   revocation and applicable ingress/role checks. If hosted fixtures/provider
   access are missing, keep their coverage pending and maintain containment.

Provider outage alone does not require destroying all healthy local sessions.
Session compromise does. Logout that only clears local cookies during a database
outage must remain reported as unconfirmed server revocation.

### Google grant disconnect and rotation

[Entity connections](../identity/CONNECTIONS_AND_CLAIMS.md#disconnect-versus-google-revocation)
own lifecycle/revision checks. The freshly authenticated business Owner confirms
Disconnect to block application use while preserving grants, bytes and links.
Then the authorized Google-account operator reviews every consumer of that
consent, including retained Sheets/backup operations, before provider revocation
through account permissions or the existing `GoogleDriveClient.revoke` path.
Do not automatically revoke shared consent, rotate another entity's grant or
delete encrypted files as rollback. If Google cannot confirm, local disconnect
is effective but provider revocation remains unconfirmed.

For replacement grants/connection encryption keys follow the canonical
[operator-assisted provisioning](../identity/CONNECTIONS_AND_CLAIMS.md#operator-assisted-google-connect-and-reconnect):
fresh exact environment/entity/provider envelopes, private new key/directory,
plan/revision checks and Owner reconnect. Verify the affected business's exact
bytes and another business's unchanged access. Do not copy production grants
into staging. NAS recovery-key custody follows the
[protected NAS inventory](NAS_BACKUP.md#protected-configuration-and-inventory).

## Evidence and human handoff

Use [incident evidence](INCIDENT_RESPONSE.md#timeline-evidence-and-closure) for
declaration, containment, provider/consumer changes, old credential refusal,
session revocation, healthy operation, custodian/alternate and residual risk.
Never record even hashed passwords/tokens as evidence; approved opaque record
references and configuration/evidence digests must contain no secret material.
Diagnostic pseudonyms are not access grants and rotating their key is not auth
revocation. Secret managers, encrypted files and provider audit logs stay private.

Before commercial recovery agreement acceptance, the human primary reviews
custody/recovery evidence and the alternate independently demonstrates secret retrieval,
safe commands and handoff. Track missing access, hosted revocation tests, provider
privilege limits or document-key recovery separately under platform operations,
operational readiness verification, identity and tenancy and receipt byte recovery. Only
a real human response can mark handoff accepted or improvements requested; this runbook
supplies no commercial sign-off, release admission or retention/deletion policy.
