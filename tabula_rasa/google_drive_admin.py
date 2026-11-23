"""Operator workflows for profile-scoped Google authorization and resources."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tabula_rasa.google_drive import (
    GoogleDriveAccessError,
    GoogleDriveClient,
    GoogleDriveConfigurationError,
    GoogleDriveError,
    GoogleDriveOAuthConfig,
    exchange_authorization_code,
    load_oauth_config,
)
from tabula_rasa.local_profile_admin_support import initialize_candidate_storage
from tabula_rasa.local_profile_store import (
    LOCAL_PROFILE_STORE_PATH,
    LocalProfileStoreError,
    load_local_profiles,
    parse_spreadsheet_id,
    update_local_profile,
    update_local_profile_google_drive,
)
from tabula_rasa.storage import GoogleSheetsStorage


def _ensure_storage_root_is_not_directly_nested(
    profile_key: str,
    selected_root: Mapping[str, Any],
    local_profiles: Mapping[str, Mapping[str, Any]],
) -> None:
    """Reject known root reuse or direct nesting using visible folder IDs only."""
    if not isinstance(selected_root, Mapping):
        raise GoogleDriveConfigurationError(
            "Google Drive could not return the selected folder details. Retry."
        )
    selected_id = str(selected_root.get("id", ""))
    parents = selected_root.get("parents")
    parent_ids = (
        {str(parent_id) for parent_id in parents}
        if isinstance(parents, list)
        else set()
    )
    for other_key, profile in local_profiles.items():
        if other_key == profile_key:
            continue
        drive = profile.get("google_drive")
        other_root = drive.get("storage_root") if isinstance(drive, Mapping) else None
        if not isinstance(other_root, Mapping):
            continue
        other_id = str(other_root.get("id", ""))
        if other_id and (other_id == selected_id or other_id in parent_ids):
            raise GoogleDriveConfigurationError(
                "Google Drive storage roots cannot be nested between business profiles."
            )


def _root_matches_profile(folder: Mapping[str, Any], profile_key: str) -> bool:
    """Manually selected roots can be untagged; reject known foreign resources."""
    properties = folder.get("appProperties")
    if not isinstance(properties, Mapping):
        return True
    return properties.get("tabulaRasaKind") in (
        None,
        "storage-root",
    ) and properties.get("tabulaRasaProfile") in (None, profile_key)


def _revalidated_storage_settings(
    client: GoogleDriveClient,
    profile_key: str,
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only destinations confirmed under the newly authorized account."""
    preserved: dict[str, Any] = {}
    storage_root = previous.get("storage_root")
    if isinstance(storage_root, Mapping):
        root_id = str(storage_root.get("id", ""))
        try:
            root = client.folder(root_id)
        except GoogleDriveError:
            return preserved
        if not _root_matches_profile(root, profile_key):
            return preserved
        root_id = str(root["id"])
        preserved["storage_root"] = {
            "id": root_id,
            "name": str(root["name"]),
        }
        for field, storage_kind in (
            ("receipt_folder", "receipts"),
            ("backup_folder", "ledger-backups"),
        ):
            child = previous.get(field)
            if not isinstance(child, Mapping):
                continue
            try:
                confirmed = client.folder(str(child.get("id", "")))
            except GoogleDriveError:
                continue
            parents = confirmed.get("parents")
            if not isinstance(parents, list):
                parents = []
            properties = confirmed.get("appProperties")
            if (
                root_id in parents
                and isinstance(properties, Mapping)
                and properties.get("tabulaRasaKind") == storage_kind
                and properties.get("tabulaRasaProfile") == profile_key
                and properties.get("tabulaRasaStorageRoot") == root_id
            ):
                preserved[field] = {
                    "id": str(confirmed["id"]),
                    "name": str(confirmed["name"]),
                }
        return preserved

    return preserved


