"""Prompt for an app password and store only its hash in protected local operator configuration."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tabula_rasa.ledger_auth import hash_password  # noqa: E402


def set_password(secrets_path: Path) -> None:
    password = getpass.getpass("New application password: ")
    confirmation = getpass.getpass("Confirm application password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")

    encoded_hash = hash_password(password)
    original = secrets_path.read_text(encoding="utf-8")
    assignment = f"app_password_hash = {json.dumps(encoded_hash)}\n"

    lines = original.splitlines(keepends=True)
    existing_index = next(
        (i for i, line in enumerate(lines) if line.startswith("app_password_hash =")),
        None,
    )
    if existing_index is not None:
        lines[existing_index] = assignment
    else:
        section_index = next(
            (i for i, line in enumerate(lines) if line.lstrip().startswith("[")),
            len(lines),
        )
        lines.insert(section_index, assignment)

    updated = "".join(lines)
    parsed = tomllib.loads(updated)
    if parsed.get("app_password_hash") != encoded_hash:
        raise RuntimeError("Generated password hash failed validation")

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=secrets_path.parent,
        prefix="secrets-",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        temporary_file.write(updated)
        temporary_path = Path(temporary_file.name)

    try:
        os.replace(temporary_path, secrets_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    print("Application password hash saved; plaintext password was not stored.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets",
        type=Path,
        default=PROJECT_ROOT / ".streamlit" / "secrets.toml",
    )
    args = parser.parse_args()
    set_password(args.secrets)


if __name__ == "__main__":
    main()
