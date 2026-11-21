"""NAS-only logical recovery artifacts. No application imports or live restores.

Uses explicit libpq service/password files, PostgreSQL clients and age. All
diagnostics are fixed codes; provider output and private paths never reach logs.
"""

import argparse
import configparser
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo


UTC = timezone.utc
GRACE = timedelta(hours=2)
CATCH_UP = timedelta(hours=24)
GUIDANCE = {
    "configuration": "Check the protected configuration, service identity and file permissions.",
    "storage": "Check the protected storage mount, marker and available capacity.",
    "overlap": "Inspect the running task; after an interruption confirm it stopped before removing only .lock.",
    "client": "Check the client installation and matching PostgreSQL major versions.",
    "source": "Check the selected direct endpoint, TLS, access and schema revision privately.",
    "dump": "Inspect source access and NAS capacity privately, then request one manual run.",
    "integrity": "Retain evidence and investigate the archive; do not restore or publish it.",
    "encryption": "Check the age recipients and storage privately; do not publish partial output.",
    "unavailable": "Inspect task and storage privately; no recovery success is established.",
}


class BackupError(Exception):
    pass


def instant():
    return datetime.now(UTC)


def stamp(value):
    return value.astimezone(UTC).isoformat()


def parse_time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError
    return result.astimezone(UTC)


def latest_slot(now, *, twice_daily=False):
    """Use unambiguous Eastern wall-clock hours across both DST changes."""
    local = now.astimezone(ZoneInfo("America/New_York"))
    for days in range(8):
        date = local.date() - timedelta(days=days)
        if not twice_daily and date.weekday() not in (2, 6):
            continue
        for hour in ((22, 10) if twice_daily else (22,)):
            candidate = datetime.combine(date, time(hour), local.tzinfo)
            if candidate <= local:
                return candidate.astimezone(UTC)
    raise BackupError("configuration")


def protected(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise BackupError("configuration")
    stat = path.stat()
    if directory != path.is_dir() or (not directory and not path.is_file()):
        raise BackupError("configuration")
    if os.name == "posix" and (stat.st_uid != os.geteuid() or stat.st_mode & 0o077):
        raise BackupError("configuration")
    return path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def sync_directory(path):
    """Persist directory entries on the supported POSIX NAS execution host."""
    if os.name != "posix":
        return  # Windows is a development test host, not a supported NAS runner.
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def load_config(path):
    config = json.loads(protected(path).read_text(encoding="utf-8"))
    required = {"environment", "source_sha256", "storage", "storage_marker", "service_file",
                "passfile", "recipients_file", "minimum_free_bytes"}
    if not required <= config.keys() <= required | {"schedule", "source_kind"} or config["environment"] not in {"production", "uat"}:
        raise BackupError("configuration")
    if "schedule" in config and (config["environment"] != "uat" or config["schedule"] != "twice_daily"):
        raise BackupError("configuration")
    if "source_kind" in config and (config["environment"] != "uat" or config["source_kind"] != "connection_custody"):
        raise BackupError("configuration")
    if not re.fullmatch("[a-f0-9]{64}", config["source_sha256"]):
        raise BackupError("configuration")
    if not re.fullmatch("[a-f0-9]{32}", config["storage_marker"]):
        raise BackupError("configuration")
    if type(config["minimum_free_bytes"]) is not int or config["minimum_free_bytes"] < 1:
        raise BackupError("configuration")
    for key in ("service_file", "passfile", "recipients_file"):
        config[key] = protected(config[key])
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(config["service_file"].read_text(encoding="utf-8"))
    allowed = {"host", "port", "dbname", "user", "sslmode", "sslrootcert", "channel_binding", "connect_timeout"}
    if parser.sections() != ["backup"] or parser.defaults() or set(parser["backup"]) - allowed:
        raise BackupError("configuration")
    service = parser["backup"]
    host = service.get("host", "")
    if (not re.fullmatch(r"[A-Za-z0-9.-]+", host) or "-pooler" in host.lower()
            or service.get("sslmode") != "verify-full"
            or service.get("channel_binding") != "require"
            or service.get("connect_timeout") != "15"
            or not service.get("sslrootcert")):
        raise BackupError("configuration")
    for field in ("dbname", "user"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", service.get(field, "")):
            raise BackupError("configuration")
    port = int(service.get("port", "5432"))
    if not 1 <= port <= 65535:
        raise BackupError("configuration")
    identity = hashlib.sha256(f"{host.lower()}:{port}/{service['dbname']}".encode()).hexdigest()
    if identity != config["source_sha256"]:
        raise BackupError("configuration")
    config["storage"] = protected(config["storage"], directory=True)
    marker = config["storage"] / ".tabula-nas-storage"
    if marker.read_text(encoding="ascii").strip() != config["storage_marker"]:
        raise BackupError("storage")
    return config


def child_environment(config):
    # No ambient PG*, application .env, service fallback or password argument.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(PGSERVICE="backup", PGSERVICEFILE=str(config["service_file"]),
               PGPASSFILE=str(config["passfile"]), PGAPPNAME="tabula-nas-backup",
               LC_ALL="C")
    return env


def command(args, env, code, *, capture=False, timeout=7200, failure_status=None):
    try:
        result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=True, timeout=timeout)
        return result.stdout.decode("utf-8") if capture else ""
    except subprocess.CalledProcessError as error:
        failure = {"kind": "exit", "returncode": error.returncode}
    except subprocess.TimeoutExpired:
        failure = {"kind": "timeout"}
    except (OSError, subprocess.SubprocessError, UnicodeError):
        failure = {"kind": "unavailable"}
    if failure_status is not None:
        write_json(failure_status, failure)
    raise BackupError(code)


