# Setup

Bookkeeping uses the [browser/API runtime](browser/RUNTIME.md) and PostgreSQL.
Create new businesses in the application; native bootstrap creates the ledger.
[Deployment](operations/DEPLOYMENT.md) owns server setup. This guide covers the
local operator CLI for Google grants, folders, source/recovery
workbooks and protected local configuration. It does not create PostgreSQL entities
or provide customer self-service Google consent.

## Prerequisites and configuration

Use Python 3.12 or newer, a supported Google account, and a Google Cloud
shared project and OAuth client for the deployment. The application uses:

| File | Contents |
| --- | --- |
| .streamlit/secrets.toml | Application password hash and app-wide Drive OAuth/Picker configuration |
| .streamlit/business_profiles.json | Profile names, workbook IDs, Google OAuth grants/folders, and local recovery metadata |

Both files are ignored. The profile file is plaintext and must stay on a
protected, non-synchronized filesystem. Never commit, email, upload, log, or
paste either file's private values.

## Install and configure

From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create the ignored application secrets file and set an application password:

```powershell
New-Item -ItemType Directory -Force .streamlit | Out-Null
New-Item -ItemType File -Force .streamlit\secrets.toml | Out-Null
python scripts\set_app_password.py
```

At minimum, the file contains an application password hash. Add the existing shared
OAuth and Picker settings from [Google Drive](GOOGLE_DRIVE.md). Keep the existing
configuration filenames when upgrading.

## Local profile administration

Run from the repository root in an interactive terminal:

```text
python -m scripts.local_profile_admin --help
python -m scripts.local_profile_admin profile add PROFILE_KEY --name "Business name"
python -m scripts.local_profile_admin google connect PROFILE_KEY
python -m scripts.local_profile_admin workbook select PROFILE_KEY
python -m scripts.local_profile_admin workbook initialize PROFILE_KEY
python -m scripts.local_profile_admin profile check PROFILE_KEY
```

Each command requires access to the protected local files and prompts for the
application password without echo. The password grants operator access to all
profiles in this store. Commercial Owner membership grants no access to these
local files or commands. Use `profile list` to review profile keys and
connection status. Keys are immutable. Use `profile edit PROFILE_KEY --name
"New name"` or `--backup-folder ABSOLUTE_PATH` to update local settings. Use
`recovery show PROFILE_KEY` to inspect the resulting paths and Synology metadata.

The default browser opens for Google consent and Picker. Return to the terminal
for the result. Google denial, Picker cancellation, Ctrl+C, and a ten-minute
timeout end the interaction. The helper uses an existing loopback callback and
closes before the command exchanges a code, validates remote access, or writes
local settings. Closing a tab without cancelling leaves the terminal command
available to interrupt. Choose an existing registered URI with
`--redirect-uri` if the default port is occupied.

A Picker-selected workbook must be accessible with Editor capability before its
ID is saved. An active workbook may belong to only one local profile. Use
`workbook authorize PROFILE_KEY` for a separate recovery or migration candidate.
It reports the authorized ID while preserving the active workbook selection.

### Blank workbook and required worksheets

1. Create a restricted folder and a blank spreadsheet for the business.
2. Ensure the profile's human Google account has Editor access. Select the
   workbook with `workbook select PROFILE_KEY`.
3. Run `workbook initialize PROFILE_KEY` to initialize or validate the schema.

The exact worksheet/header contract is centralized in
[Final Sheets source contract](SHEETS_SOURCE_CONTRACT.md). The default Sheet1
tab may remain. A genuinely empty workbook, or interrupted bootstrap containing
only current headers and no records, receives format-13 tabs and headers.
Populated workbooks are validated without modification; missing, historical,
or additional ledger fields are rejected. Select a supported format-13 workbook
or restore a validated format-13 backup into a new blank workbook. Historical
ledger and archive converters are retired; preserve unsupported source files.

Earlier profile and receipt-association preparation converters are retired. Use only
current local configuration and explicit format-13 receipt relationships. No startup,
backup or migration path infers associations from old Drive metadata. Preserve
unsupported sources and use a validated format-13 generation for recovery.




## Optional Google Drive, Maps, and recovery settings