def save_google_authorization(
    config: GoogleDriveOAuthConfig,
    profile_key: str,
    code: str,
    redirect_uri: str,
    *,
    store_path: Path = LOCAL_PROFILE_STORE_PATH,
) -> None:
    """Exchange an accepted callback and replace only the selected profile's grant."""
    local_profiles = load_local_profiles(store_path)
    profile = local_profiles[profile_key]
    drive_settings = exchange_authorization_code(config, code, redirect_uri)
    previous = profile.get("google_drive")
    if isinstance(previous, Mapping):
        client = GoogleDriveClient(config, drive_settings)
        drive_settings.update(
            _revalidated_storage_settings(client, profile_key, previous)
        )
        root = drive_settings.get("storage_root")
        if root:
            _ensure_storage_root_is_not_directly_nested(
                profile_key, client.folder(root["id"]), local_profiles
            )
    update_local_profile_google_drive(profile_key, drive_settings, path=store_path)


def validate_google_drive_profile(
    app_secrets: Mapping[str, Any],
    profile_key: str,
    drive_settings: Mapping[str, Any],
    *,
    local_profiles: Mapping[str, Mapping[str, Any]],
) -> None:
    """Re-read one profile's Drive grant and configured destinations."""
    storage_root = drive_settings.get("storage_root")
    if not isinstance(storage_root, Mapping) or not storage_root.get("id"):
        raise GoogleDriveConfigurationError(
            "Select or create the Google Drive storage root."
        )
    root_id = str(storage_root.get("id") or "")
    config = load_oauth_config(app_secrets)
    client = GoogleDriveClient(config, drive_settings)
    root = client.folder(root_id)
    if not _root_matches_profile(root, profile_key):
        raise GoogleDriveAccessError(
            "The storage root belongs to a different profile or purpose."
        )
    client.validate_storage_configuration(profile_key, root_folder=root)
    _ensure_storage_root_is_not_directly_nested(
        profile_key,
        root,
        local_profiles,
    )


def save_storage_root(
    profile_key: str,
    drive_settings: Mapping[str, Any],
    selected_id: str,
    *,
    client: GoogleDriveClient,
    store_path: Path,
) -> bool:
    existing = drive_settings.get("storage_root")
    folder = client.folder(selected_id)
    if not isinstance(folder, Mapping):
        raise GoogleDriveAccessError(
            "Google Drive could not return the selected folder details. Retry."
        )
    _ensure_storage_root_is_not_directly_nested(
        profile_key, folder, load_local_profiles(store_path)
    )
    if not _root_matches_profile(folder, profile_key):
        raise GoogleDriveAccessError("Select a storage root for this profile.")
    folder_id = str(folder.get("id") or "")
    folder_name = str(folder.get("name") or "").strip()
    if not folder_id or not folder_name:
        raise GoogleDriveAccessError(
            "Google Drive could not return the selected folder details. Retry."
        )
    updated = dict(drive_settings)
    updated["storage_root"] = {
        "id": folder_id,
        "name": folder_name,
    }
    same_root = isinstance(existing, Mapping) and existing.get("id") == selected_id
    if same_root and existing.get("name") == folder_name:
        return False
    if not same_root:
        updated.pop("receipt_folder", None)
        updated.pop("backup_folder", None)
    update_local_profile_google_drive(profile_key, updated, path=store_path)
    return True


def create_storage_root(
    profile_key: str,
    profile_name: str,
    drive_settings: Mapping[str, Any],
    *,
    client: GoogleDriveClient,
    store_path: Path,
) -> None:
    root = client.create_storage_root(
        profile_key,
        f"{profile_name} Storage",
    )
    save_storage_root(
        profile_key, drive_settings, root["id"], client=client, store_path=store_path
    )


