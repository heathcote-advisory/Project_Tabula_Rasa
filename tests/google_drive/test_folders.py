from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from tabula_rasa.google_drive import (
    GoogleDriveAccessError,
    GoogleDriveClient,
    GoogleDriveConfigurationError,
)
from tests.drive_fixtures import drive_folder

from ._support import FakeResponse, DriveSession, drive_error, drive_settings, managed_folder, oauth_config

class TestGoogleDriveFolders:
    @pytest.mark.parametrize("ids", [None, "abc", ["duplicate-id", "duplicate-id", "another-id"]])
    def test_rejects_malformed_reserved_storage_ids(self, ids):
        transport = DriveSession([FakeResponse(200, {"ids": ids})])
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=transport)
        with pytest.raises(GoogleDriveAccessError, match="did not reserve"):
            client.new_storage_ids()

    @pytest.mark.parametrize("invalid", ["trashed", "readonly", "managed", "inaccessible"])
    def test_storage_parent_requires_accessible_writable_nonmanaged_folder(self, invalid):
        parent = drive_folder("picked-parent", "Books")
        parent["capabilities"] = {"canAddChildren": invalid != "readonly"}
        parent["trashed"] = invalid == "trashed"
        if invalid == "managed":
            parent["appProperties"] = {"tabulaRasaProfile": "different-business"}
        reply = FakeResponse(404, {}) if invalid == "inaccessible" else FakeResponse(200, parent)
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=DriveSession([reply]))
        with pytest.raises(GoogleDriveAccessError):
            client.storage_parent("picked-parent")

    def test_storage_parent_resolves_my_drive_alias(self):
        folder = {**drive_folder("my-drive-root", "My Drive"), "capabilities": {"canAddChildren": True}}
        transport = DriveSession([FakeResponse(200, folder)])
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=transport)
        assert client.storage_parent("root") == "my-drive-root"
        assert transport.requests[0][1].endswith("/files/root")

    def test_selected_parent_with_reserved_id_reconciles_uncertain_creation(self):
        folder = drive_folder("reserved-folder", "Business books", parent_id="picked-parent",
                              profile_key="north", kind="storage-root")
        transport = DriveSession([FakeResponse(200, {"files": []}), FakeResponse(503, {}), FakeResponse(200, folder)])
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=transport)
        result = client.create_storage_root("north", "Business books", parent_id="picked-parent",
                                            file_id="reserved-folder")
        assert result == {"id": "reserved-folder", "name": "Business books"}
        assert [method for method, _, _ in transport.requests] == ["GET", "POST", "GET"]
        metadata = json.loads(transport.requests[1][2]["data"])
        assert metadata["id"] == "reserved-folder"
        assert metadata["parents"] == ["picked-parent"]
        assert transport.requests[-1][1].endswith("/files/reserved-folder")

    def test_freshly_validates_profile_root_and_managed_children(self) -> None:
        root = drive_folder("rootfolder123", "North Storage")
        receipt = drive_folder(
            "receiptfolder123",
            "Receipts",
            parent_id="rootfolder123",
            profile_key="north",
            kind="receipts",
            storage_root_id="rootfolder123",
        )
        backup = drive_folder(
            "backupfolder123",
            "Ledger Backups",
            parent_id="rootfolder123",
            profile_key="north",
            kind="ledger-backups",
            storage_root_id="rootfolder123",
        )
        settings = {
            **drive_settings(),
            "storage_root": {"id": root["id"], "name": root["name"]},
            "receipt_folder": {"id": receipt["id"], "name": receipt["name"]},
            "backup_folder": {"id": backup["id"], "name": backup["name"]},
        }
        client = GoogleDriveClient(
            oauth_config(),
            settings,
            session=DriveSession(
                [FakeResponse(200, root), FakeResponse(200, receipt), FakeResponse(200, backup)]
            ),
        )

        validated = client.validate_storage_configuration("north")

        assert validated["storage_root"]["id"] == "rootfolder123"
        assert validated["receipt_folder"]["id"] == "receiptfolder123"
        assert validated["backup_folder"]["id"] == "backupfolder123"


    def test_fresh_storage_validation_rejects_a_child_outside_the_root(self) -> None:
        root = drive_folder("rootfolder123", "North Storage")
        outside = drive_folder(
            "receiptfolder123",
            "Receipts",
            parent_id="different-root",
            profile_key="north",
            kind="receipts",
            storage_root_id="rootfolder123",
        )
        settings = {
            **drive_settings(),
            "storage_root": {"id": root["id"], "name": root["name"]},
            "receipt_folder": {"id": outside["id"], "name": outside["name"]},
            "backup_folder": {"id": "backupfolder123", "name": "Ledger Backups"},
        }
        client = GoogleDriveClient(
            oauth_config(),
            settings,
            session=DriveSession([FakeResponse(200, root), FakeResponse(200, outside)]),
        )

        with pytest.raises(GoogleDriveAccessError, match="outside"):
            client.validate_storage_configuration("north")


    @pytest.mark.parametrize(
        ("root_update", "message"),
        [
            ({"id": None}, "invalid"),
            ({"name": None}, "not an available folder"),
        ],
    )
    def test_fresh_storage_validation_rejects_null_root_values(
        self,
        root_update: dict[str, object],
        message: str,
    ) -> None:
        root = {
            "id": "rootfolder123",
            "name": "North Storage",
            "mimeType": "application/vnd.google-apps.folder",
            "trashed": False,
            "parents": [],
        }
        root.update(root_update)
        settings = {
            **drive_settings(),
            "storage_root": {"id": "rootfolder123", "name": "North Storage"},
            "receipt_folder": {"id": "receiptfolder123", "name": "Receipts"},
            "backup_folder": {"id": "backupfolder123", "name": "Ledger Backups"},
        }
        client = GoogleDriveClient(
            oauth_config(),
            settings,
            session=DriveSession([FakeResponse(200, root)]),
        )

        with pytest.raises(GoogleDriveAccessError, match=message):
            client.validate_storage_configuration("north")


    def test_fresh_storage_validation_rejects_null_child_id(self) -> None:
        root = {
            "id": "rootfolder123",
            "name": "North Storage",
            "mimeType": "application/vnd.google-apps.folder",
            "trashed": False,
            "parents": [],
        }
        settings = {
            **drive_settings(),
            "storage_root": {"id": "rootfolder123", "name": "North Storage"},
            "receipt_folder": {"id": "receiptfolder123", "name": "Receipts"},
            "backup_folder": {"id": "backupfolder123", "name": "Ledger Backups"},
        }
        client = GoogleDriveClient(
            oauth_config(),
            settings,
            session=DriveSession([FakeResponse(200, root)]),
        )
        client.profile_settings["receipt_folder"] = {"id": None, "name": "Receipts"}

        with pytest.raises(
            GoogleDriveConfigurationError,
            match="Provision the Receipts",
        ):
            client.validate_storage_configuration("north")


    def test_provisions_exactly_one_child_of_each_kind(self) -> None:
        root_id = "rootfolder123"
        session = DriveSession(
            [
                FakeResponse(
                    200,
                    {
                        "id": root_id,
                        "name": "North Storage",
                        "mimeType": "application/vnd.google-apps.folder",
                        "trashed": False,
                    },
                ),
                FakeResponse(200, {"files": []}),
                FakeResponse(
                    200,
                    managed_folder(
                        "receiptchild123",
                        "Receipts",
                        storage_kind="receipts",
                        root_id=root_id,
                    ),
                ),
                FakeResponse(200, {"files": []}),
                FakeResponse(
                    200,
                    managed_folder(
                        "backupchild123",
                        "Ledger Backups",
                        storage_kind="ledger-backups",
                        root_id=root_id,
                    ),
                ),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        children = client.provision_storage_children("north", root_id)

        assert children == {
            "receipts": {"id": "receiptchild123", "name": "Receipts"},
            "ledger-backups": {
                "id": "backupchild123",
                "name": "Ledger Backups",
            },
        }
        create_requests = [
            request for request in session.requests if request[0] == "POST"
        ]
        assert len(create_requests) == 2
        receipt_metadata = json.loads(create_requests[0][2]["data"])
        assert receipt_metadata["parents"] == [root_id]
        assert receipt_metadata["appProperties"]["tabulaRasaProfile"] == "north"
        assert receipt_metadata["appProperties"]["tabulaRasaKind"] == "receipts"


    def test_creates_an_app_managed_storage_root(self) -> None:
        root = managed_folder(
            "rootfolder123",
            "North Storage",
            storage_kind="storage-root",
        )
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                FakeResponse(200, root),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        created = client.create_storage_root("north", "North Storage")

        assert created == {"id": "rootfolder123", "name": "North Storage"}
        metadata = json.loads(session.requests[1][2]["data"])
        assert metadata["mimeType"] == "application/vnd.google-apps.folder"
        assert metadata["appProperties"]["tabulaRasaKind"] == "storage-root"


    def test_root_creation_does_not_repeat_after_storage_quota_failure(self) -> None:
        delays = []
        session = DriveSession([
            FakeResponse(200, {"files": []}),
            drive_error(403, "storageQuotaExceeded"),
            FakeResponse(200, {"files": []}),
        ])
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=session, sleeper=delays.append)

        with pytest.raises(GoogleDriveAccessError, match="quota or storage limit"):
            client.create_storage_root("north", "North Storage")

        assert [method for method, _, _ in session.requests] == ["GET", "POST", "GET"]
        assert delays == []

    def test_uncertain_root_creation_reuses_the_confirmed_root(self) -> None:
        root = managed_folder(
            "rootfolder123",
            "North Storage",
            storage_kind="storage-root",
        )
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                requests.ConnectionError("root response was lost"),
                FakeResponse(200, {"files": [root]}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        created = client.create_storage_root("north", "North Storage")

        assert created["id"] == "rootfolder123"
        assert [request[0] for request in session.requests].count("POST") == 1


    def test_uncertain_root_creation_delays_before_a_retry(self) -> None:
        root = managed_folder(
            "rootfolder123",
            "North Storage",
            storage_kind="storage-root",
        )
        delays: list[float] = []
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                requests.ConnectionError("root response was lost"),
                FakeResponse(200, {"files": []}),
                FakeResponse(200, root),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=delays.append,
        )

        created = client.create_storage_root("north", "North Storage")

        assert created["id"] == "rootfolder123"
        assert delays == [1.0]
        assert [request[0] for request in session.requests].count("POST") == 2


    def test_reuses_existing_children_without_creating_duplicates(self) -> None:
        root_id = "rootfolder123"
        receipt = managed_folder(
            "receiptchild123",
            "Receipts",
            storage_kind="receipts",
            root_id=root_id,
        )
        backup = managed_folder(
            "backupchild123",
            "Ledger Backups",
            storage_kind="ledger-backups",
            root_id=root_id,
        )
        session = DriveSession(
            [
                FakeResponse(
                    200,
                    {
                        "id": root_id,
                        "name": "North Storage",
                        "mimeType": "application/vnd.google-apps.folder",
                        "trashed": False,
                    },
                ),
                FakeResponse(200, {"files": [receipt]}),
                FakeResponse(200, {"files": [backup]}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        children = client.provision_storage_children("north", root_id)

        assert children["receipts"]["id"] == "receiptchild123"
        assert children["ledger-backups"]["id"] == "backupchild123"
        assert not any(request[0] == "POST" for request in session.requests)
        assert len(session.requests) == 3
        assert all(request[1].endswith("/files") for request in session.requests[1:])


    def test_uncertain_child_creation_reconciles_before_retrying(self) -> None:
        root_id = "rootfolder123"
        receipt = managed_folder(
            "receiptchild123",
            "Receipts",
            storage_kind="receipts",
            root_id=root_id,
        )
        backup = managed_folder(
            "backupchild123",
            "Ledger Backups",
            storage_kind="ledger-backups",
            root_id=root_id,
        )
        session = DriveSession(
            [
                FakeResponse(
                    200,
                    {
                        "id": root_id,
                        "name": "North Storage",
                        "mimeType": "application/vnd.google-apps.folder",
                        "trashed": False,
                    },
                ),
                FakeResponse(200, {"files": []}),
                requests.ConnectionError("response was lost after create"),
                FakeResponse(200, {"files": [receipt]}),
                FakeResponse(200, {"files": [backup]}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        children = client.provision_storage_children("north", root_id)

        assert children["receipts"]["id"] == "receiptchild123"
        assert [request[0] for request in session.requests].count("POST") == 1


    def test_partial_provisioning_is_reusable_on_a_later_attempt(self) -> None:
        root_id = "rootfolder123"
        receipt = managed_folder(
            "receiptchild123",
            "Receipts",
            storage_kind="receipts",
            root_id=root_id,
        )
        backup = managed_folder(
            "backupchild123",
            "Ledger Backups",
            storage_kind="ledger-backups",
            root_id=root_id,
        )
        session = DriveSession(
            [
                FakeResponse(200, {"id": root_id, "name": "North Storage", "mimeType": "application/vnd.google-apps.folder", "trashed": False}),
                FakeResponse(200, {"files": []}),
                FakeResponse(200, receipt),
                FakeResponse(200, {"files": []}),
                requests.ConnectionError("backup response was lost"),
                FakeResponse(200, {"files": []}),
                requests.ConnectionError("backup response was lost again"),
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"id": "rootfolder123", "name": "North Storage", "mimeType": "application/vnd.google-apps.folder", "trashed": False}),
                FakeResponse(200, {"files": [receipt]}),
                FakeResponse(200, {"files": []}),
                FakeResponse(200, backup),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        with pytest.raises(GoogleDriveAccessError):
            client.provision_storage_children("north", root_id)
        children = client.provision_storage_children("north", root_id)

        assert children["receipts"]["id"] == "receiptchild123"
        assert children["ledger-backups"]["id"] == "backupchild123"


    def test_managed_child_must_remain_under_its_profile_root(self) -> None:
        root_id = "rootfolder123"
        wrong_root = "otherroot123"
        child = managed_folder(
            "receiptchild123",
            "Receipts",
            storage_kind="receipts",
            root_id=wrong_root,
        )
        session = DriveSession(
            [
                FakeResponse(200, {"id": root_id, "name": "North Storage", "mimeType": "application/vnd.google-apps.folder", "trashed": False}),
                FakeResponse(200, {"files": [child]}),
                FakeResponse(200, child),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        with pytest.raises(GoogleDriveAccessError, match="outside"):
            client.provision_storage_children("north", root_id)


    def test_managed_child_with_missing_parents_is_rejected_safely(self) -> None:
        root_id = "rootfolder123"
        child = managed_folder(
            "receiptchild123",
            "Receipts",
            storage_kind="receipts",
            root_id=root_id,
        )
        child["parents"] = None
        session = DriveSession(
            [
                FakeResponse(
                    200,
                    {
                        "id": root_id,
                        "name": "North Storage",
                        "mimeType": "application/vnd.google-apps.folder",
                        "trashed": False,
                    },
                ),
                FakeResponse(200, {"files": [child]}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        with pytest.raises(GoogleDriveAccessError, match="outside"):
            client.provision_storage_children("north", root_id)


    def test_managed_folder_with_missing_name_is_rejected(self) -> None:
        session = DriveSession(
            [
                FakeResponse(
                    200,
                    {
                        "files": [
                            {
                                "id": "rootfolder123",
                                "name": None,
                                "mimeType": "application/vnd.google-apps.folder",
                                "trashed": False,
                                "appProperties": {
                                    "tabulaRasaKind": "storage-root",
                                    "tabulaRasaProfile": "north",
                                },
                            }
                        ]
                    },
                )
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        with pytest.raises(GoogleDriveAccessError, match="not available"):
            client.create_storage_root("north", "North Storage")


    def test_selected_folder_is_validated_before_it_is_saved(self) -> None:
        session = DriveSession(
            [
                FakeResponse(
                    200,
                    {
                        "id": "receiptfolder123",
                        "name": "Receipts",
                        "mimeType": "application/vnd.google-apps.folder",
                        "trashed": False,
                        "webViewLink": (
                            "https://drive.google.com/drive/folders/receiptfolder123"
                        ),
                    },
                )
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        folder = client.folder("receiptfolder123")

        assert folder["id"] == "receiptfolder123"
        assert folder["name"] == "Receipts"
        request = session.requests[0]
        assert request[0] == "GET"
        assert request[1].endswith("/files/receiptfolder123")
        assert request[2]["params"]["supportsAllDrives"] == "true"


    @pytest.mark.parametrize(
        "folder",
        [
            {
                "id": "receiptfolder123",
                "name": "Not a folder",
                "mimeType": "application/pdf",
                "trashed": False,
            },
            {
                "id": "receiptfolder123",
                "name": "Deleted folder",
                "mimeType": "application/vnd.google-apps.folder",
                "trashed": True,
            },
        ],
    )
    def test_selected_folder_must_be_available_and_be_a_folder(
        self,
        folder: dict[str, Any],
    ) -> None:
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=DriveSession([FakeResponse(200, folder)]),
            sleeper=lambda _delay: None,
        )

        with pytest.raises(
            GoogleDriveAccessError,
            match="not an available folder",
        ):
            client.folder("receiptfolder123")


    def test_profile_scoped_backup_queries_use_distinct_authorizations(self) -> None:
        north_session = DriveSession(
            [FakeResponse(200, {"files": []})],
            access_token="north-access",
        )
        south_session = DriveSession(
            [FakeResponse(200, {"files": []})],
            access_token="south-access",
        )
        north_client = GoogleDriveClient(
            oauth_config(),
            drive_settings(
                email="north@example.test",
                refresh_token="north-refresh",
            ),
            session=north_session,
            sleeper=lambda _delay: None,
        )
        south_client = GoogleDriveClient(
            oauth_config(),
            drive_settings(
                email="south@example.test",
                refresh_token="south-refresh",
            ),
            session=south_session,
            sleeper=lambda _delay: None,
        )

        north_client.list_backups(
            profile_key="north",
            folder_id="northfolder123",
        )
        south_client.list_backups(
            profile_key="south",
            folder_id="southfolder123",
        )

        north_request = north_session.requests[0][2]
        south_request = south_session.requests[0][2]
        assert north_session.refresh_tokens == ["north-refresh"]
        assert south_session.refresh_tokens == ["south-refresh"]
        assert north_request["headers"]["Authorization"] == "Bearer north-access"
        assert south_request["headers"]["Authorization"] == "Bearer south-access"
        assert "'northfolder123' in parents" in north_request["params"]["q"]
        assert "value='north'" in north_request["params"]["q"]
        assert "south" not in north_request["params"]["q"]
        assert "'southfolder123' in parents" in south_request["params"]["q"]
        assert "value='south'" in south_request["params"]["q"]
        assert "north" not in south_request["params"]["q"]
