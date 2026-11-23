import json
import os
import sys
from unittest.mock import Mock

import pytest

from scripts import local_profile_admin as cli, restore_google_sheets_backup as restore
from tabula_rasa import google_drive_admin
from tabula_rasa.google_drive import GoogleDriveAuthorizationError
from tabula_rasa.ledger_auth import hash_password
from tabula_rasa.local_profile_store import (
    load_business_profile_document,
    load_local_profiles,
)
from tabula_rasa.receipt_manifest import build_receipt_manifest
from tabula_rasa.storage import GoogleSheetsStorage, StorageWorkbookAccessError
from tests.fakes import FakeWorkbook
from tests.google_auth_fixtures import GOOGLE_OAUTH_CONFIG, google_grant
from tests.sheets_fixtures import account


@pytest.fixture
def operator(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles.json"
    secrets = tmp_path / "secrets.toml"
    password = "test-only-password"
    config = {
        **GOOGLE_OAUTH_CONFIG,
        "picker_api_key": "test-key",
        "project_number": "123",
    }
    secrets.write_text(
        f'app_password_hash = "{hash_password(password)}"\n[google_drive_oauth]\n'
        + "\n".join(f"{key} = {json.dumps(value)}" for key, value in config.items()),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "LOCAL_PROFILE_STORE_PATH", profiles)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: password)

    def run(*args):
        return cli.main(["--secrets", str(secrets), *args])

    return run, profiles, secrets


def test_two_profile_operator_recovery(operator, tmp_path, monkeypatch, capsys):
    run, profiles_path, secrets = operator
    north = "north-ledger-123"
    south = "south-ledger-123"
    candidate = "blank-recovery-123"
    workbooks = {
        north: FakeWorkbook(),
        south: FakeWorkbook(),
        candidate: FakeWorkbook({}),
    }

    def open_workbook(settings, *, scopes):
        token = settings["google_drive"]["refresh_token"]
        identifier = settings["spreadsheet_id"]
        if identifier not in workbooks:
            raise StorageWorkbookAccessError("provider details must stay private")
        assert (
            token.startswith("south")
            if identifier == south
            else token.startswith("north")
        )
        return workbooks[identifier]

    drive = Mock()
    drive.access_token.return_value = "test-short-lived-token"
    drive._request.return_value.json.return_value = {
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "capabilities": {"canEdit": True},
    }
    drive.folder.side_effect = lambda identifier: {"id": identifier, "name": "Storage"}
    drive.provision_storage_children.return_value = {
        "receipts": {"id": "south-receipts-123", "name": "Receipts"},
        "ledger-backups": {"id": "south-backups-123", "name": "Backups"},
    }
    monkeypatch.setattr(cli, "GoogleDriveClient", Mock(return_value=drive))
    monkeypatch.setattr(
        google_drive_admin, "GoogleDriveClient", Mock(return_value=drive)
    )
    monkeypatch.setattr(
        GoogleSheetsStorage, "_open_workbook_from_secrets", open_workbook
    )
    monkeypatch.setattr(
        cli,
        "request_google_code",
        lambda _, key, *args, **kwargs: f"{key}-private-code",
    )
    monkeypatch.setattr(
        google_drive_admin,
        "exchange_authorization_code",
        lambda _, code, redirect: google_grant(token=code.replace("code", "refresh")),
    )

    for key, workbook in (("north", north), ("south", south)):
        assert run("profile", "add", key, "--name", key.title()) == 0
        assert run("google", "connect", key) == 0
        monkeypatch.setattr(cli, "request_picker_id", Mock(return_value=workbook))
        assert run("workbook", "select", key) == 0
    monkeypatch.setattr(cli, "request_picker_id", Mock(return_value="south-root-123"))
    assert run("google", "pick-root", "south") == 0
    assert run("google", "provision-folders", "south") == 0
    assert run("google", "check", "south") == 0
    assert run("recovery", "set", "south") == 0
    south_before = load_local_profiles(profiles_path)["south"]

    assert (
        run(
            "profile",
            "edit",
            "north",
            "--name",
            "North Books",
            "--backup-folder",
            str(tmp_path),
        )
        == 0
    )
    assert run("workbook", "initialize", "north") == 0
    assert run("profile", "check", "north") == 0

    source = GoogleSheetsStorage(workbooks[north])
    account(source, "Synthetic checking", "Bank")
    archive = tmp_path / "format-13.zip"
    archive.write_bytes(
        source.export_backup_zip(build_receipt_manifest("north", [], []))
    )

    before = profiles_path.read_bytes()
    for rejected in (south, "inaccessible-workbook-123"):
        monkeypatch.setattr(cli, "request_picker_id", Mock(return_value=rejected))
        assert run("workbook", "select", "north") == 1
        assert profiles_path.read_bytes() == before

    monkeypatch.setattr(cli, "request_picker_id", Mock(return_value=candidate))
    assert run("workbook", "authorize", "north") == 0
    assert candidate in capsys.readouterr().out
    assert profiles_path.read_bytes() == before

    monkeypatch.setattr(
        restore,
        "load_business_profile_document",
        lambda path: load_business_profile_document(path, profiles_path=profiles_path),
    )
    for target in (north, south, candidate):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "restore_google_sheets_backup",
                str(archive),
                "--profile",
                "north",
                "--secrets",
                str(secrets),
                "--target-spreadsheet-id",
                target,
                "--initialize",
            ],
        )
        if target == candidate:
            restore.main()
        else:
            with pytest.raises(SystemExit, match="configured live workbook"):
                restore.main()

    assert (
        workbooks[candidate].worksheet("Accounts").rows
        == workbooks[north].worksheet("Accounts").rows
    )
    assert load_local_profiles(profiles_path)["south"] == south_before
    assert profiles_path.read_bytes() == before
    output = capsys.readouterr()
    for private in (
        "private-code",
        "private-refresh",
        "test-short-lived-token",
        "fake-secret",
        "Synthetic checking",
        "provider details",
    ):
        assert private not in output.out + output.err


