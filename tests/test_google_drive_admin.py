from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

import tabula_rasa.google_drive_admin as google_drive_admin
from tabula_rasa.google_drive import (
    GoogleDriveAccessError,
    GoogleDriveConfigurationError,
)
from tabula_rasa.google_drive_admin import (
    _folder_display_name,
    _ensure_storage_root_is_not_directly_nested,
    _create_storage_root,
    _provision_storage_children,
    _revalidated_storage_settings,
    _save_storage_root,
    process_google_drive_oauth_callback,
    validate_google_drive_profile,
)


class FolderClient:
    def __init__(self, folders: dict[str, dict[str, Any]]) -> None:
        self.folders = folders

    def folder(self, folder_id: str) -> dict[str, Any]:
        if folder_id not in self.folders:
            raise GoogleDriveAccessError("folder unavailable")
        return self.folders[folder_id]


def test_readiness_rechecks_direct_profile_root_conflict() -> None:
    client = Mock()
    client.validate_storage_configuration.return_value = {
        "storage_root": {"id": "northroot123", "name": "North Storage"}
    }
    client.folder.return_value = {
        "id": "northroot123",
        "name": "North Storage",
        "parents": [],
    }
    drive_settings = {"storage_root": {"id": "northroot123"}}
    profiles = {"north": {"google_drive": drive_settings}}

    with patch(
        "tabula_rasa.google_drive_admin.load_oauth_config",
        return_value=Mock(),
    ), patch(
        "tabula_rasa.google_drive_admin._cached_drive_client",
        return_value=client,
    ), patch(
        "tabula_rasa.google_drive_admin._ensure_storage_root_is_not_directly_nested"
    ) as ensure_direct:
        validate_google_drive_profile(
            {},
            "north",
            drive_settings,
            local_profiles=profiles,
        )

    ensure_direct.assert_called_once_with(
        "north",
        client.folder.return_value,
        profiles,
    )
    client.validate_storage_configuration.assert_called_once_with(
        "north",
        root_folder=client.folder.return_value,
    )


def test_oauth_callback_persists_profile_grant_and_clears_query(
    tmp_path: Path,
) -> None:
    config = Mock()
    drive_settings = {
        "authorized_email": "owner@example.test",
        "refresh_token": "refresh-token",
    }
    with (
        patch.object(
            google_drive_admin.st,
            "query_params",
            {"state": "signed-state", "code": "authorization-code"},
        ),
        patch.object(
            google_drive_admin,
            "load_oauth_config",
            return_value=config,
        ),
        patch.object(
            google_drive_admin,
            "verify_oauth_state",
            return_value={"profile": "north", "redirect_uri": "http://localhost"},
        ),
        patch.object(
            google_drive_admin,
            "load_local_profiles",
            return_value={"north": {"name": "North Shop"}},
        ),
        patch.object(
            google_drive_admin,
            "exchange_authorization_code",
            return_value=drive_settings,
        ),
        patch.object(google_drive_admin, "update_local_profile_google_drive") as update,
        patch.object(google_drive_admin.st, "rerun"),
    ):
        assert process_google_drive_oauth_callback(
            {"google_drive_oauth": {}},
            store_path=tmp_path / "profiles.json",
        )

    update.assert_called_once_with(
        "north",
        drive_settings,
        path=tmp_path / "profiles.json",
    )
    assert not google_drive_admin.st.query_params


def test_missing_oauth_configuration_shows_loopback_setup_example() -> None:
    with patch.object(google_drive_admin, "st") as streamlit:
        google_drive_admin.render_google_drive_admin(
            {}, {}, current_url="http://localhost:8501/"
        )

    streamlit.warning.assert_called_once()
    streamlit.code.assert_called_once()
    example = tomllib.loads(streamlit.code.call_args.args[0])
    assert example["google_drive_oauth"]["redirect_uris"] == [
        "http://localhost:8501/",
        "http://127.0.0.1:8501/",
    ]


def test_drive_validation_uses_only_visible_parent_ids() -> None:
    client = Mock()
    client.folder.return_value = {
        "id": "northroot123",
        "name": "North Storage",
        "parents": ["unreadableparent123"],
    }
    drive_settings = {"storage_root": {"id": "northroot123"}}
    profiles = {
        "north": {"google_drive": drive_settings},
        "south": {
            "google_drive": {"storage_root": {"id": "southroot123"}}
        },
    }

    with patch(
        "tabula_rasa.google_drive_admin.load_oauth_config",
        return_value=Mock(),
    ), patch(
        "tabula_rasa.google_drive_admin._cached_drive_client",
        return_value=client,
    ), patch(
        "tabula_rasa.google_drive_admin._ensure_storage_root_is_not_directly_nested"
    ) as ensure_direct:
        validate_google_drive_profile(
            {},
            "north",
            drive_settings,
            local_profiles=profiles,
        )

    ensure_direct.assert_called_once_with(
        "north",
        client.folder.return_value,
        profiles,
    )
    client.validate_storage_configuration.assert_called_once_with(
        "north",
        root_folder=client.folder.return_value,
    )


