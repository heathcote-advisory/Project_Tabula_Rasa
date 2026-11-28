# Documentation

Use these guides by application area. [Architecture](ARCHITECTURE.md) describes
the system boundaries and [design.md](../design.md) defines the interface.
Bookkeeping uses the browser application and PostgreSQL. The local operator CLI
supports profile administration. Sheets tools support source migration, archive validation and recovery.

## Application use and internal setup

- [User workflows](USING_TABULA_RASA.md): accounts, transactions, journals,
  recurring records, assets, receipts, mileage, reports and imports.
- [Local operator setup](SETUP.md), [local Google Drive setup](GOOGLE_DRIVE.md),
  [Google Maps](GOOGLE_MAPS.md) and [Google authorization recovery](OAUTH_MIGRATION.md).
- [Sheets backup and recovery](BACKUP_RECOVERY.md) and the
  [frozen Sheets source contract](SHEETS_SOURCE_CONTRACT.md).
- [Terms of Use](TERMS_OF_USE.md) and [Privacy Policy](PRIVACY_POLICY.md).

## Browser application and HTTP API

- [Runtime, packages and topology](browser/RUNTIME.md) and
  [HTTP/browser foundation](browser/FOUNDATION.md).
- [Protected mutations and concurrency](browser/CONCURRENCY.md).
- [Core bookkeeping](browser/BOOKKEEPING.md),
  [recurring, mixed-use and assets](browser/RECURRING_AND_ASSETS.md),
  [mileage and receipts](browser/MILEAGE_AND_RECEIPTS.md), and
  [imports, exports and integrations](browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md).
- [UI components](browser/UI_COMPONENTS.md), [browser testing](browser/TESTING.md),
  [visual and accessibility review](browser/VISUAL_REVIEW.md), and
  [release validation and Streamlit transition](browser/RELEASE_VALIDATION.md).

## Identity and authorization

- [Provider setup](identity/PROVIDER_SETUP.md) and
  [authentication and sessions](identity/AUTHENTICATION.md).
- [Server authorization](identity/AUTHORIZATION.md) and
  [business switching, membership and onboarding](identity/WORKSPACES.md).
- [External connections and existing entity claims](identity/CONNECTIONS_AND_CLAIMS.md).
- [Customer Google Drive connections](identity/GOOGLE_DRIVE.md).
- [Hosted rollout](identity/HOSTED_ROLLOUT.md) and
  [identity/tenant security testing](identity/SECURITY_TESTING.md).

## Accounting and database

- [PostgreSQL development and operations](database/POSTGRESQL.md) and
  [database mutations and loaded baselines](database/CONCURRENCY.md).
- [Accounting periods and corrections](accounting/PERIODS_AND_CORRECTIONS.md).
- [Sheets migration and reconciliation](accounting/SHEETS_MIGRATION.md),
  [acceptance and controlled cutover](accounting/CUTOVER.md), and
  [complete entity export](accounting/EXPORT.md).

## Document storage

- [Hosted staging receipts](storage/STAGING_RECEIPTS.md) and
  [Drive receipt storage, byte backup and recovery](storage/RECEIPT_RECOVERY.md).
- [Optional object storage](storage/OBJECT_STORAGE.md): deferred groundwork,
  requiring separate provider adoption.

## Deployment and operations

- [Development/staging deployment](operations/DEPLOYMENT.md),
  [stage-specific operational readiness](operations/DEPLOYMENT.md#stage-specific-operational-readiness),
  [observability](operations/OBSERVABILITY.md), and
  [least-privilege support](operations/SUPPORT.md).
- [Durable jobs and bookkeeping audit history](operations/JOBS_AND_AUDIT.md) and
  [audit retention/access policy](operations/AUDIT_POLICY.md).
- [Managed database recovery](operations/DATABASE_RECOVERY.md),
  [NAS backups](operations/NAS_BACKUP.md), and
  [audit-aware recovery verification](operations/AUDIT_RECOVERY.md).
- [Incident response](operations/INCIDENT_RESPONSE.md),
  [controlled incident drills](operations/INCIDENT_DRILLS.md), and
  [secret custody/rotation](operations/SECRETS.md).
- [Resource inventory and approved retirement](operations/RESOURCE_RETIREMENT.md).

## Development and architecture decisions

- [Development and review workflow](DEVELOPMENT.md) and
  [CI execution and suite ownership](development/CI.md).
- [Accounting schema ADR](adr/0001-canonical-accounting-schema.md).
- [Web runtime ADR](adr/0002-commercial-web-runtime.md).
- [Identity and tenancy ADR](adr/0003-commercial-identity-and-tenancy.md).
- [Receipt storage ADR](adr/0004-managed-receipt-storage.md).
- [Commercial billing ADR](adr/0005-commercial-billing.md).

## Documentation ownership

Update the relevant component guide when behavior, configuration or recovery
procedures change. Keep architecture decisions in `adr/` and current interface
rules in `design.md`. Issue/PR progress, dated evidence, review handoffs and
release-specific acceptance results belong in the owning GitHub discussion.
Link to those records when provenance matters; do not maintain a second status
ledger here. Repository-wide contributor rules are in [AGENTS.md](../AGENTS.md).
