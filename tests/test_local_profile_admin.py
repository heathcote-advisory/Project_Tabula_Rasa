from __future__ import annotations

from tests.google_auth_fixtures import GOOGLE_OAUTH_CONFIG, google_grant

from unittest.mock import Mock, patch

import pytest
import streamlit as st

from tabula_rasa.local_profile_admin import _profile_rows
from tabula_rasa.local_profile_admin_readiness import (
    fresh_profile_readiness,
    readiness_for_display,
)
from tabula_rasa.local_profile_admin_security import (
    render_security_deployment_info,
)
from tabula_rasa.local_profile_admin_support import (
    initialize_candidate_storage,
)
from tabula_rasa.profile_readiness import ProfileReadiness, profile_settings_fingerprint
from tabula_rasa.storage import (
    StorageError,
    StorageValidationError,
    StorageWorkbookAccessError,
)
SETTINGS = {
    "spreadsheet_id": "abcdefghij1234567890",
    "google_drive": google_grant('ledger@example.test'), "google_drive_oauth": GOOGLE_OAUTH_CONFIG,
}


def test_profile_rows_use_a_safe_fallback_for_null_backup_paths() -> None:
    rows = _profile_rows(
        {
            "business_profiles": {
                "north": {
                    **SETTINGS,
                    "name": "North Shop",
                    "backup_folder_path": None,
                }
            }
        },
        {"north": {}},
    )

    assert rows[0]["Local backup folder"] == "Not configured"
    assert rows[0]["Spreadsheet"] == "…34567890"
    assert SETTINGS["spreadsheet_id"] not in repr(rows)


def test_profile_rows_allow_clean_install_without_hosted_profiles() -> None:
    assert _profile_rows({"app_password_hash": "configured"}, {}) == []


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


def test_readiness_cache_is_ignored_after_profile_settings_change() -> None:
    original = {"name": "North Shop", **SETTINGS}
    changed = {**original, "backup_folder_path": r"G:\\Backups\\north"}
    cached = ProfileReadiness(status="ready-local")
    with patch.object(
        st,
        "session_state",
        {
            "profile-readiness:north": (
                profile_settings_fingerprint(original),
                cached,
            )
        },
    ):
        report = readiness_for_display(
            "north",
            changed,
        )

    assert report.status == "incomplete"
    assert report.missing_steps == (
        "Run the readiness check to verify current Sheets and Drive settings.",
    )


def test_ready_local_does_not_direct_operator_to_retired_hosted_export() -> None:
    from tabula_rasa.local_profile_admin_readiness import render_profile_readiness

    with patch(
        "tabula_rasa.local_profile_admin_readiness.readiness_for_display",
        return_value=ProfileReadiness(status="ready-local"),
    ), patch.object(st, "container"), patch.object(st, "subheader"), patch.object(
        st, "metric"
    ), patch.object(st, "info") as info, patch.object(st, "button", return_value=False):
        render_profile_readiness("north", SETTINGS, app_secrets={}, local_profiles={})

    message = info.call_args.args[0]
    assert "separate approved server provisioning" in message
    assert "export_streamlit_secrets" not in message
    assert "App settings > Secrets" not in message


def test_security_guidance_links_to_operator_guides_without_offering_credential_export() -> None:
    with patch.object(st, "subheader"), patch.object(st, "write") as render_write, patch.object(
        st, "info"
    ), patch.object(st, "markdown") as render_markdown, patch.object(
        st, "button"
    ) as button:
        render_security_deployment_info()

    button.assert_not_called()
    guidance = " ".join(str(call.args[0]) for call in render_write.call_args_list)
    assert "separate encrypted server provisioning" in guidance
    assert "export is retired" in guidance
    rendered = " ".join(
        str(call.args[0]) for call in render_markdown.call_args_list if call.args
    )
    assert "/docs/SETUP.md" in rendered
    assert "/docs/BACKUP_RECOVERY.md)" in rendered
    assert "/docs/BACKUP_RECOVERY.md#optional-synology-recovery-copy" in rendered
    assert "/docs/setup/" not in rendered
    assert "/docs/operations/" not in rendered
