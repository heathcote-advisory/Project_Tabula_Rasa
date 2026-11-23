"""Manage privileged local Sheets and Drive profiles with operator credentials."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tabula_rasa.google_drive import (
    GoogleDriveClient,
    GoogleDriveConfigurationError,
    GoogleDriveError,
    load_oauth_config,
)
from tabula_rasa.google_drive_admin import (
    create_storage_root,
    provision_storage_children,
    save_storage_root,
    provision_profile_ledger,
    save_google_authorization,
    select_profile_ledger,
    validate_google_drive_profile,
)
from tabula_rasa.ledger_auth import verify_password
from tabula_rasa.local_profile_admin_readiness import fresh_profile_readiness
from tabula_rasa.local_profile_admin_support import (
    initialize_candidate_storage,
    masked_spreadsheet_id,
)
from tabula_rasa.local_profile_store import (
    LOCAL_PROFILE_STORE_PATH,
    LocalProfileStoreError,
    build_profile_settings,
    load_local_app_secrets,
    load_local_profiles,
    save_local_profile,
    update_local_profile,
    update_local_profile_google_drive,
    update_local_profile_synology_recovery,
    validate_profile_key,
)
from tabula_rasa.operator_google_browser import (
    local_redirect_uri,
    request_google_code,
    request_picker_id,
)
from tabula_rasa.storage import StorageError
from tabula_rasa.synology_recovery import (
    PROFILE_ROOT_LAYOUT,
    SynologyRecoveryConfigurationError,
    resolve_synology_recovery_defaults,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=LOCAL_PROFILE_STORE_PATH.with_name("secrets.toml"),
    )
    groups = parser.add_subparsers(dest="group", required=True)

    profile = groups.add_parser(
        "profile", help="Create, edit, list or validate local profiles"
    )
    profile.add_argument("action", choices=("add", "edit", "list", "check"))
    profile.add_argument("profile", nargs="?")
    profile.add_argument("--name")
    profile.add_argument(
        "--backup-folder", help="Absolute local path, or an empty value to clear it"
    )

    google = groups.add_parser(
        "google", help="Authorize Google and configure profile storage folders"
    )
    google.add_argument(
        "action",
        choices=(
            "connect",
            "disconnect",
            "pick-root",
            "create-root",
            "provision-folders",
            "check",
        ),
    )
    google.add_argument("profile")
    google.add_argument(
        "--redirect-uri",
        help="Use this registered loopback URI instead of the first configured one",
    )

    workbook = groups.add_parser(
        "workbook", help="Select, initialize or authorize Google Sheets workbooks"
    )
    workbook.add_argument(
        "action", choices=("select", "authorize", "create", "initialize")
    )
    workbook.add_argument("profile")
    workbook.add_argument(
        "--redirect-uri",
        help="Use this registered loopback URI instead of the first configured one",
    )

    recovery = groups.add_parser(
        "recovery", help="Manage the local profile's Synology Cloud Sync metadata"
    )
    recovery.add_argument("action", choices=("set", "show", "disable"))
    recovery.add_argument("profile")
    recovery.add_argument("--destination")
    recovery.add_argument("--schedule")
    recovery.add_argument("--retention")
    return parser


@contextmanager
def _operator_lock(path: Path) -> Iterator[None]:
    """Serialize operator commands that share a profile store across processes."""
    with path.with_suffix(".lock").open("ab") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LocalProfileStoreError(
                "Another operator command is using the local profile store."
            ) from None
        yield


def _authenticate(secrets: Mapping[str, Any]) -> None:
    encoded = secrets.get("app_password_hash")
    if not isinstance(encoded, str) or not encoded:
        raise LocalProfileStoreError(
            "Configure the local application password before administration."
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("Local application password: ")
        except (EOFError, getpass.GetPassWarning):
            raise LocalProfileStoreError(
                "Run this command in an interactive terminal."
            ) from None
    if not verify_password(password, encoded):
        raise LocalProfileStoreError("Incorrect local application password.")


def _profile_command(args, secrets, profiles, path: Path) -> None:
    if args.action == "list":
        for key, profile in profiles.items():
            ledger = (
                masked_spreadsheet_id(profile.get("spreadsheet_id", "")) or "unselected"
            )
            drive = profile.get("google_drive")
            connected = "disconnected"
            if drive:
                email = drive.get("authorized_email")
                connected = f"connected as {email!r}" if email else "connected"
            print(f"{key} | {profile['name']!r} | Sheets {ledger} | Google {connected}")
        if not profiles:
            print("No local profiles configured.")
        return

    key = args.profile
    if args.action == "add":
        settings = build_profile_settings(args.name, "", args.backup_folder)
        save_local_profile(key, settings, path=path)
        print(f"Created profile {key}.")
        return

    settings = profiles[key]
    if args.action == "edit":
        update_local_profile(
            key,
            name=args.name if args.name is not None else settings["name"],
            spreadsheet=settings["spreadsheet_id"],
            backup_folder_path=(
                args.backup_folder
                if args.backup_folder is not None
                else settings.get("backup_folder_path")
            ),
            path=path,
        )
        print(f"Updated profile {key}.")
        return

    report = fresh_profile_readiness(
        key, settings, app_secrets=secrets, local_profiles=profiles
    )
    print(f"{key}: {report.status}")
    for message in (*report.validation_errors, *report.missing_steps):
        print(message)
    if report.status != "ready-local":
        raise SystemExit(1)


def _google_command(args, secrets, profile, path: Path) -> None:
    key = args.profile
    action = args.action
    if action == "disconnect":
        update_local_profile_google_drive(key, None, path=path)
        print(f"Disconnected local Google authorization for {key}.")
        return

    config = load_oauth_config(secrets)
    drive = profile.get("google_drive")
    if action == "connect":
        previous_email = drive.get("authorized_email", "") if drive else ""
        redirect = local_redirect_uri(config, args.redirect_uri)
        print(f"Waiting for Google consent for {key}. Press Ctrl+C to cancel.")
        code = request_google_code(config, key, redirect, login_hint=previous_email)
        save_google_authorization(config, key, code, redirect, store_path=path)
        print(f"Saved Google authorization for {key}.")
        return

    if not drive:
        raise GoogleDriveConfigurationError(
            "Connect this profile's Google account first."
        )
    if action == "check":
        validate_google_drive_profile(
            secrets, key, drive, local_profiles=load_local_profiles(path)
        )
        print(f"Google grant and storage folders validated for {key}.")
        return

    client = GoogleDriveClient(config, drive)
    if action == "pick-root":
        redirect = local_redirect_uri(config, args.redirect_uri)
        print(f"Waiting for a Drive folder for {key}. Press Ctrl+C to cancel.")
        selected = request_picker_id(config, redirect, "folder", client.access_token)
        save_storage_root(key, drive, selected, client=client, store_path=path)
        print(f"Storage root selected for {key}.")
    elif action == "create-root":
        if drive.get("storage_root"):
            raise LocalProfileStoreError(
                "A storage root is already selected. Use pick-root to change it."
            )
        create_storage_root(key, profile["name"], drive, client=client, store_path=path)
        print(f"Storage root created for {key}.")
    else:
        provision_storage_children(key, drive, client=client, store_path=path)
        print(f"Receipt and ledger backup folders are configured for {key}.")


def _workbook_command(args, secrets, profile, path: Path) -> None:
    key = args.profile
    if args.action == "create":
        provision_profile_ledger(secrets, key, store_path=path)
        print(f"Workbook created and initialized for {key}.")
    elif args.action == "initialize":
        if not profile.get("spreadsheet_id"):
            raise LocalProfileStoreError(
                "Select this profile's workbook before initializing it."
            )
        initialize_candidate_storage(
            {**profile, "google_drive_oauth": secrets.get("google_drive_oauth")}
        )
        print(f"Ledger schema and defaults validated for {key}.")
    else:
        drive = profile.get("google_drive")
        if not drive:
            raise GoogleDriveConfigurationError(
                "Connect this profile's Google account first."
            )
        config = load_oauth_config(secrets)
        redirect = local_redirect_uri(config, args.redirect_uri)
        client = GoogleDriveClient(config, drive)
        print(f"Waiting for a workbook for {key}. Press Ctrl+C to cancel.")
        selected = request_picker_id(
            config, redirect, "spreadsheet", client.access_token
        )
        persist = args.action == "select"
        workbook_id = select_profile_ledger(
            secrets, key, {"id": selected}, store_path=path, persist=persist
        )
        if persist:
            print(f"Workbook selected for {key}.")
        else:
            print(f"Authorized workbook for {key}: {workbook_id}")


def _recovery_command(args, secrets, profile, path: Path) -> None:
    key = args.profile
    existing = profile.get("synology_recovery", {})
    if args.action == "show":
        print(
            f"Local backup folder: {profile.get('backup_folder_path') or 'unconfigured'!r}"
        )
        if not existing:
            print(f"Synology recovery is disabled for {key}.")
            return
        print(f"Synology recovery for {key} (profile-root, configure in DSM):")
        for label, field in (
            ("Destination", "destination_path"),
            ("Schedule", "schedule"),
            ("Retention", "retention_policy"),
        ):
            print(f"{label}: {existing[field]!r}")
        return

    settings = None
    if args.action == "set":
        defaults = resolve_synology_recovery_defaults(
            secrets.get("synology_recovery_defaults"), key
        )
        settings = {
            "layout": PROFILE_ROOT_LAYOUT,
            "destination_path": args.destination
            if args.destination is not None
            else existing.get("destination_path", defaults["destination_path"]),
            "schedule": args.schedule
            if args.schedule is not None
            else existing.get("schedule", defaults["schedule"]),
            "retention_policy": args.retention
            if args.retention is not None
            else existing.get("retention_policy", defaults["retention_policy"]),
        }
    update_local_profile_synology_recovery(key, settings, path=path)
    print(f"Synology recovery {'configured' if settings else 'disabled'} for {key}.")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.group == "profile":
        if args.action != "list" and not args.profile:
            parser.error("this action requires PROFILE")
        if args.action == "add" and not args.name:
            parser.error("profile add requires --name")
    try:
        secrets = load_local_app_secrets(args.secrets)
        _authenticate(secrets)
        path = LOCAL_PROFILE_STORE_PATH
        with _operator_lock(path):
            profiles = load_local_profiles(path)
            if args.profile:
                args.profile = validate_profile_key(args.profile)
            creating = args.group == "profile" and args.action == "add"
            if args.profile and args.profile not in profiles and not creating:
                raise LocalProfileStoreError(
                    "This local profile does not exist. Run profile list."
                )
            if args.group == "profile":
                _profile_command(args, secrets, profiles, path)
            else:
                command = {
                    "google": _google_command,
                    "workbook": _workbook_command,
                    "recovery": _recovery_command,
                }
                command[args.group](args, secrets, profiles[args.profile], path)
    except KeyboardInterrupt:
        print(
            "\nOperator command interrupted. Check the profile before retrying a write.",
            file=sys.stderr,
        )
        return 130
    except (
        LocalProfileStoreError,
        GoogleDriveError,
        SynologyRecoveryConfigurationError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    except StorageError:
        print(
            "Sheets operation failed. Confirm this profile's Google access and workbook format. "
            "If creating, inspect Drive and use workbook select or initialize before retrying.",
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            "Local configuration could not be accessed. Check its directory and permissions.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
