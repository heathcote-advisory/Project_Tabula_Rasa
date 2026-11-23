from __future__ import annotations

import pytest

from tabula_rasa.synology_recovery import (
    PROFILE_ROOT_LAYOUT,
    SynologyRecoveryConfigurationError,
    resolve_synology_recovery_defaults,
    validate_synology_recovery_settings,
)


def test_normalizes_synology_recovery_settings() -> None:
    settings = validate_synology_recovery_settings(
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": " /volume1/Backups/TabulaRasa/north ",
            "schedule": " Continuous ",
            "retention_policy": " Keep until manual review. ",
        }
    )
    assert settings == {
        "layout": PROFILE_ROOT_LAYOUT,
        "destination_path": "/volume1/Backups/TabulaRasa/north",
        "schedule": "Continuous",
        "retention_policy": "Keep until manual review.",
    }


def test_rejects_retired_three_field_synology_record_explicitly() -> None:
    with pytest.raises(
        SynologyRecoveryConfigurationError,
        match="retired three-field schema.*layout='profile-root'",
    ):
        validate_synology_recovery_settings(
            {
                "destination_path": "/volume1/Backups/TabulaRasa/north",
                "schedule": "Continuous",
                "retention_policy": "Keep until manual review.",
            }
        )


def test_rejects_missing_synology_recovery_fields_explicitly() -> None:
    with pytest.raises(
        SynologyRecoveryConfigurationError,
        match="missing required fields: retention_policy",
    ):
        validate_synology_recovery_settings(
            {
                "layout": PROFILE_ROOT_LAYOUT,
                "destination_path": "/volume1/Backups/TabulaRasa/north",
                "schedule": "Continuous",
            }
        )


def test_rejects_unsupported_synology_recovery_fields_explicitly() -> None:
    with pytest.raises(
        SynologyRecoveryConfigurationError,
        match="unsupported fields: legacy_task_id",
    ):
        validate_synology_recovery_settings(
            {
                "layout": PROFILE_ROOT_LAYOUT,
                "destination_path": "/volume1/Backups/TabulaRasa/north",
                "schedule": "Continuous",
                "retention_policy": "Keep all.",
                "legacy_task_id": "task-123",
            }
        )


@pytest.mark.parametrize("destination", ["/volume1/Backups/team/../north//", "//volume1/Backups/north"])
def test_normalizes_equivalent_dsm_paths(destination: str) -> None:
    settings = validate_synology_recovery_settings(
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": destination,
            "schedule": "Continuous",
            "retention_policy": "Keep all.",
        }
    )

    assert settings["destination_path"] == "/volume1/Backups/north"


def test_resolves_profile_root_defaults_and_overrides() -> None:
    defaults = resolve_synology_recovery_defaults(
        {
            "base_path": "/volume2/Recovery/TabulaRasa/",
            "schedule": "Daily at 11:00 PM",
            "retention_policy": "Keep quarterly versions.",
        },
        "north-shop",
    )

    assert defaults == {
        "layout": PROFILE_ROOT_LAYOUT,
        "destination_path": "/volume2/Recovery/TabulaRasa/north-shop",
        "schedule": "Daily at 11:00 PM",
        "retention_policy": "Keep quarterly versions.",
    }


def test_default_destinations_are_isolated_by_profile_key() -> None:
    north = resolve_synology_recovery_defaults(None, "north")
    south = resolve_synology_recovery_defaults(None, "south")

    assert north["destination_path"] != south["destination_path"]
    assert north["destination_path"].endswith("/north")
    assert south["destination_path"].endswith("/south")


@pytest.mark.parametrize(
    "settings",
    (
        {},
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": "relative/path",
            "schedule": "Continuous",
            "retention_policy": "Keep all",
        },
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": "/",
            "schedule": "Continuous",
            "retention_policy": "Keep all",
        },
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": "/volume1",
            "schedule": "Continuous",
            "retention_policy": "Keep all",
        },
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": "/etc/tabula-rasa",
            "schedule": "Continuous",
            "retention_policy": "Keep all",
        },
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": "/volume1/Backups/../../../etc/tabula-rasa",
            "schedule": "Continuous",
            "retention_policy": "Keep all",
        },
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": "/volume1/Backups/north",
            "schedule": "",
            "retention_policy": "Keep all",
        },
        {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": "/volume1/Backups/north",
            "schedule": "Continuous",
            "retention_policy": "Keep all",
            "password": "must-not-be-stored",
        },
        {
            "layout": "unknown",
            "destination_path": "/volume1/Backups/north",
            "schedule": "Continuous",
            "retention_policy": "Keep all",
        },
    ),
)
def test_rejects_incomplete_or_unsupported_synology_settings(
    settings: object,
) -> None:
    with pytest.raises(SynologyRecoveryConfigurationError):
        validate_synology_recovery_settings(settings)


@pytest.mark.parametrize(
    "settings",
    (
        "not-a-table",
        {"base_path": "relative/path"},
        {"base_path": "/volume1/Recovery", "password": "not-allowed"},
    ),
)
def test_rejects_malformed_app_wide_defaults(settings: object) -> None:
    with pytest.raises(SynologyRecoveryConfigurationError):
        resolve_synology_recovery_defaults(settings, "north")
