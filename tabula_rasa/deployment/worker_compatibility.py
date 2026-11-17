"""Installed-worker identity and synthetic compatibility evidence contract."""

import hashlib
import json
import re

from .release import ReleaseError


CHECKS = ["recurring-generation", "deduplication", "two-business-isolation", "bounded-failure-backoff"]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(environ):
    image = environ.get("NAS_WORKER_IMAGE", "")
    config = environ.get("NAS_WORKER_CONFIG_SHA256", "")
    rootfs = environ.get("NAS_WORKER_ROOTFS_SHA256", "")
    if (not re.fullmatch(r"ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}", image)
            or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in (config, rootfs))):
        raise ReleaseError("Configure the audited installed NAS worker registry digest and config/rootfs fingerprints.")
    return {"image": image, "config_sha256": config, "rootfs_sha256": rootfs, "platform": "linux/arm64"}


def verify_image(metadata, expected):
    if (metadata.get("Os") != "linux" or metadata.get("Architecture") != "arm64"
            or fingerprint(metadata.get("Config")) != expected["config_sha256"]
            or fingerprint(metadata.get("RootFS", {}).get("Layers")) != expected["rootfs_sha256"]):
        raise ReleaseError("Registry worker does not match the installed NAS export; do not substitute a rebuild.")


def verify(proof, candidate, environ, *, run_id, attempt, schemas):
    expected = {"version": 1, "worker": identity(environ), "candidate": candidate,
                "run_id": str(run_id), "run_attempt": attempt, "status": "passed"}
    if not isinstance(proof, dict) or any(proof.get(key) != value for key, value in expected.items()):
        raise ReleaseError("Missing or mismatched installed-worker compatibility evidence.")
    rows = proof.get("results")
    if (not isinstance(rows, list) or not rows
            or any(not isinstance(row, dict) or row.get("success") is not True
                   or row.get("checks") != CHECKS or not isinstance(row.get("schema"), str) for row in rows)
            or len({row["schema"] for row in rows}) != len(rows)
            or not set(schemas) <= {row["schema"] for row in rows}):
        raise ReleaseError("Installed-worker probes failed or do not cover both release schemas.")
    return proof