def source_info(env, source_kind=None):
    versions = {}
    for tool in ("pg_dump", "pg_restore", "psql"):
        raw = command([tool, "--version"], env, "client", capture=True, timeout=30)
        match = re.search(r"\(PostgreSQL\) (\d+(?:\.\d+)+)", raw)
        if not match:
            raise BackupError("client")
        versions[tool] = match[1]
    probe = [sys.executable, str(Path(__file__).with_name("nas_postgres_source.py"))]
    if source_kind is not None:
        probe.append(source_kind)
    raw = command(probe, env, "source", capture=True, timeout=60)
    metadata = json.loads(raw)
    if (metadata.get("tls") is not True
            or not re.fullmatch(r"[A-Za-z0-9_]+(?:,[A-Za-z0-9_]+)*", metadata.get("schema_version") or "")
            or (source_kind is not None and metadata["schema_version"] != source_kind)):
        raise BackupError("source")
    server_major = metadata["server_version_num"] // 10000
    if any(int(value.split(".")[0]) != server_major for value in versions.values()):
        raise BackupError("client")
    return {**metadata, "tools": versions}


def namespace(config):
    # Endpoint identity, not caller-chosen labels, owns archive and status scope.
    return config["storage"] / config["environment"] / config["source_sha256"]


def available_capacity(config):
    free = shutil.disk_usage(config["storage"]).free
    if free < config["minimum_free_bytes"]:
        raise BackupError("storage")
    return free


@contextmanager
def guard(root):
    lock = root / ".lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise BackupError("overlap") from None
    try:
        yield
    finally:
        lock.rmdir()
        sync_directory(root)


def read_state(root):
    path = root / "status.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def verify_generation(config, generation):
    """Local ciphertext integrity only. Decryption/restore is a separate drill."""
    if not re.fullmatch(r"\d{8}T\d{12}Z-[a-f0-9]{32}", generation):
        raise BackupError("integrity")
    folder = namespace(config) / "complete" / generation
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    archive = folder / "database.dump.age"
    if (manifest.get("format") != 1 or manifest.get("status") != "complete"
            or manifest.get("source_sha256") != config["source_sha256"]
            or manifest.get("environment") != config["environment"]
            or manifest.get("source_kind") != config.get("source_kind")
            or archive.stat().st_size != manifest["archive_bytes"]
            or digest(archive) != manifest["archive_sha256"]):
        raise BackupError("integrity")
    return manifest


def health(config, now):
    root = namespace(config)
    state = read_state(root)
    free = shutil.disk_usage(config["storage"]).free
    twice_daily = config.get("schedule") == "twice_daily"
    slot = latest_slot(now, twice_daily=twice_daily)
    age_limit_exceeded = False
    result = {"status": "healthy", "free_bytes": free,
              "latest_slot": stamp(slot), "last_success": None}
    if not state.get("last_success"):
        result["status"] = "missing"
    else:
        manifest = verify_generation(config, state["last_success"])
        captured = parse_time(manifest["capture_started_at"])
        result.update(last_success=manifest["completed_at"], backup_age_seconds=int((now-captured).total_seconds()))
        age_limit_exceeded = twice_daily and now - captured >= timedelta(hours=20)
        if captured > now or age_limit_exceeded or (captured < slot and now - slot > GRACE):
            result["status"] = "stale"
    if state.get("result") == "failed":
        result["status"] = "failed"
    elif state.get("result") == "running" and not age_limit_exceeded:
        # Give a normal in-flight backup the same bounded grace as the slot.
        result["status"] = "overdue" if now-parse_time(state["started_at"]) > GRACE else "running"
    if free < config["minimum_free_bytes"]:
        result["status"] = "capacity"
    result["action"] = ("None." if result["status"] == "healthy" else
                        "Backup in progress; recheck after the two-hour grace." if result["status"] == "running" else
                        "Inspect task, storage and last verified generation; resolve the cause before one manual run.")
    return result


