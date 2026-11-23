"""Readiness validation and presentation for local business profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tabula_rasa.google_drive import GoogleDriveError
from tabula_rasa.google_drive_admin import validate_google_drive_profile
from tabula_rasa.local_profile_store import LocalProfileStoreError
from tabula_rasa.profile_readiness import (
    ProfileReadiness,
    profile_configuration_readiness,
)
from tabula_rasa.storage import (
    GoogleSheetsStorage,
    StorageAuthenticationError,
    StorageError,
    StorageInitializationError,
    StorageValidationError,
    StorageWorkbookAccessError,
)


def safe_readiness_error(stage: str, error: Exception) -> str:
    """Return an actionable readiness message without echoing secrets."""
    if stage == "Sheets" and isinstance(error, StorageAuthenticationError):
        return "Google OAuth validation failed. Reconnect this profile's Google account."
    if stage == "Sheets" and isinstance(error, StorageWorkbookAccessError):
        return "Sheets workbook validation failed. Confirm sharing and the workbook ID."
    if stage == "Sheets" and isinstance(error, StorageInitializationError):
        return "Sheets workbook initialization failed. Confirm Editor access."
    if stage == "Sheets" and isinstance(error, StorageValidationError):
        return "Sheets workbook structure validation failed. Repair the required tabs."
    if stage == "Drive" and isinstance(error, GoogleDriveError):
        return "Google Drive validation failed. Check the grant and selected folders."
    return f"{stage} validation failed. Retry the readiness check."


def fresh_profile_readiness(
    profile_key: str,
    settings: Mapping[str, Any],
    *,
    app_secrets: Mapping[str, Any],
    local_profiles: Mapping[str, Mapping[str, Any]] | None = None,
) -> ProfileReadiness:
    """Run fresh Sheets and configured Drive checks for one profile."""
    validation_errors: list[str] = []
    profiles_for_isolation = local_profiles or {profile_key: settings}
    try:
        GoogleSheetsStorage.from_secrets_read_only(
            {**settings, "google_drive_oauth": app_secrets.get("google_drive_oauth")}
        )
    except StorageError as error:
        validation_errors.append(safe_readiness_error("Sheets", error))

    drive = settings.get("google_drive")
    storage_root = drive.get("storage_root") if isinstance(drive, Mapping) else None
    if (
        isinstance(drive, Mapping)
        and isinstance(storage_root, Mapping)
        and storage_root.get("id")
    ):
        try:
            validate_google_drive_profile(
                app_secrets,
                profile_key,
                drive,
                local_profiles=profiles_for_isolation,
            )
        except (GoogleDriveError, LocalProfileStoreError) as error:
            validation_errors.append(safe_readiness_error("Drive", error))

    return profile_configuration_readiness(
        profile_key,
        settings,
        validation_errors=tuple(validation_errors),
    )
