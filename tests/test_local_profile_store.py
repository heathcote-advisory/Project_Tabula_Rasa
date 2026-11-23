from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tabula_rasa.business_profiles import load_business_profiles
from tabula_rasa.local_profile_store import (
    LocalProfileStoreError,
    build_profile_settings,
    load_local_profiles,
    merged_profile_secrets,
    save_local_profile,
    update_local_profile,
    update_local_profile_google_drive,
    update_local_profile_synology_recovery,
)




def settings(
    name: str = "North Shop", *, spreadsheet_id: str = "abcdefghij1234567890"
) -> dict:
    return build_profile_settings(
        name,
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        str(Path(tempfile.gettempdir()) / "Tabula Rasa Test Backups" / "north"),
        google_drive={"authorized_email": "owner@example.test", "refresh_token": "fake-refresh", "token_uri": "https://oauth2.googleapis.com/token", "scopes": ["https://www.googleapis.com/auth/drive.file"]},
    )


class TestLocalProfileStore:
    def test_blank_backup_path_is_not_stored(self) -> None:
        configured = build_profile_settings(
            "Cloud Shop",
            "abcdefghij1234567890",
                "   ",
        )

        assert "backup_folder_path" not in configured

    def test_blank_stored_backup_path_migrates_without_manual_editing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "business_profiles": {
                            "north": {
                                **settings(),
                                "backup_folder_path": "",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            stored = load_local_profiles(path)

        assert "backup_folder_path" not in stored["north"]

    def test_atomically_saves_and_loads_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"

            save_local_profile("north", settings(), path=path)

            stored = load_local_profiles(path)
            assert stored["north"]["name"] == "North Shop"
            assert (
                stored["north"]["google_drive"]["authorized_email"]
                == "owner@example.test"
            )
            assert "credential-content-must-not-leak" not in path.read_text(
                encoding="utf-8"
            )

    def test_requires_explicit_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)

            with pytest.raises(LocalProfileStoreError):
                save_local_profile("north", settings("Replacement"), path=path)

            save_local_profile(
                "north",
                settings("Replacement"),
                replace=True,
                path=path,
            )
            assert load_local_profiles(path)["north"]["name"] == "Replacement"

    def test_ordinary_edit_preserves_credentials_and_profile_scoped_metadata(self) -> None:
        drive = {
            "authorized_email": "north@example.test",
            "authorized_name": "North Owner",
            "refresh_token": "durable-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/drive.file"],
            "storage_root": {"id": "rootfolder123", "name": "North Storage"},
            "receipt_folder": {"id": "receiptfolder123", "name": "Receipts"},
            "backup_folder": {"id": "backupfolder123", "name": "Backups"},
        }
        recovery = {
            "layout": "profile-root",
            "destination_path": "/volume1/Backups/TabulaRasa/north",
            "schedule": "Continuous",
            "retention_policy": "Retain until manual review.",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            update_local_profile_google_drive("north", drive, path=path)
            update_local_profile_synology_recovery("north", recovery, path=path)
            before = load_local_profiles(path)["north"]

            update_local_profile(
                "north",
                name="North Shop Renamed",
                spreadsheet="newspreadsheet123456789",
                backup_folder_path="",
                path=path,
            )
            after = load_local_profiles(path)["north"]

        assert after["name"] == "North Shop Renamed"
        assert after["spreadsheet_id"] == "newspreadsheet123456789"
        assert "backup_folder_path" not in after
        assert after["google_drive"] == before["google_drive"]
        assert after["google_drive"] == before["google_drive"]
        assert after["synology_recovery"] == before["synology_recovery"]

    def test_failed_edit_leaves_the_complete_profile_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            before = load_local_profiles(path)["north"]

            with pytest.raises(LocalProfileStoreError):
                update_local_profile(
                    "north",
                    name="North Changed",
                    spreadsheet="bad",
                    backup_folder_path="",
                    path=path,
                )

            assert load_local_profiles(path)["north"] == before


    def test_edit_isolated_to_the_selected_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            save_local_profile("south", settings("South Shop", spreadsheet_id="south-ledger123"), path=path)

            update_local_profile(
                "north",
                name="North Shop Updated",
                spreadsheet="northspreadsheet123456789",
                backup_folder_path="",
                path=path,
            )
            stored = load_local_profiles(path)

        assert stored["north"]["name"] == "North Shop Updated"
        assert stored["south"]["name"] == "South Shop"
        assert stored["south"]["backup_folder_path"] == settings("South Shop", spreadsheet_id="south-ledger123")[
            "backup_folder_path"
        ]

    def test_json_profiles_are_merged_with_app_secrets(self) -> None:
        base = {"app_password_hash": "hash"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)

            merged = merged_profile_secrets(base, path=path)
            profiles = load_business_profiles(merged)

        assert [profile.key for profile in profiles] == ["north"]
        assert merged["business_profiles"]["north"]["name"] == "North Shop"

    def test_refuses_corrupt_store_instead_of_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            path.write_text('{"private_key": "do-not-overwrite"}', encoding="utf-8")

            with pytest.raises(LocalProfileStoreError):
                save_local_profile("north", settings(), path=path)

            assert "do-not-overwrite" in path.read_text(encoding="utf-8")

    def test_persists_and_disconnects_profile_scoped_drive_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            drive = {
                "authorized_email": "north@example.test",
                "authorized_name": "North Owner",
                "refresh_token": "durable-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/drive.file"],
                "storage_root": {"id": "rootfolder123", "name": "North Storage"},
                "receipt_folder": {
                    "id": "receiptfolder123",
                    "name": "Receipts",
                },
            }

            update_local_profile_google_drive("north", drive, path=path)
            stored = load_local_profiles(path)
            assert stored["north"]["google_drive"] == drive

            update_local_profile_google_drive("north", None, path=path)
            assert "google_drive" not in load_local_profiles(path)["north"]

    def test_storage_root_is_validated_and_cannot_be_shared(self) -> None:
        north_drive = {
            "authorized_email": "north@example.test",
            "authorized_name": "North Owner",
            "refresh_token": "north-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/drive.file"],
            "storage_root": {"id": "rootfolder123", "name": "North Storage"},
        }
        south_drive = {
            "authorized_email": "south@example.test",
            "authorized_name": "South Owner",
            "refresh_token": "south-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/drive.file"],
            "storage_root": {"id": "rootfolder123", "name": "North Storage"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            save_local_profile("south", settings("South Shop", spreadsheet_id="south-ledger123"), path=path)
            update_local_profile_google_drive("north", north_drive, path=path)

            with pytest.raises(LocalProfileStoreError, match="only one"):
                update_local_profile_google_drive("south", south_drive, path=path)

            stored = load_local_profiles(path)

        assert stored["north"]["google_drive"]["storage_root"]["id"] == (
            "rootfolder123"
        )
        assert "storage_root" not in stored["south"]["google_drive"]

    def test_synology_recovery_is_optional_and_profile_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            drive = {
                "authorized_email": "north@example.test",
                "authorized_name": "North Owner",
                "refresh_token": "durable-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/drive.file"],
                "storage_root": {"id": "rootfolder123", "name": "North Storage"},
                "receipt_folder": {
                    "id": "receiptfolder123",
                    "name": "Receipts",
                },
                "backup_folder": {
                    "id": "backupfolder123",
                    "name": "North Backups",
                },
            }
            update_local_profile_google_drive("north", drive, path=path)
            recovery = {
                "layout": "profile-root",
                "destination_path": "/volume1/Backups/TabulaRasa/north",
                "schedule": "Continuous",
                "retention_policy": "Retain until manual review.",
            }

            update_local_profile_synology_recovery("north", recovery, path=path)
            assert load_local_profiles(path)["north"]["synology_recovery"] == recovery

            update_local_profile_synology_recovery("north", None, path=path)
            assert "synology_recovery" not in load_local_profiles(path)["north"]

    def test_synology_recovery_rejects_retired_three_field_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)

            with pytest.raises(
                LocalProfileStoreError,
                match="retired three-field schema",
            ):
                update_local_profile_synology_recovery(
                    "north",
                    {
                        "destination_path": "/volume1/Backups/north",
                        "schedule": "Continuous",
                        "retention_policy": "Keep all.",
                    },
                    path=path,
                )

    def test_profile_root_synology_requires_complete_root_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            update_local_profile_google_drive(
                "north",
                {
                    "authorized_email": "north@example.test",
                    "authorized_name": "North Owner",
                    "refresh_token": "durable-refresh-token",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "scopes": ["https://www.googleapis.com/auth/drive.file"],
                    "storage_root": {
                        "id": "rootfolder123",
                        "name": "North Storage",
                    },
                    "backup_folder": {
                        "id": "backupfolder123",
                        "name": "North Backups",
                    },
                },
                path=path,
            )

            with pytest.raises(LocalProfileStoreError, match="storage root"):
                update_local_profile_synology_recovery(
                    "north",
                    {
                        "layout": "profile-root",
                        "destination_path": "/volume1/Backups/north",
                        "schedule": "Continuous",
                        "retention_policy": "Keep all.",
                    },
                    path=path,
                )

    def test_profile_root_synology_blocks_drive_root_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            drive = {
                "authorized_email": "north@example.test",
                "authorized_name": "North Owner",
                "refresh_token": "durable-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/drive.file"],
                "storage_root": {"id": "rootfolder123", "name": "North Storage"},
                "receipt_folder": {"id": "receiptfolder123", "name": "Receipts"},
                "backup_folder": {"id": "backupfolder123", "name": "Backups"},
            }
            update_local_profile_google_drive("north", drive, path=path)
            update_local_profile_synology_recovery(
                "north",
                {
                    "layout": "profile-root",
                    "destination_path": "/volume1/Backups/north",
                    "schedule": "Continuous",
                    "retention_policy": "Keep all.",
                },
                path=path,
            )

            with pytest.raises(LocalProfileStoreError, match="Disable Synology recovery"):
                update_local_profile_google_drive(
                    "north",
                    {
                        **drive,
                        "storage_root": {
                            "id": "differentroot123",
                            "name": "Different Storage",
                        },
                    },
                    path=path,
                )

            with pytest.raises(LocalProfileStoreError, match="Disable Synology recovery"):
                update_local_profile_google_drive("north", None, path=path)

            for missing_field in ("receipt_folder", "backup_folder"):
                incomplete_drive = {
                    field: value
                    for field, value in drive.items()
                    if field != missing_field
                }
                with pytest.raises(LocalProfileStoreError, match="both child folders"):
                    update_local_profile_google_drive(
                        "north",
                        incomplete_drive,
                        path=path,
                    )

            stored = load_local_profiles(path)

        assert stored["north"]["google_drive"]["storage_root"]["id"] == (
            "rootfolder123"
        )
        assert stored["north"]["synology_recovery"]["layout"] == "profile-root"

    @pytest.mark.parametrize(
        "south_destination",
        (
            "/volume1/Backups/shared",
            "/volume1/Backups/shared/south",
            "/VOLUME1/Backups/SHARED",
            "/volume1/Backups/SHARED/south",
        ),
    )
    def test_synology_destinations_cannot_share_or_overlap_between_profiles(
        self,
        south_destination: str,
    ) -> None:
        drive = {
            "authorized_email": "owner@example.test",
            "authorized_name": "Owner",
            "refresh_token": "durable-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/drive.file"],
            "storage_root": {"id": "northroot123", "name": "Storage"},
            "receipt_folder": {"id": "receiptfolder123", "name": "Receipts"},
            "backup_folder": {"id": "backupfolder123", "name": "Backups"},
        }
        recovery = {
            "layout": "profile-root",
            "destination_path": "/volume1/Backups/shared",
            "schedule": "Continuous",
            "retention_policy": "Keep all.",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_business_profiles.json"
            save_local_profile("north", settings(), path=path)
            save_local_profile("south", settings("South Shop", spreadsheet_id="south-ledger123"), path=path)
            update_local_profile_google_drive("north", drive, path=path)
            update_local_profile_synology_recovery("north", recovery, path=path)
            update_local_profile_google_drive(
                "south",
                {
                    **drive,
                    "authorized_email": "south@example.test",
                    "refresh_token": "south-refresh-token",
                    "storage_root": {
                        "id": "southroot123",
                        "name": "Storage",
                    },
                    "receipt_folder": {
                        "id": "southreceiptfolder123",
                        "name": "Receipts",
                    },
                    "backup_folder": {
                        "id": "southbackupfolder123",
                        "name": "Backups",
                    },
                },
                path=path,
            )

            with pytest.raises(
                LocalProfileStoreError,
                match="must not share or overlap",
            ):
                update_local_profile_synology_recovery(
                    "south",
                    {**recovery, "destination_path": south_destination},
                    path=path,
                )

            assert "synology_recovery" not in load_local_profiles(path)["south"]