@pytest.mark.parametrize(
    "failure",
    [
        GoogleDriveAuthorizationError(
            "Google browser interaction was cancelled or denied."
        ),
        GoogleDriveAuthorizationError("Google browser interaction timed out."),
        KeyboardInterrupt(),
    ],
)
def test_failed_reconnect_preserves_existing_grant(operator, monkeypatch, failure):
    run, profiles_path, _ = operator
    assert run("profile", "add", "north", "--name", "North") == 0
    monkeypatch.setattr(cli, "request_google_code", Mock(return_value="code"))
    exchange = Mock(return_value=google_grant())
    monkeypatch.setattr(google_drive_admin, "exchange_authorization_code", exchange)
    assert run("google", "connect", "north") == 0
    before = profiles_path.read_bytes()
    exchange.reset_mock()
    monkeypatch.setattr(cli, "request_google_code", Mock(side_effect=failure))
    assert run("google", "connect", "north") == (
        130 if isinstance(failure, KeyboardInterrupt) else 1
    )
    assert profiles_path.read_bytes() == before
    exchange.assert_not_called()


def test_password_is_required_before_profile_or_browser_access(
    operator, monkeypatch, capsys
):
    run, profiles_path, _ = operator
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "bad-private-password")
    browser = Mock()
    monkeypatch.setattr(cli, "request_google_code", browser)
    assert run("google", "connect", "north") == 1
    assert not profiles_path.exists()
    assert not profiles_path.with_suffix(".lock").exists()
    browser.assert_not_called()
    assert "bad-private-password" not in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="Exercises Windows byte-range locking")
def test_windows_operator_lock_uses_first_byte_of_existing_file(tmp_path):
    import msvcrt

    profile_path = tmp_path / "profiles.json"
    lock_path = profile_path.with_suffix(".lock")
    lock_path.write_bytes(b"existing lock file")
    with lock_path.open("r+b") as other_operator:
        msvcrt.locking(other_operator.fileno(), msvcrt.LK_NBLCK, 1)
        with pytest.raises(
            cli.LocalProfileStoreError, match="Another operator command"
        ):
            with cli._operator_lock(profile_path):
                pytest.fail("A second operator acquired the same lock")
    with cli._operator_lock(profile_path):
        pass
    assert lock_path.read_bytes() == b"existing lock file"