def backup(config, *, scheduled=False):
    root = namespace(config)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with guard(root):
        now = instant()
        slot = latest_slot(now, twice_daily=config.get("schedule") == "twice_daily")
        state = read_state(root)
        if scheduled:
            if (now-slot > CATCH_UP or state.get("attempted_slot") == stamp(slot)
                    or (state.get("last_success") and
                        parse_time(verify_generation(config, state["last_success"])["capture_started_at"]) >= slot)):
                return {"status": "no_attempt", "health": health(config, now)["status"]}
        state.update(result="running", attempted_slot=stamp(slot), started_at=stamp(now))
        write_json(root / "status.json", state)
        # The first attempt creates environment/database namespace entries.
        # Persist those parents before any remote call can consume this slot.
        sync_directory(root.parent)
        sync_directory(config["storage"])
        generation = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
        partial = root / "incomplete" / generation
        try:
            available_capacity(config)
            env = child_environment(config)
            source_kind = config.get("source_kind")
            metadata = source_info(env, source_kind) if source_kind else source_info(env)
            partial.mkdir(parents=True, mode=0o700)
            plain = partial / "database.dump"
            dump_args = ["pg_dump", "-w", "-Fc", "--file", str(plain)]
            if source_kind == "connection_custody":
                dump_args.extend(["--schema=connection_custody", "--strict-names"])
            command(dump_args, env, "dump")
            if plain.stat().st_size == 0:
                raise BackupError("integrity")
            # Traverse/decompress every archive item, not only its table of contents.
            command(["pg_restore", "--file", os.devnull, str(plain)], env, "integrity")
            archive = partial / "database.dump.age"
            # Rotation changes only future captures: age and the manifest use
            # the same private per-run copy of the public recipient bytes.
            recipients = config["recipients_file"].read_bytes()
            recipient_copy = partial / "recipients.txt"
            recipient_copy.write_bytes(recipients)
            command(["age", "--recipients-file", str(recipient_copy),
                     "--output", str(archive), str(plain)], env, "encryption")
            if archive.stat().st_size == 0:
                raise BackupError("encryption")
            manifest = {"format": 1, "status": "complete", "environment": config["environment"],
                        "source_sha256": config["source_sha256"], "capture_started_at": stamp(now),
                        **metadata,
                        "archive_sha256": digest(archive), "archive_bytes": archive.stat().st_size,
                        "recipients_sha256": hashlib.sha256(recipients).hexdigest()}
            if source_kind:
                manifest["source_kind"] = source_kind
            recipient_copy.unlink()
            plain.unlink()  # Only this invocation's transient plaintext, never a retained generation.
            with archive.open("r+b") as stream:
                os.fsync(stream.fileno())
            manifest["completed_at"] = stamp(instant())
            write_json(partial / "manifest.json", manifest)
            complete = root / "complete"
            complete.mkdir(exist_ok=True, mode=0o700)
            partial.rename(complete / generation)
            # Persist publication and newly created namespace entries before
            # publishing a durable last-success pointer. Errors remain failures.
            for directory in (complete, partial.parent, root):
                sync_directory(directory)
            state.update(result="complete", last_success=generation, completed_at=manifest["completed_at"])
            write_json(root / "status.json", state)
            return {"status": "complete", "generation": generation}
        except Exception as error:
            # Incomplete artifacts remain quarantined on protected storage.
            # Do not erase diagnostic evidence or the last verified backup.
            state.update(result="failed", code=str(error) if isinstance(error, BackupError) else "unavailable")
            write_json(root / "status.json", state)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "run", "scheduled", "status", "verify"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--generation")
    args = parser.parse_args(argv)
    previous_umask = os.umask(0o077)
    try:
        config = load_config(args.config)
        if args.action == "preflight":
            result = {"status": "compatible", **source_info(child_environment(config), config.get("source_kind")),
                      "free_bytes": available_capacity(config)}
            command(["age", "--version"], child_environment(config), "encryption", timeout=30)
        elif args.action == "status":
            result = health(config, instant())
        elif args.action == "verify":
            verify_generation(config, args.generation or "")
            result = {"status": "verified", "scope": "ciphertext_checksum"}
        else:
            result = backup(config, scheduled=args.action == "scheduled")
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] in {"compatible", "complete", "verified", "healthy", "running"} or (
            result["status"] == "no_attempt" and result["health"] in {"healthy", "running"}) else 1
    except (Exception, KeyboardInterrupt) as error:
        code = str(error) if isinstance(error, BackupError) and str(error) in GUIDANCE else "unavailable"
        print(json.dumps({"status": "failed", "code": code, "action": GUIDANCE[code]}, sort_keys=True))
        return 1
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
