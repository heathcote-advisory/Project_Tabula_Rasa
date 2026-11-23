from __future__ import annotations

from tests.google_auth_fixtures import GOOGLE_OAUTH_CONFIG, google_grant

from unittest.mock import Mock, patch

import pytest

from tabula_rasa.local_profile_admin_readiness import (
    fresh_profile_readiness,
)
from tabula_rasa.local_profile_admin_support import (
    initialize_candidate_storage,
)
from tabula_rasa.storage import (
    StorageError,
    StorageValidationError,
    StorageWorkbookAccessError,
)
SETTINGS = {
    "spreadsheet_id": "abcdefghij1234567890",
    "google_drive": google_grant('ledger@example.test'), "google_drive_oauth": GOOGLE_OAUTH_CONFIG,
}


@pytest.mark.parametrize(
    "error",
    (StorageWorkbookAccessError("access"), StorageValidationError("invalid")),
)
def test_candidate_storage_preserves_specific_storage_errors(
    error: StorageError,
) -> None:
    candidate = Mock()
    candidate.ensure_defaults.side_effect = error
    with patch(
        "tabula_rasa.local_profile_admin_support.GoogleSheetsStorage.from_secrets",
        return_value=candidate,
    ):
        with pytest.raises(type(error)) as raised:
            initialize_candidate_storage(SETTINGS)

    assert raised.value is error


def test_fresh_readiness_reports_sheets_failure_without_secret_values() -> None:
    with patch(
        "tabula_rasa.local_profile_admin_readiness.GoogleSheetsStorage.from_secrets_read_only",
        side_effect=StorageWorkbookAccessError("contains no secret"),
    ), patch(
        "tabula_rasa.local_profile_admin_readiness.validate_google_drive_profile"
    ) as validate_drive:
        report = fresh_profile_readiness(
            "north",
            {
                **SETTINGS,
                "name": "North Shop",
            },
            app_secrets={},
        )

    assert report.status == "incomplete"
    assert report.validation_errors == (
        "Sheets workbook validation failed. Confirm sharing and the workbook ID.",
    )
    validate_drive.assert_not_called()


def test_fresh_readiness_skips_drive_validation_without_a_storage_root() -> None:
    with patch(
        "tabula_rasa.local_profile_admin_readiness.GoogleSheetsStorage.from_secrets_read_only"
    ), patch(
        "tabula_rasa.local_profile_admin_readiness.validate_google_drive_profile"
    ) as validate_drive:
        report = fresh_profile_readiness(
            "north",
            {
                **SETTINGS,
                "name": "North Shop",
                "google_drive": {"authorized_email": "owner@example.test"},
            },
            app_secrets={},
        )

    assert report.status == "ready-local"
    assert report.missing_steps == ()
    assert report.validation_errors == ()
    validate_drive.assert_not_called()


def test_fresh_readiness_uses_visible_drive_parent_ids_only() -> None:
    drive = {
        "storage_root": {"id": "northroot123", "name": "North Storage"},
        "receipt_folder": {"id": "receipts123", "name": "Receipts"},
        "backup_folder": {"id": "backups1234", "name": "Ledger Backups"},
    }
    settings = {**SETTINGS, "name": "North Shop", "google_drive": drive}
    local_profiles = {
        "north": settings,
        "south": {
            **SETTINGS,
            "name": "South Shop",
            "spreadsheet_id": "southsheet123",
            "google_drive": {
                "storage_root": {"id": "southroot123"},
            },
        },
    }
    with patch(
        "tabula_rasa.local_profile_admin_readiness.GoogleSheetsStorage.from_secrets_read_only"
    ), patch(
        "tabula_rasa.local_profile_admin_readiness.validate_google_drive_profile"
    ) as validate_drive:
        report = fresh_profile_readiness(
            "north",
            settings,
            app_secrets={},
            local_profiles=local_profiles,
        )

    assert report.status == "ready-local"
    validate_drive.assert_called_once_with(
        {},
        "north",
        drive,
        local_profiles=local_profiles,
    )