def test_drive_validation_rejects_direct_known_root_parent() -> None:
    client = Mock()
    client.folder.return_value = {
        "id": "northroot123",
        "name": "North Storage",
        "parents": ["southroot123"],
    }
    drive_settings = {"storage_root": {"id": "northroot123"}}
    profiles = {
        "north": {"google_drive": drive_settings},
        "south": {
            "google_drive": {"storage_root": {"id": "southroot123"}}
        },
    }

    with patch(
        "tabula_rasa.google_drive_admin.load_oauth_config",
        return_value=Mock(),
    ), patch(
        "tabula_rasa.google_drive_admin._cached_drive_client",
        return_value=client,
    ), pytest.raises(GoogleDriveConfigurationError, match="cannot be nested"):
        validate_google_drive_profile(
            {},
            "north",
            drive_settings,
            local_profiles=profiles,
        )


def managed_folder(
    folder_id: str,
    name: str,
    *,
    kind: str,
    profile_key: str = "north",
    root_id: str | None = None,
) -> dict[str, Any]:
    properties = {
        "tabulaRasaKind": kind,
        "tabulaRasaProfile": profile_key,
    }
    folder: dict[str, Any] = {
        "id": folder_id,
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "trashed": False,
        "appProperties": properties,
    }
    if root_id is not None:
        properties["tabulaRasaStorageRoot"] = root_id
        folder["parents"] = [root_id]
    return folder


def test_reconnect_preserves_only_valid_managed_root_and_children() -> None:
    root_id = "rootfolder123"
    folders = {
        root_id: managed_folder(root_id, "North Storage", kind="storage-root"),
        "receiptchild123": managed_folder(
            "receiptchild123",
            "Receipts",
            kind="receipts",
            root_id=root_id,
        ),
    }
    client = FolderClient(folders)
    previous = {
        "storage_root": {"id": root_id, "name": "Old Root Name"},
        "receipt_folder": {"id": "receiptchild123", "name": "Old Receipts"},
        "backup_folder": {"id": "missingchild123", "name": "Old Backups"},
    }

    preserved = _revalidated_storage_settings(client, "north", previous)

    assert preserved == {
        "storage_root": {"id": root_id, "name": "North Storage"},
        "receipt_folder": {"id": "receiptchild123", "name": "Receipts"},
    }


@pytest.mark.parametrize(
    ("root_kind", "root_profile"),
    [("receipts", "north"), ("storage-root", "south")],
)
def test_reconnect_drops_root_with_mismatched_identity(
    root_kind: str, root_profile: str
) -> None:
    root_id = "rootfolder123"
    client = FolderClient(
        {root_id: managed_folder(root_id, "Other Storage", kind=root_kind, profile_key=root_profile)}
    )

    preserved = _revalidated_storage_settings(
        client,
        "north",
        {"storage_root": {"id": root_id, "name": "North Storage"}},
    )

    assert preserved == {}


def test_reconnect_drops_managed_child_with_missing_parents_safely() -> None:
    root_id = "rootfolder123"
    child = managed_folder(
        "receiptchild123",
        "Receipts",
        kind="receipts",
        root_id=root_id,
    )
    child["parents"] = None
    client = FolderClient(
        {
            root_id: managed_folder(root_id, "North Storage", kind="storage-root"),
            "receiptchild123": child,
        }
    )

    preserved = _revalidated_storage_settings(
        client,
        "north",
        {
            "storage_root": {"id": root_id, "name": "North Storage"},
            "receipt_folder": {"id": "receiptchild123", "name": "Receipts"},
        },
    )

    assert preserved == {
        "storage_root": {"id": root_id, "name": "North Storage"}
    }


def test_save_storage_root_refreshes_name_when_id_is_unchanged(tmp_path: Path) -> None:
    root_id = "rootfolder123"
    client = FolderClient(
        {root_id: managed_folder(root_id, "Tabula Rasa Data", kind="storage-root")}
    )
    drive_settings = {
        "storage_root": {"id": root_id, "name": "Tabula Rosa Data"},
        "receipt_folder": {"id": "receiptchild123", "name": "Receipts"},
        "backup_folder": {"id": "backupchild123", "name": "Ledger Backups"},
    }

    with (
        patch.object(google_drive_admin, "update_local_profile_google_drive") as update,
        patch.object(google_drive_admin.st.cache_resource, "clear"),
        patch.object(google_drive_admin.st, "rerun"),
    ):
        assert _save_storage_root(
            "north",
            drive_settings,
            {"id": root_id, "name": "Tabula Rasa Data"},
            client=client,
            store_path=tmp_path / "profiles.json",
        )

    update.assert_called_once_with(
        "north",
        {
            "storage_root": {"id": root_id, "name": "Tabula Rasa Data"},
            "receipt_folder": {"id": "receiptchild123", "name": "Receipts"},
            "backup_folder": {"id": "backupchild123", "name": "Ledger Backups"},
        },
        path=tmp_path / "profiles.json",
    )
    assert "name refreshed" in google_drive_admin.st.session_state[
        "google_drive_admin_notice"
    ]


