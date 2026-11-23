from __future__ import annotations

from tests.google_auth_fixtures import GOOGLE_OAUTH_CONFIG, google_grant

import pytest

from tabula_rasa.profile_readiness import (
    profile_configuration_readiness,
)


def _settings(**overrides: object) -> dict[str, object]:
    settings: dict[str, object] = {
        "name": "North Shop",
        "spreadsheet_id": "north-sheet",
        "google_drive": google_grant('ledger@example.test'), "google_drive_oauth": GOOGLE_OAUTH_CONFIG,
    }
    settings.update(overrides)
    return settings


def _drive_settings() -> dict[str, object]:
    return {
        "authorized_email": "owner@example.test",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
        "storage_root": {"id": "root123456", "name": "North Storage"},
        "receipt_folder": {"id": "receipts123456", "name": "Receipts"},
        "backup_folder": {"id": "backup123456", "name": "Ledger Backups"},
    }


def test_sheets_only_profile_is_ready_locally_without_optional_destinations() -> None:
    report = profile_configuration_readiness("north", _settings())

    assert report.status == "ready-local"
    assert report.missing_steps == ()


def test_partial_drive_configuration_is_incomplete() -> None:
    report = profile_configuration_readiness(
        "north",
        _settings(google_drive={"refresh_token": "present", "storage_root": {}}),
    )

    assert report.status == "incomplete"
    assert report.missing_steps == (
        "Select or create the Google Drive storage root.",
        "Provision the Receipts and Ledger Backups folders under the root.",
    )


@pytest.mark.parametrize(
    ("folder", "invalid_value", "expected_step"),
    (
        (
            "storage_root",
            {},
            "Select or create the Google Drive storage root.",
        ),
        (
            "storage_root",
            {"id": None},
            "Select or create the Google Drive storage root.",
        ),
        (
            "receipt_folder",
            {},
            "Provision the Receipts and Ledger Backups folders under the root.",
        ),
        (
            "receipt_folder",
            {"id": None},
            "Provision the Receipts and Ledger Backups folders under the root.",
        ),
        (
            "backup_folder",
            {},
            "Provision the Receipts and Ledger Backups folders under the root.",
        ),
        (
            "backup_folder",
            {"id": None},
            "Provision the Receipts and Ledger Backups folders under the root.",
        ),
    ),
)
def test_drive_folder_mappings_require_ids(
    folder: str,
    invalid_value: dict[str, object],
    expected_step: str,
) -> None:
    drive = _drive_settings()
    drive[folder] = invalid_value

    report = profile_configuration_readiness(
        "north",
        _settings(google_drive=drive),
    )

    assert report.status == "incomplete"
    assert expected_step in report.missing_steps


def test_ready_local_does_not_claim_to_verify_hosted_secrets() -> None:
    report = profile_configuration_readiness("north", _settings())

    assert report.status == "ready-local"
    assert not hasattr(report, "hosted_export_needed")


def test_local_only_validation_failure_is_sanitized_and_actionable() -> None:
    report = profile_configuration_readiness(
        "north",
        _settings(),
        validation_errors=("Sheets validation failed",),
    )

    assert report.status == "incomplete"
    assert report.validation_errors == ("Sheets validation failed",)
    assert "credential" not in " ".join(report.validation_errors).casefold()
