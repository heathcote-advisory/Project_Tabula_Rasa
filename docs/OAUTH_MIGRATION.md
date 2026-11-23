# Google authorization and configuration recovery

The one-time version-1 profile converter is retired. Local administration accepts
only version-2 profile documents and human Google OAuth grants. Preserve old protected
configuration for operator investigation; do not load obsolete service-account fields
into runtime or automatically rewrite the source file.

Use [Setup](SETUP.md) and [Google Drive](GOOGLE_DRIVE.md) to register the intended local
profile, connect or replace its grant, select existing resources through Picker and
validate readiness. One shared web OAuth client serves the deployment; every profile
keeps its own grant, workbook and folder bindings. Reconnection must preserve existing
folder IDs only when the selected account can validate them. Never print tokens.

Native PostgreSQL business creation does not acquire a Google grant. Commercial grant
staging and named Owner activation follow [connections](identity/CONNECTIONS_AND_CLAIMS.md).
The local password is independent of commercial membership and requires protected operator access and a fresh prompt. Google browser interaction
is loopback-only.

For recovery, `python -m scripts.local_profile_admin workbook authorize PROFILE_KEY` grants access
to a candidate without selecting it as the live ledger. [Restore](BACKUP_RECOVERY.md)
still rejects a configured live workbook and requires a new blank target. Only
[format 13](SHEETS_SOURCE_CONTRACT.md) workbooks and archives are supported. Configuration
retirement changes no backup retention and deletes no credentials, ledgers or receipts.
