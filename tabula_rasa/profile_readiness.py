"""Sanitized readiness contracts for local profile administration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

ReadinessStatus = Literal["incomplete", "ready-local"]


@dataclass(frozen=True)
class ProfileReadiness:
    """Safe status information suitable for operator reports."""

    status: ReadinessStatus
    missing_steps: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()


def profile_configuration_readiness(
    profile_key: str,
    settings: Mapping[str, Any],
    *,
    validation_errors: tuple[str, ...] = (),
) -> ProfileReadiness:
    """Return a secret-free readiness report for one validated local profile."""
    missing: list[str] = []
    drive = settings.get("google_drive")
    if not settings.get("spreadsheet_id"):
        missing.append("Select the ledger spreadsheet through Google Picker.")
    if not isinstance(drive, Mapping):
        missing.append("Connect this profile's Google account.")
    if isinstance(drive, Mapping) and any(field in drive for field in ("storage_root", "receipt_folder", "backup_folder")):
        storage_root = drive.get("storage_root")
        if not isinstance(storage_root, Mapping) or not storage_root.get("id"):
            missing.append("Select or create the Google Drive storage root.")
        receipt_folder = drive.get("receipt_folder")
        backup_folder = drive.get("backup_folder")
        if (
            not isinstance(receipt_folder, Mapping)
            or not receipt_folder.get("id")
            or not isinstance(backup_folder, Mapping)
            or not backup_folder.get("id")
        ):
            missing.append(
                "Provision the Receipts and Ledger Backups folders under the root."
            )

    errors = tuple(str(error) for error in validation_errors if str(error).strip())
    if errors:
        missing.append("Resolve the reported validation failure and run the check again.")
    if missing:
        return ProfileReadiness(
            status="incomplete",
            missing_steps=tuple(dict.fromkeys(missing)),
            validation_errors=errors,
        )

    return ProfileReadiness(status="ready-local")
