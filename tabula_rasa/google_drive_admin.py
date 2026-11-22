"""Authenticated local administration for business-scoped Drive OAuth."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

import streamlit as st

from tabula_rasa.google_drive import (
    GoogleDriveAccessError,
    GoogleDriveAuthorizationError,
    GoogleDriveClient,
    GoogleDriveConfigurationError,
    GoogleDriveError,
    GoogleDriveOAuthConfig,
    authorization_url,
    exchange_authorization_code,
    load_oauth_config,
    select_redirect_uri,
    verify_oauth_state,
)
from tabula_rasa.google_drive_picker import folder_picker, spreadsheet_picker
from tabula_rasa.storage import GoogleSheetsStorage, StorageError
from tabula_rasa.local_profile_admin_support import initialize_candidate_storage
from tabula_rasa.local_profile_store import update_local_profile, parse_spreadsheet_id
from tabula_rasa.local_profile_store import (
    LOCAL_PROFILE_STORE_PATH,
    LocalProfileStoreError,
    ensure_google_drive_disconnect_allowed,
    load_local_profiles,
    update_local_profile_google_drive,
)


@st.cache_resource
def _cached_drive_client(
    config: GoogleDriveOAuthConfig,
    profile_key: str,
    settings_json: str,
) -> GoogleDriveClient:
    """Reuse a profile's access token while its saved OAuth settings are unchanged."""
    del profile_key
    return GoogleDriveClient(config, json.loads(settings_json))


def _query_value(value: object) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _folder_display_name(
    folder: Mapping[str, Any],
    fallback: str = "",
) -> str:
    return str(folder.get("name") or fallback)


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
                "Google Drive storage roots cannot be nested between business "
                "profiles."
            )


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
        root_properties = root.get("appProperties")
        if (
            not isinstance(root_properties, Mapping)
            or root_properties.get("tabulaRasaKind") != "storage-root"
            or root_properties.get("tabulaRasaProfile") != profile_key
        ):
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


def process_google_drive_oauth_callback(
    app_secrets: Mapping[str, Any],
    *,
    store_path: Path = LOCAL_PROFILE_STORE_PATH,
) -> bool:
    """Process a signed Google callback and persist one local refresh token."""
    state = _query_value(st.query_params.get("state"))
    code = _query_value(st.query_params.get("code"))
    error = _query_value(st.query_params.get("error"))
    if not state or (not code and not error):
        return False

    try:
        config = load_oauth_config(app_secrets)
        payload = verify_oauth_state(config, state)
        profile_key = str(payload["profile"])
        local_profiles = load_local_profiles(store_path)
        if profile_key not in local_profiles:
            raise LocalProfileStoreError(
                "The Google authorization profile is not available locally."
            )
        if error:
            raise GoogleDriveAuthorizationError(
                "Google Drive authorization was cancelled or denied."
            )
        drive_settings = exchange_authorization_code(
            config,
            code,
            str(payload["redirect_uri"]),
        )
        previous_drive = local_profiles[profile_key].get("google_drive")
        if isinstance(previous_drive, Mapping):
            client = GoogleDriveClient(config, drive_settings)
            drive_settings.update(
                _revalidated_storage_settings(
                    client,
                    profile_key,
                    previous_drive,
                )
            )
            storage_root = drive_settings.get("storage_root")
            if isinstance(storage_root, Mapping):
                # Re-fetch the root after revalidation so direct-parent metadata
                # is available for the minimal cross-profile check; persisted
                # settings intentionally retain only the validated ID and name.
                storage_root_id = str(storage_root.get("id") or "").strip()
                if storage_root_id:
                    storage_root = client.folder(storage_root_id)
                    _ensure_storage_root_is_not_directly_nested(
                        profile_key,
                        storage_root,
                        local_profiles,
                    )
        update_local_profile_google_drive(
            profile_key,
            drive_settings,
            path=store_path,
        )
        st.session_state["local_profile_admin_unlocked"] = True
        st.session_state["google_drive_admin_notice"] = (
            f"Google Drive was connected for profile '{profile_key}'. "
            "Choose or provision its profile storage folders next."
        )
    except (GoogleDriveError, LocalProfileStoreError) as exc:
        st.session_state["google_drive_admin_error"] = str(exc)
    finally:
        st.query_params.clear()
    st.cache_resource.clear()
    st.rerun()
    return True


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
    client = _cached_drive_client(
        config,
        profile_key,
        json.dumps(dict(drive_settings), sort_keys=True),
    )
    root = client.folder(root_id)
    client.validate_storage_configuration(profile_key, root_folder=root)
    _ensure_storage_root_is_not_directly_nested(
        profile_key,
        root,
        local_profiles,
    )