For receipts and cloud-native backups, connect the selected profile to a human Google
account and provision its Drive storage root, Receipts child, and ledger Backups child.
Sheets and Drive use the same profile OAuth grant. Follow [Google
Drive](GOOGLE_DRIVE.md) for the complete OAuth and Picker procedure.

Google address suggestions and automatic route mileage require paid,
app-wide Maps Platform configuration, two separately restricted API keys,
quotas, budget alerts, attribution, and deployment policy surfaces. Complete
the operator checklist in [Google Maps mileage setup](GOOGLE_MAPS.md) before
making a live request. Maps is not profile-scoped and must not reuse the Sheets
profile Google OAuth identity.

For an optional Synology recovery copy, use `recovery set PROFILE_KEY` to record
a profile-root Cloud Sync destination, schedule, and retention policy. Configure
and check the actual task in DSM. Follow the [backup and recovery
guide](BACKUP_RECOVERY.md) for the required download-only task and validation.

## Commercial deployment configuration

Streamlit Cloud Secrets generation and export are retired. Local administration
continues to use the protected split app/profile files. Local changes do not
configure the React/FastAPI deployment. Use [external connections and claims](identity/CONNECTIONS_AND_CLAIMS.md)
for approved encrypted grant staging and Owner activation, and [deployment](operations/DEPLOYMENT.md)
for server configuration. Never paste provider grants into a browser form or
bookkeeping export.

## Validate readiness

Validate one configured workbook, its headers, and managed Payees records
without displaying ledger rows:

```powershell
python scripts\validate_google_sheets.py --profile PROFILE_KEY
```

For a reversible permission test on a disposable worksheet:

```powershell
python scripts\validate_google_sheets.py --profile PROFILE_KEY --write-test
```

The write test creates, reads, updates, and deletes only that disposable
worksheet. For a recovery workbook, use a read-only spreadsheet override:

```powershell
python scripts\validate_google_sheets.py --profile PROFILE_KEY --spreadsheet-id RECOVERY_WORKBOOK_ID
```

The override cannot be combined with `--write-test`. Confirm the intended profile
and workbook privately before any recovery mutation.

## Troubleshooting

### Google OAuth or workbook access failed

Reconnect the selected profile's Google account. Confirm Sheets API is enabled
in the shared OAuth project, Editor access to the workbook, and explicit Picker
selection. Do not make the workbook public. Confirm required tabs and headers;
Initialization refuses populated incompatible worksheets.

## Credential rotation

Reconnect Google locally to replace a local grant and validate the selected profile.
For a commercial connection, follow the separate approved encrypted staging and
Owner activation procedure in [connections](identity/CONNECTIONS_AND_CLAIMS.md). To replace the application password:

```powershell
python scripts\set_app_password.py
```

This changes the local application password only. Commercial named login and
provider secrets use separate custody; local password rotation does not update
them or grant business access.

Keep OAuth configuration and refresh tokens in protected storage. Never place credentials in backups, issues, logs, screenshots, or
documentation.

## References

- [Google Sheets API concepts](https://developers.google.com/workspace/sheets/api/guides/concepts)
- [Google Sheets authorization scopes](https://developers.google.com/workspace/sheets/api/scopes)
- [Google Drive sharing permissions](https://support.google.com/drive/answer/2494822)

## Existing deployment configuration

Runtime accepts only version-2 local profiles. Older profile conversion is retired;
use [Google authorization and configuration recovery](OAUTH_MIGRATION.md) for current
setup and preservation guidance. Commercial connection staging remains separate.


### Create an initial workbook

For an approved Sheets source setup, a profile with no ledger can run
`python -m scripts.local_profile_admin workbook create PROFILE_KEY`. It creates
`Business name Ledger` in the authorized account's My Drive and saves the ID before
initializing the format-13 tabs and defaults. This action refuses a profile that
already has a workbook. New PostgreSQL businesses use in-app creation.

If creation is uncertain, inspect Drive before retrying. Use `workbook select`
for an existing file or `workbook initialize` if the ID is already saved.
For a separate blank restore target, create a blank workbook in Google Sheets
and use `workbook authorize`. Follow [backup and recovery](BACKUP_RECOVERY.md).