def test_save_storage_root_handles_missing_folder_name_without_key_error(
    tmp_path: Path,
) -> None:
    root_id = "rootfolder123"
    client = FolderClient({root_id: {"id": root_id}})

    with patch.object(google_drive_admin, "update_local_profile_google_drive") as update:
        with pytest.raises(GoogleDriveAccessError, match="folder details"):
            _save_storage_root(
                "north",
                {},
                {"id": root_id},
                client=client,
                store_path=tmp_path / "profiles.json",
            )

    update.assert_not_called()


def test_save_storage_root_handles_non_mapping_folder_response(
    tmp_path: Path,
) -> None:
    root_id = "rootfolder123"
    client = FolderClient({root_id: [root_id]})

    with patch.object(google_drive_admin, "update_local_profile_google_drive") as update:
        with pytest.raises(GoogleDriveAccessError, match="folder details"):
            _save_storage_root(
                "north",
                {},
                {"id": root_id},
                client=client,
                store_path=tmp_path / "profiles.json",
            )

    update.assert_not_called()


def test_storage_root_rejects_exact_profile_root_reuse() -> None:
    selected_root = {"id": "northroot123", "parents": []}
    profiles = {
        "north": {"google_drive": {"storage_root": {"id": "northroot123"}}},
        "south": {"google_drive": {"storage_root": {"id": "northroot123"}}},
    }

    with pytest.raises(GoogleDriveConfigurationError, match="cannot be nested"):
        _ensure_storage_root_is_not_directly_nested("south", selected_root, profiles)


def test_storage_root_rejects_non_mapping_folder_response() -> None:
    with pytest.raises(GoogleDriveConfigurationError, match="folder details"):
        _ensure_storage_root_is_not_directly_nested("north", [], {})  # type: ignore[arg-type]


def test_storage_root_rejects_known_direct_parent_only() -> None:
    selected_root = {"id": "southroot123", "parents": ["northroot123"]}
    profiles = {
        "north": {"google_drive": {"storage_root": {"id": "northroot123"}}},
        "south": {"google_drive": {"storage_root": {"id": "southroot123"}}},
    }

    with pytest.raises(GoogleDriveConfigurationError, match="cannot be nested"):
        _ensure_storage_root_is_not_directly_nested("south", selected_root, profiles)


def test_storage_root_check_does_not_read_parent_folders() -> None:
    selected_root = {"id": "northroot123", "parents": ["unreadableparent123"]}
    profiles = {
        "north": {"google_drive": {"storage_root": {"id": "northroot123"}}},
        "south": {"google_drive": {"storage_root": {"id": "southroot123"}}},
    }

    _ensure_storage_root_is_not_directly_nested("north", selected_root, profiles)


def test_create_storage_root_checks_drive_root_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    north_root = managed_folder("northroot123", "North Storage", kind="storage-root")
    south_root = managed_folder(
        "southroot123",
        "South Storage",
        kind="storage-root",
        root_id="northroot123",
    )

    class CreateClient(FolderClient):
        def create_storage_root(self, profile_key: str, name: str) -> dict[str, str]:
            assert profile_key == "south"
            assert name == "South Storage"
            return {"id": "southroot123", "name": "South Storage"}

    client = CreateClient(
        {"northroot123": north_root, "southroot123": south_root}
    )
    monkeypatch.setattr(
        google_drive_admin,
        "load_local_profiles",
        lambda _path: {
            "north": {"google_drive": {"storage_root": {"id": "northroot123"}}},
            "south": {"google_drive": {}},
        },
    )

    with pytest.raises(GoogleDriveConfigurationError, match="cannot be nested"):
        _create_storage_root(
            "south",
            "South",
            {"refresh_token": "token"},
            client=client,
            store_path=Path("unused.json"),
        )


def test_provisioning_rejects_a_storage_root_without_an_id() -> None:
    with pytest.raises(GoogleDriveConfigurationError, match="folder ID"):
        _provision_storage_children(
            "north",
            {"storage_root": {}},
            client=FolderClient({}),
            store_path=Path("unused.json"),
        )


@pytest.mark.parametrize(
    ("folder", "fallback", "expected"),
    (({"name": None}, "", ""), ({"name": None}, "Provisioned", "Provisioned")),
)
def test_folder_display_name_handles_null_names(
    folder: dict[str, Any],
    fallback: str,
    expected: str,
) -> None:
    assert _folder_display_name(folder, fallback) == expected