def _save_storage_root(
    profile_key: str,
    drive_settings: Mapping[str, Any],
    selected: Mapping[str, Any],
    *,
    client: GoogleDriveClient,
    store_path: Path,
    folder: Mapping[str, Any] | None = None,
) -> bool:
    selected_id = str(selected.get("id", ""))
    existing = drive_settings.get("storage_root")
    if folder is None:
        folder = client.folder(selected_id)
    if not isinstance(folder, Mapping):
        raise GoogleDriveAccessError(
            "Google Drive could not return the selected folder details. Retry."
        )
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
    st.session_state["google_drive_admin_notice"] = (
        (
            f"Google Drive storage root name refreshed for profile '{profile_key}'."
            if same_root
            else (
                f"Google Drive storage root saved for profile '{profile_key}'. "
                "Provision its receipt and ledger-backup folders next."
            )
        )
    )
    st.cache_resource.clear()
    st.rerun()
    return True


def _create_storage_root(
    profile_key: str,
    profile_name: str,
    drive_settings: Mapping[str, Any],
    *,
    client: GoogleDriveClient,
    store_path: Path,
) -> bool:
    root = client.create_storage_root(
        profile_key,
        f"{profile_name} Storage",
    )
    local_profiles = load_local_profiles(store_path)
    full_root = client.folder(str(root.get("id", "")))
    _ensure_storage_root_is_not_directly_nested(
        profile_key,
        full_root,
        local_profiles,
    )
    updated = dict(drive_settings)
    updated["storage_root"] = {
        "id": str(full_root["id"]),
        "name": str(full_root["name"]),
    }
    updated.pop("receipt_folder", None)
    updated.pop("backup_folder", None)
    update_local_profile_google_drive(profile_key, updated, path=store_path)
    st.session_state["google_drive_admin_notice"] = (
        f"Google Drive storage root created for profile '{profile_key}'. "
        "Provision its receipt and ledger-backup folders next."
    )
    st.cache_resource.clear()
    st.rerun()
    return True


def _provision_storage_children(
    profile_key: str,
    drive_settings: Mapping[str, Any],
    *,
    client: GoogleDriveClient,
    store_path: Path,
) -> bool:
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
    st.session_state["google_drive_admin_notice"] = (
        f"Google Drive receipt and ledger-backup folders are ready for "
        f"'{profile_key}'."
    )
    st.cache_resource.clear()
    st.rerun()
    return True


def select_profile_ledger(
    app_secrets: Mapping[str, Any],
    profile_key: str,
    selected: Mapping[str, Any],
    *,
    store_path: Path = LOCAL_PROFILE_STORE_PATH,
    persist: bool = True,
) -> None:
    """Validate the selected file with fresh profile OAuth before saving its ID.

    Opening alone allows historical workbooks to be selected for the explicit
    migration command. Validation/blank initialization is a separate action.
    """
    profiles = load_local_profiles(store_path)
    profile = profiles[profile_key]
    spreadsheet_id = parse_spreadsheet_id(str(selected.get("id") or ""))
    candidate = {**profile, "spreadsheet_id": spreadsheet_id,
                 "google_drive_oauth": app_secrets.get("google_drive_oauth")}
    GoogleSheetsStorage._open_workbook_from_secrets(candidate, scopes=[])
    # Sheets opening confirms read access; Drive capabilities confirms Editor access.
    client = GoogleDriveClient(load_oauth_config(app_secrets), profile["google_drive"])
    from tabula_rasa.google_drive.errors import DRIVE_API_ROOT, _json_response

    metadata = _json_response(client._request("GET", f"{DRIVE_API_ROOT}/files/{spreadsheet_id}", params={
        "fields": "id,mimeType,capabilities(canEdit)", "supportsAllDrives": "true"
    }))
    capabilities = metadata.get("capabilities")
    if (metadata.get("mimeType") != "application/vnd.google-apps.spreadsheet"
            or not isinstance(capabilities, Mapping) or not capabilities.get("canEdit")):
        raise GoogleDriveAccessError("Select a Google Sheets workbook with Editor access.")
    if not persist:
        return
    update_local_profile(profile_key, name=str(profile["name"]),
                         spreadsheet=spreadsheet_id,
                         backup_folder_path=profile.get("backup_folder_path"), path=store_path)