def provision_storage_children(
    profile_key: str,
    drive_settings: Mapping[str, Any],
    *,
    client: GoogleDriveClient,
    store_path: Path,
) -> None:
    storage_root = drive_settings.get("storage_root")
    if not isinstance(storage_root, Mapping):
        raise GoogleDriveConfigurationError(
            "Select or create a storage root before provisioning its folders."
        )
    storage_root_id = str(storage_root.get("id", ""))
    if not storage_root_id:
        raise GoogleDriveConfigurationError(
            "The configured storage root is missing its Google Drive folder ID."
        )
    children = client.provision_storage_children(profile_key, storage_root_id)
    updated = dict(drive_settings)
    updated["receipt_folder"] = children["receipts"]
    updated["backup_folder"] = children["ledger-backups"]
    update_local_profile_google_drive(profile_key, updated, path=store_path)


def select_profile_ledger(
    app_secrets: Mapping[str, Any],
    profile_key: str,
    selected: Mapping[str, Any],
    *,
    store_path: Path = LOCAL_PROFILE_STORE_PATH,
    persist: bool = True,
) -> str:
    """Validate the selected file with fresh profile OAuth before saving its ID.

    Opening alone allows historical workbooks to be selected for the explicit
    migration command. Validation/blank initialization is a separate action.
    """
    profiles = load_local_profiles(store_path)
    profile = profiles[profile_key]
    spreadsheet_id = parse_spreadsheet_id(str(selected.get("id") or ""))
    if persist and any(
        key != profile_key and other.get("spreadsheet_id") == spreadsheet_id
        for key, other in profiles.items()
    ):
        raise LocalProfileStoreError(
            "A Sheets workbook can be configured for only one business profile."
        )
    candidate = {
        **profile,
        "spreadsheet_id": spreadsheet_id,
        "google_drive_oauth": app_secrets.get("google_drive_oauth"),
    }
    GoogleSheetsStorage._open_workbook_from_secrets(candidate, scopes=[])
    # Sheets opening confirms read access; Drive capabilities confirms Editor access.
    client = GoogleDriveClient(load_oauth_config(app_secrets), profile["google_drive"])
    from tabula_rasa.google_drive.errors import DRIVE_API_ROOT, _json_response

    metadata = _json_response(
        client._request(
            "GET",
            f"{DRIVE_API_ROOT}/files/{spreadsheet_id}",
            params={
                "fields": "id,mimeType,capabilities(canEdit)",
                "supportsAllDrives": "true",
            },
        )
    )
    capabilities = metadata.get("capabilities")
    if (
        metadata.get("mimeType") != "application/vnd.google-apps.spreadsheet"
        or not isinstance(capabilities, Mapping)
        or not capabilities.get("canEdit")
    ):
        raise GoogleDriveAccessError(
            "Select a Google Sheets workbook with Editor access."
        )
    if not persist:
        return spreadsheet_id
    update_local_profile(
        profile_key,
        name=str(profile["name"]),
        spreadsheet=spreadsheet_id,
        backup_folder_path=profile.get("backup_folder_path"),
        path=store_path,
    )
    return spreadsheet_id


def provision_profile_ledger(
    app_secrets: Mapping[str, Any],
    profile_key: str,
    *,
    store_path: Path = LOCAL_PROFILE_STORE_PATH,
) -> None:
    """Provision an initial ledger, saving its ID before recoverable initialization.

    A partial workbook is retained in Drive for the explicit initialize command. A configured ledger is never replaced by this initial setup action.
    """
    profile = load_local_profiles(store_path)[profile_key]
    if profile.get("spreadsheet_id"):
        raise LocalProfileStoreError(
            "This profile already has a ledger. Use Picker to change it."
        )
    client = GoogleDriveClient(
        load_oauth_config(app_secrets), profile.get("google_drive", {})
    )
    created = client.create_ledger_spreadsheet(profile_key, f"{profile['name']} Ledger")
    update_local_profile(
        profile_key,
        name=str(profile["name"]),
        spreadsheet=created["id"],
        backup_folder_path=profile.get("backup_folder_path"),
        path=store_path,
    )
    initialize_candidate_storage(
        {
            **profile,
            "spreadsheet_id": created["id"],
            "google_drive_oauth": app_secrets.get("google_drive_oauth"),
        }
    )