def provision_profile_ledger(
    app_secrets: Mapping[str, Any], profile_key: str, *,
    store_path: Path = LOCAL_PROFILE_STORE_PATH,
) -> None:
    """Provision an initial ledger, saving its ID before recoverable initialization.

    A partial workbook is retained in Drive and can be repaired with Validate
    ledger. A configured ledger is never replaced by this initial setup action.
    """
    profile = load_local_profiles(store_path)[profile_key]
    if profile.get("spreadsheet_id"):
        raise LocalProfileStoreError("This profile already has a ledger. Use Picker to change it.")
    client = GoogleDriveClient(load_oauth_config(app_secrets), profile.get("google_drive", {}))
    created = client.create_ledger_spreadsheet(profile_key, f"{profile['name']} Ledger")
    update_local_profile(profile_key, name=str(profile["name"]), spreadsheet=created["id"],
                         backup_folder_path=profile.get("backup_folder_path"), path=store_path)
    initialize_candidate_storage({**profile, "spreadsheet_id": created["id"],
                                  "google_drive_oauth": app_secrets.get("google_drive_oauth")})


def render_google_drive_admin(
    app_secrets: Mapping[str, Any],
    local_profiles: Mapping[str, Mapping[str, Any]],
    *,
    current_url: str,
    selected_profile_key: str | None = None,
    store_path: Path = LOCAL_PROFILE_STORE_PATH,
) -> None:
    """Render connection, folder selection, test, and disconnect controls."""
    st.divider()
    st.subheader("Google setup: Sheets and Drive")
    st.write(
        "Connect the human Google account with Editor access to the ledger and Drive files. "
        "The OAuth client is shared, while refresh tokens, storage roots, and "
        "child folders remain isolated by business profile."
    )

    notice = st.session_state.pop("google_drive_admin_notice", None)
    if notice:
        st.success(notice)
    callback_error = st.session_state.pop("google_drive_admin_error", None)
    if callback_error:
        st.error(callback_error)

    try:
        config = load_oauth_config(app_secrets)
        redirect_uri = select_redirect_uri(config, current_url)
    except GoogleDriveConfigurationError as exc:
        st.warning(str(exc))
        st.code(
            '[google_drive_oauth]\n'
            'client_id = "…apps.googleusercontent.com"\n'
            'client_secret = "…"\n'
            'redirect_uris = [\n'
            '  "http://localhost:8501/",\n'
            '  "http://127.0.0.1:8501/",\n'
            "]\n"
            'picker_api_key = "…"\n'
            'project_number = "…"\n',
            language="toml",
        )
        return

    if not local_profiles:
        st.info("Add a local business profile before connecting Google Drive.")
        return

    profile_key = selected_profile_key
    if profile_key is None:
        profile_key = st.selectbox(
            "Business profile to connect",
            list(local_profiles),
            format_func=lambda key: str(local_profiles[key].get("name", key)),
            key="google_drive_profile_key",
        )
    if profile_key not in local_profiles:
        st.info("Select a local business profile before configuring Google Drive.")
        return
    profile = local_profiles[profile_key]
    drive_settings = profile.get("google_drive")
    if not isinstance(drive_settings, Mapping):
        st.info("Google Drive is not connected for this profile.")
        connect_url = authorization_url(
            config,
            profile_key,
            redirect_uri,
        )
        st.link_button(
            "Connect Google",
            connect_url,
            type="primary",
            help=(
                "Google will return to this app after you approve narrow "
                "Drive file access and offline access."
            ),
        )
        st.caption(f"OAuth callback: `{redirect_uri}`")
        return

    authorized_email = str(drive_settings.get("authorized_email", "")).strip()
    authorized_name = str(drive_settings.get("authorized_name", "")).strip()
    identity = authorized_email or authorized_name or "Connected Google account"
    st.success(f"Connected as {identity}.")

    try:
        client = _cached_drive_client(
            config,
            profile_key,
            json.dumps(dict(drive_settings), sort_keys=True),
        )
        access_token = client.access_token()
    except GoogleDriveError as exc:
        st.error(str(exc))
        reconnect_url = authorization_url(
            config,
            profile_key,
            redirect_uri,
            login_hint=authorized_email,
        )
        st.link_button("Reconnect Google", reconnect_url, type="primary")
        remove_invalid = st.checkbox(
            "Remove the saved invalid Google Drive connection",
            key=f"remove-invalid-google-drive-{profile_key}",
        )
        remove_connection_error = st.empty()
        if st.button(
            "Remove saved connection",
            disabled=not remove_invalid,
            key=f"remove-invalid-google-drive-button-{profile_key}",
        ):
            try:
                update_local_profile_google_drive(profile_key, None, path=store_path)
            except LocalProfileStoreError as remove_error:
                remove_connection_error.error(str(remove_error))
            else:
                st.cache_resource.clear()
                st.session_state["google_drive_admin_notice"] = (
                    f"Saved Google Drive connection removed for profile '{profile_key}'."
                )
                st.rerun()
        return

    st.markdown("**Ledger spreadsheet**")
    st.caption("Select the existing ledger with Google Picker to grant this application file access.")
    if profile.get("spreadsheet_id"):
        st.caption("Configured ledger: …" + str(profile["spreadsheet_id"])[-8:])
    if config.picker_ready:
        selected_ledger = spreadsheet_picker(
            access_token=access_token,
            api_key=config.picker_api_key,
            project_number=config.project_number,
            key=f"ledger-picker-{profile_key}",
        )
        if selected_ledger:
            try:
                select_profile_ledger(
                    app_secrets, profile_key, selected_ledger, store_path=store_path
                )
            except LocalProfileStoreError as exc:
                st.error(str(exc))
            except (StorageError, GoogleDriveError):
                st.error("The Google account cannot select this ledger. Confirm Editor access and retry Picker.")
            else:
                st.cache_resource.clear()
                st.session_state["google_drive_admin_notice"] = "Ledger selected. Validate the ledger next."
                st.rerun()
    if not profile.get("spreadsheet_id"):
        st.caption("For a new business, create a ledger in this Google account's My Drive with the required worksheets and default accounts/categories.")
        provision_blocked = f"ledger-create-uncertain:{profile_key}"
        if st.session_state.get(provision_blocked):
            st.warning("Creation is paused after an uncertain response. Inspect Google Drive and use Picker to select any created ledger.")
        if st.button("Create ledger spreadsheet", key=f"create-ledger-{profile_key}",
                     disabled=bool(st.session_state.get(provision_blocked))):
            try:
                provision_profile_ledger(app_secrets, profile_key, store_path=store_path)
            except (StorageError, GoogleDriveError, LocalProfileStoreError) as exc:
                if isinstance(exc, GoogleDriveAccessError) and exc.upload_attempted:
                    st.session_state[provision_blocked] = True
                st.cache_resource.clear()
                st.session_state["google_drive_admin_error"] = (
                    "Ledger setup did not finish. Inspect Google Drive and select any created ledger through Picker; if already selected, use Validate ledger. No files were deleted."
                )
                st.rerun()
            else:
                st.cache_resource.clear()
                st.session_state["google_drive_admin_notice"] = "Ledger spreadsheet created and initialized. Configure storage folders or run readiness next."
                st.rerun()
    with st.expander("Authorize a recovery or migration workbook"):
        st.caption("Use Picker to grant access to a candidate workbook without changing the active ledger. Use its ID in the recovery or migration command.")
        if config.picker_ready:
            candidate_selection = spreadsheet_picker(
                access_token=access_token, api_key=config.picker_api_key,
                project_number=config.project_number, key=f"candidate-picker-{profile_key}",
            )
            if candidate_selection:
                try:
                    select_profile_ledger(app_secrets, profile_key, candidate_selection,
                                          store_path=store_path, persist=False)
                except (StorageError, GoogleDriveError, LocalProfileStoreError):
                    st.error("Candidate access failed. Confirm Google account Editor access and retry Picker.")
                else:
                    st.success("Candidate workbook authorized; active ledger unchanged.")
    if st.button("Validate ledger", key=f"validate-ledger-{profile_key}", disabled=not profile.get("spreadsheet_id")):
        try:
            initialize_candidate_storage({**profile, "google_drive_oauth": app_secrets.get("google_drive_oauth")})
        except StorageError as exc:
            st.error(str(exc))
        else:
            st.success("Ledger schema and defaults validated.")

    if not config.picker_ready:
        st.warning(
            "Add picker_api_key and project_number to google_drive_oauth before "
            "choosing folders."
        )
    else:
        storage_root = drive_settings.get("storage_root")
        receipt_folder = drive_settings.get("receipt_folder")
        backup_folder = drive_settings.get("backup_folder")
        st.markdown("**Profile storage root**")
        if isinstance(storage_root, Mapping):
            st.caption(_folder_display_name(storage_root))
        storage_root_error = st.empty()
        selected_root = folder_picker(
            access_token=access_token,
            api_key=config.picker_api_key,
            project_number=config.project_number,
            key=f"storage-root-{profile_key}",
        )
        if selected_root is not None:
            try:
                local_profiles = load_local_profiles(store_path)
                folder = client.folder(str(selected_root.get("id", "")))
                _ensure_storage_root_is_not_directly_nested(
                    profile_key,
                    folder,
                    local_profiles,
                )
                _save_storage_root(
                    profile_key,
                    drive_settings,
                    selected_root,
                    client=client,
                    store_path=store_path,
                    folder=folder,
                )
            except (GoogleDriveError, LocalProfileStoreError) as exc:
                storage_root_error.error(str(exc))
        if not isinstance(storage_root, Mapping):
            create_root_error = st.empty()
            if st.button(
                "Create storage root",
                key=f"create-storage-root-{profile_key}",
            ):
                try:
                    _create_storage_root(
                        profile_key,
                        str(profile.get("name", profile_key)),
                        drive_settings,
                        client=client,
                        store_path=store_path,
                    )
                except (GoogleDriveError, LocalProfileStoreError) as exc:
                    create_root_error.error(str(exc))
        if isinstance(storage_root, Mapping):
            if isinstance(receipt_folder, Mapping):
                st.caption(
                    f"Receipts: {_folder_display_name(receipt_folder, 'Provisioned')}"
                )
            if isinstance(backup_folder, Mapping):
                st.caption(
                    "Ledger backups: "
                    f"{_folder_display_name(backup_folder, 'Provisioned')}"
                )
            provision_error = st.empty()
            if st.button(
                "Provision storage folders",
                type="primary",
                key=f"provision-storage-folders-{profile_key}",
            ):
                try:
                    _provision_storage_children(
                        profile_key,
                        drive_settings,
                        client=client,
                        store_path=store_path,
                    )
                except (GoogleDriveError, LocalProfileStoreError) as exc:
                    provision_error.error(str(exc))

    st.divider()
    reconnect_url = authorization_url(
        config,
        profile_key,
        redirect_uri,
        login_hint=authorized_email,
    )
    st.link_button("Reconnect Google", reconnect_url)
    confirm_disconnect = st.checkbox(
        "Disconnect this profile from Sheets and Drive and remove folder selections",
        key=f"disconnect-google-drive-{profile_key}",
    )
    disconnect_error = st.empty()
    if st.button(
        "Disconnect Google",
        disabled=not confirm_disconnect,
        key=f"disconnect-google-drive-button-{profile_key}",
    ):
        try:
            ensure_google_drive_disconnect_allowed(profile)
            # Google revocation can invalidate other grants for the same account/client.
            # Local disconnection must remain scoped to this profile.
            update_local_profile_google_drive(profile_key, None, path=store_path)
        except (GoogleDriveError, LocalProfileStoreError) as exc:
            disconnect_error.error(str(exc))
        else:
            st.cache_resource.clear()
            st.session_state["google_drive_admin_notice"] = (
                f"Google Drive was disconnected for profile '{profile_key}'."
            )
            st.rerun()
