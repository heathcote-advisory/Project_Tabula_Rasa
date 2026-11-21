"""NAS generations, private subprocess boundary, scheduling and recovery health."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import nas_postgres_backup as nas
from scripts import nas_postgres_source as source


NOW = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)  # Wednesday 22:00 EDT
SENTINEL = "private-password-and-ledger-content"


def private_file(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def configured(tmp_path, monkeypatch):
    # tmp_path is the protected, mounted operator storage in this fixture.
    storage = tmp_path / "storage"
    storage.mkdir(mode=0o700)
    marker = "c" * 32
    private_file(storage / ".tabula-nas-storage", marker)
    service = private_file(tmp_path / "pg_service.conf", """[backup]
host=production.example.invalid
port=5432
dbname=ledger
user=backup_role
sslmode=verify-full
sslrootcert=/test/ca.pem
channel_binding=require
connect_timeout=15
""")
    config = {"environment": "production", "storage": str(storage), "storage_marker": marker,
              "source_sha256": hashlib.sha256(b"production.example.invalid:5432/ledger").hexdigest(),
              "service_file": str(service), "passfile": str(private_file(tmp_path / "pgpass", SENTINEL)),
              "recipients_file": str(private_file(tmp_path / "recipients", "age1test-public-recipient")),
              "minimum_free_bytes": 1}
    path = private_file(tmp_path / "config.json", json.dumps(config))
    monkeypatch.setattr(nas, "instant", lambda: NOW)
    return path, config


@pytest.fixture
def clients(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["env"]["PGSERVICE"] == "backup"
        assert "PGPASSWORD" not in kwargs["env"]
        assert SENTINEL not in " ".join(args)
        output = b""
        if "--version" in args:
            output = f"{args[0]} (PostgreSQL) 18.6\n".encode()
        elif len(args) > 1 and args[1].endswith("nas_postgres_source.py"):
            schema = "connection_custody" if args[-1] == "connection_custody" else "0005_bookkeeping_cutover"
            output = json.dumps({"server_version_num": 180006, "schema_version": schema, "tls": True}).encode()
        elif args[0] == "pg_dump":
            Path(args[args.index("--file") + 1]).write_bytes(b"PGDMP" + SENTINEL.encode())
        elif args[0] == "age" and "--output" in args:
            Path(args[-2]).write_bytes(b"age-encrypted-test-archive")
        return subprocess.CompletedProcess(args, 0, output)
    monkeypatch.setattr(nas.subprocess, "run", run)
    return calls


def load(configured):
    return nas.load_config(configured[0])


def test_complete_generation_is_encrypted_scoped_and_verifiable(configured, clients, monkeypatch, capsys):
    monkeypatch.setenv("PGPASSWORD", SENTINEL)
    monkeypatch.setenv("PGHOST", "wrong-uat")
    monkeypatch.setenv("PGOPTIONS", "unsafe-ambient-options")
    assert nas.main(["run", "--config", str(configured[0])]) == 0
    output = json.loads(capsys.readouterr().out)
    config = load(configured)
    folder = nas.namespace(config) / "complete" / output["generation"]
    assert {p.name for p in folder.iterdir()} == {"database.dump.age", "manifest.json"}
    manifest = nas.verify_generation(config, output["generation"])
    assert manifest["status"] == "complete" and manifest["tls"] is True
    assert manifest["tools"] == {"pg_dump": "18.6", "pg_restore": "18.6", "psql": "18.6"}
    assert SENTINEL not in (folder / "manifest.json").read_text()
    assert nas.health(config, NOW)["status"] == "healthy"
    assert [a[0] for a, _ in clients] == ["pg_dump", "pg_restore", "psql", nas.sys.executable, "pg_dump", "pg_restore", "age"]
    assert all("PGHOST" not in kw["env"] and "PGOPTIONS" not in kw["env"] for _, kw in clients)
    assert not (nas.namespace(config) / ".lock").exists()


@pytest.mark.parametrize("tool,code", [("pg_dump", "dump"), ("pg_restore", "integrity"), ("age", "encryption")])
def test_failure_quarantines_partial_and_preserves_last_success(configured, clients, monkeypatch, capsys, tool, code):
    config = load(configured)
    initial = nas.backup(config)
    original_run = nas.subprocess.run
    def fail(args, **kwargs):
        if args[0] == tool and "--version" not in args:
            if tool == "pg_dump":
                Path(args[-1]).write_text(SENTINEL)
            raise subprocess.CalledProcessError(1, args, stderr=SENTINEL)
        return original_run(args, **kwargs)
    monkeypatch.setattr(nas.subprocess, "run", fail)
    assert nas.main(["run", "--config", str(configured[0])]) == 1
    text = capsys.readouterr().out
    assert SENTINEL not in text and json.loads(text)["code"] == code
    state = nas.read_state(nas.namespace(config))
    assert state["last_success"] == initial["generation"] and state["result"] == "failed"
    assert len(list((nas.namespace(config) / "complete").iterdir())) == 1
    assert len(list((nas.namespace(config) / "incomplete").iterdir())) == 1
    nas.verify_generation(config, initial["generation"])
    assert nas.health(config, NOW)["status"] == "failed"


def test_tampered_ciphertext_fails_verification(configured, clients):
    config = load(configured)
    result = nas.backup(config)
    folder = nas.namespace(config) / "complete" / result["generation"]
    (folder / "database.dump.age").write_bytes(b"tampered")
    with pytest.raises(nas.BackupError, match="integrity"):
        nas.verify_generation(config, result["generation"])


def test_overlap_never_changes_state_or_attempts_remote_call(configured, clients):
    config = load(configured)
    root = nas.namespace(config)
    root.mkdir(parents=True)
    with nas.guard(root):
        with pytest.raises(nas.BackupError, match="overlap"):
            nas.backup(config)
    assert not clients and not (root / "status.json").exists()


def test_interruption_leaves_running_health_and_no_usable_partial(configured, clients, monkeypatch):
    config = load(configured)
    original_run = nas.subprocess.run
    def interrupt(args, **kwargs):
        if args[0] == "pg_dump" and "--version" not in args:
            raise KeyboardInterrupt
        return original_run(args, **kwargs)
    monkeypatch.setattr(nas.subprocess, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        nas.backup(config)
    assert nas.health(config, NOW)["status"] == "running"
    assert not (nas.namespace(config) / "complete").exists()
    # A subsequent scheduler tick cannot retry the interrupted slot.
    assert nas.backup(config, scheduled=True) == {"status": "no_attempt", "health": "running"}


@pytest.mark.parametrize("date,expected", [
    ("2026-03-05T03:00:00+00:00", "2026-03-05T03:00:00+00:00"),
    ("2026-03-09T02:00:00+00:00", "2026-03-09T02:00:00+00:00"),
    ("2026-10-29T02:00:00+00:00", "2026-10-29T02:00:00+00:00"),
    ("2026-11-02T03:00:00+00:00", "2026-11-02T03:00:00+00:00"),
    ("2026-11-02T02:59:00+00:00", "2026-10-29T02:00:00+00:00"),
])
def test_calendar_slots_follow_eastern_dst(date, expected):
    assert nas.stamp(nas.latest_slot(datetime.fromisoformat(date))) == expected


def test_schedule_one_attempt_then_next_slot_and_unlimited_retention(configured, clients, monkeypatch):
    config = load(configured)
    first = nas.backup(config, scheduled=True)
    assert nas.backup(config, scheduled=True)["status"] == "no_attempt"
    monkeypatch.setattr(nas, "instant", lambda: NOW + timedelta(days=4))
    second = nas.backup(config, scheduled=True)
    assert first["generation"] != second["generation"]
    assert len(list((nas.namespace(config) / "complete").iterdir())) == 2
    nas.verify_generation(config, first["generation"])


def test_missed_run_stale_signal_and_bounded_catchup(configured, clients, monkeypatch):
    config = load(configured)
    nas.backup(config, scheduled=True)
    missed = NOW + timedelta(days=4, hours=3)
    assert nas.health(config, missed)["status"] == "stale"
    monkeypatch.setattr(nas, "instant", lambda: missed)
    assert nas.backup(config, scheduled=True)["status"] == "complete"
    too_late = NOW + timedelta(days=8, hours=1)
    monkeypatch.setattr(nas, "instant", lambda: too_late)
    assert nas.backup(config, scheduled=True) == {"status": "no_attempt", "health": "stale"}


def test_production_uat_and_two_databases_do_not_share_archives_or_guards(configured, clients):
    config = load(configured)
    production = nas.backup(config)
    path, original = configured
    def other_config(label, host, database, environment):
        service = private_file(path.parent / f"{label}.service", Path(original["service_file"]).read_text().replace(
            "production.example.invalid", host).replace("dbname=ledger", f"dbname={database}"))
        passfile = private_file(path.parent / f"{label}.pgpass", label + "-private-password")
        selected = {**original, "environment": environment,
                    "source_sha256": hashlib.sha256(f"{host}:5432/{database}".encode()).hexdigest(),
                    "service_file": str(service), "passfile": str(passfile)}
        return nas.load_config(private_file(path.parent / f"{label}.json", json.dumps(selected)))
    uat = other_config("uat", "uat.example.invalid", "ledger", "uat")
    second_db = other_config("other-db", "production.example.invalid", "other", "production")
    with nas.guard(nas.namespace(config)):
        other = nas.backup(uat)
        nas.backup(second_db)
    assert nas.read_state(nas.namespace(config))["last_success"] == production["generation"]
    assert nas.read_state(nas.namespace(uat))["last_success"] == other["generation"]
    with pytest.raises(FileNotFoundError):
        nas.verify_generation(uat, production["generation"])
    observed_passfiles = {kw["env"]["PGPASSFILE"] for _, kw in clients}
    assert observed_passfiles == {str(value["passfile"]) for value in (config, uat, second_db)}


@pytest.mark.parametrize("mutation", ["pooler", "tls", "channel_binding", "identity", "password", "defaults", "dbname", "permissions", "marker"])
def test_invalid_or_unsafe_configuration_never_connects(configured, clients, capsys, mutation):
    path, config = configured
    service = Path(config["service_file"])
    text = service.read_text()
    changes = {"pooler": ("production.example", "production-pooler.example"),
               "tls": ("verify-full", "require"), "channel_binding": ("channel_binding=require", "channel_binding=prefer"),
               "dbname": ("dbname=ledger", "dbname=postgresql://secret@wrong/db")}
    if mutation in changes:
        service.write_text(text.replace(*changes[mutation]))
    elif mutation == "password":
        service.write_text(text + "password=" + SENTINEL)
    elif mutation == "defaults":
        service.write_text("[DEFAULT]\npassword=" + SENTINEL + "\n" + text)
    elif mutation == "identity":
        config["source_sha256"] = "d" * 64
        path.write_text(json.dumps(config))
    elif mutation == "permissions":
        if os.name != "posix":
            # Windows ACLs are outside the NAS POSIX permission boundary.
            service.unlink()
        else:
            service.chmod(0o644)
    else:
        (Path(config["storage"]) / ".tabula-nas-storage").write_text("wrong-mount")
    assert nas.main(["run", "--config", str(path)]) == 1
    assert SENTINEL not in capsys.readouterr().out and not clients


def test_disk_capacity_blocks_dump_and_health_is_actionable(configured, clients, monkeypatch):
    config = load(configured)
    class Disk:
        free = 0
    monkeypatch.setattr(nas.shutil, "disk_usage", lambda _: Disk())
    with pytest.raises(nas.BackupError, match="storage"):
        nas.backup(config)
    assert not clients
    assert nas.health(config, NOW)["status"] == "capacity"


@pytest.mark.parametrize("failure", ["version", "tls", "schema", "invalid_schema", "empty_revision", "timeout",
                                   "certificate", "hostname", "authentication"])
def test_preflight_rejects_incompatible_source_without_exposing_output(configured, clients, monkeypatch, capsys, failure):
    original_run = nas.subprocess.run
    def invalid(args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30, output=SENTINEL)
        if failure in {"certificate", "hostname", "authentication"} and args[-1].endswith("nas_postgres_source.py"):
            raise subprocess.CalledProcessError(1, args, stderr=SENTINEL + " production.example.invalid")
        result = original_run(args, **kwargs)
        if failure == "version" and args[0] == "pg_dump":
            result.stdout = b"pg_dump (PostgreSQL) 17.1"
        elif failure in {"tls", "schema", "invalid_schema", "empty_revision"} and args[-1].endswith("nas_postgres_source.py"):
            data = json.loads(result.stdout)
            data["tls" if failure == "tls" else "schema_version"] = {
                "tls": False, "schema": None, "invalid_schema": "private://invalid", "empty_revision": ","
            }[failure]
            result.stdout = json.dumps(data).encode()
        return result
    monkeypatch.setattr(nas.subprocess, "run", invalid)
    assert nas.main(["preflight", "--config", str(configured[0])]) == 1
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert "production.example.invalid" not in captured.out + captured.err
    assert json.loads(captured.out)["code"] == ("client" if failure in {"version", "timeout"} else "source")


@pytest.fixture
def metadata_connection(monkeypatch):
    import psycopg

    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.pgconn.ssl_in_use = True
    connection.info.server_version = 180006
    connection.execute.return_value.fetchone.return_value = ("0005_bookkeeping_cutover",)
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(psycopg, "connect", connect)
    return connect, connection


@pytest.mark.parametrize("backend_tls", [False, True])
def test_metadata_uses_client_tls_on_same_connection(metadata_connection, backend_tls):
    connect, connection = metadata_connection
    # A provider may terminate client TLS before its PostgreSQL backend.
    def execute(query):
        if "pg_stat_ssl" in query:
            return SimpleNamespace(fetchone=lambda: (backend_tls,))
        return SimpleNamespace(fetchone=lambda: ("0005_bookkeeping_cutover",))
    connection.execute.side_effect = execute
    assert source.source_metadata() == {
        "server_version_num": 180006, "schema_version": "0005_bookkeeping_cutover", "tls": True
    }
    connect.assert_called_once_with("service=backup", sslmode="verify-full", channel_binding="require", connect_timeout=15)
    queries = [call.args[0] for call in connection.execute.call_args_list]
    assert queries[0] == "SET TRANSACTION READ ONLY"
    assert len(queries) == 2 and "public.alembic_version" in queries[1]
    connection.__exit__.assert_called_once()


def test_metadata_plaintext_is_rejected_before_query(metadata_connection):
    _, connection = metadata_connection
    connection.pgconn.ssl_in_use = False
    with pytest.raises(ValueError, match="source"):
        source.source_metadata()
    connection.execute.assert_not_called()
    connection.__exit__.assert_called_once()


@pytest.mark.parametrize("stage", ["certificate", "hostname", "authentication", "missing_table"])
def test_metadata_errors_are_silent(metadata_connection, capsys, stage):
    import psycopg

    connect, connection = metadata_connection
    error = psycopg.OperationalError(stage + SENTINEL + " production.example.invalid")
    if stage == "missing_table":
        connection.execute.side_effect = [MagicMock(), error]
    else:
        connect.side_effect = error
    assert source.main() == 1
    assert capsys.readouterr() == ("", "")


def test_metadata_subprocess_emits_only_safe_metadata(metadata_connection, capsys):
    assert source.main() == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "server_version_num": 180006, "schema_version": "0005_bookkeeping_cutover", "tls": True
    }


def test_status_failure_after_publication_preserves_archive(configured, clients, monkeypatch):
    config = load(configured)
    original = nas.write_json
    def fail(path, data):
        if path.name == "status.json" and data["result"] != "running":
            raise OSError(SENTINEL)
        return original(path, data)
    monkeypatch.setattr(nas, "write_json", fail)
    with pytest.raises(OSError):
        nas.backup(config)
    root = nas.namespace(config)
    generation = next((root / "complete").iterdir()).name
    nas.verify_generation(config, generation)
    assert nas.health(config, NOW)["status"] == "running"


def test_missing_config_has_safe_action_without_application_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TABULA_RASA_DATABASE_URL", SENTINEL)
    assert nas.main(["run", "--config", str(tmp_path / "missing.json")]) == 1
    output = capsys.readouterr().out
    assert SENTINEL not in output and json.loads(output)["action"]


def test_failed_scheduled_slot_is_not_retried_automatically(configured, clients, monkeypatch):
    config = load(configured)
    def fail(*args, **kwargs):
        raise nas.BackupError("source")
    monkeypatch.setattr(nas, "source_info", fail)
    with pytest.raises(nas.BackupError, match="source"):
        nas.backup(config, scheduled=True)
    monkeypatch.setattr(nas, "source_info", lambda _: pytest.fail("Failed slot must not reconnect"))
    assert nas.backup(config, scheduled=True) == {"status": "no_attempt", "health": "failed"}


def test_overdue_running_state_alerts_after_grace(configured, clients, monkeypatch, capsys):
    config = load(configured)
    root = nas.namespace(config)
    root.mkdir(parents=True)
    nas.write_json(root / "status.json", {"result": "running", "started_at": nas.stamp(NOW)})
    assert nas.main(["status", "--config", str(configured[0])]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "running"
    monkeypatch.setattr(nas, "instant", lambda: NOW + timedelta(hours=3))
    assert nas.main(["status", "--config", str(configured[0])]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "overdue"


@pytest.mark.parametrize("host,accepted", [
    ("production-pooler.example.invalid", False),
    ("PRODUCTION-POOLER.EXAMPLE.INVALID", False),
    ("Production-PoOlEr.example.invalid", False),
    ("PRODUCTION.EXAMPLE.INVALID", True),
])
def test_dns_case_cannot_bypass_direct_endpoint_guard(configured, clients, capsys, host, accepted):
    path, config = configured
    service = Path(config["service_file"])
    service.write_text(service.read_text().replace("production.example.invalid", host))
    config["source_sha256"] = hashlib.sha256(f"{host.lower()}:5432/ledger".encode()).hexdigest()
    path.write_text(json.dumps(config))
    assert nas.main(["preflight", "--config", str(path)]) == (0 if accepted else 1)
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == ("compatible" if accepted else "failed")
    if not accepted:
        assert output["code"] == "configuration" and not clients


def test_publication_entries_are_synced_before_durable_success(configured, clients, monkeypatch):
    config = load(configured)
    root = nas.namespace(config)
    synced = []
    original_write = nas.write_json
    def record(directory):
        synced.append(directory)
    def write(path, value):
        if path == root / "status.json" and value["result"] == "complete":
            generation = root / "complete" / value["last_success"]
            assert generation.is_dir()
            # The generation's manifest is persisted before the rename;
            # both rename parents and all new namespace entries are persisted
            # before status can advertise this generation as successful.
            assert synced[-3:] == [root / "complete", root / "incomplete", root]
            assert generation.name in {p.name for p in synced if p.parent == root / "incomplete"}
        original_write(path, value)
    monkeypatch.setattr(nas, "sync_directory", record)
    monkeypatch.setattr(nas, "write_json", write)
    result = nas.backup(config)
    assert result["status"] == "complete"
    assert synced[-1] == root  # status replacement is persisted before return.


def test_publication_sync_failure_preserves_last_success(configured, clients, monkeypatch, capsys):
    config = load(configured)
    previous = nas.backup(config)["generation"]
    root = nas.namespace(config)
    original_sync = nas.sync_directory
    def fail(directory):
        if directory == root / "complete":
            raise OSError(SENTINEL)
        original_sync(directory)
    monkeypatch.setattr(nas, "sync_directory", fail)
    assert nas.main(["run", "--config", str(configured[0])]) == 1
    output = capsys.readouterr().out
    assert SENTINEL not in output and json.loads(output)["status"] == "failed"
    state = nas.read_state(root)
    assert state["result"] == "failed" and state["last_success"] == previous
    assert len(list((root / "complete").iterdir())) == 2
    nas.verify_generation(config, previous)


def test_first_attempt_namespace_is_durable_before_remote_calls(configured, clients, monkeypatch):
    config = load(configured)
    root = nas.namespace(config)
    assert not root.exists()
    synced = []
    original_run = nas.subprocess.run
    def run(args, **kwargs):
        assert synced[:3] == [root, root.parent, config["storage"]]
        state = nas.read_state(root)
        assert state["attempted_slot"] == nas.stamp(NOW) and state["result"] == "running"
        return original_run(args, **kwargs)
    monkeypatch.setattr(nas, "sync_directory", synced.append)
    monkeypatch.setattr(nas.subprocess, "run", run)
    assert nas.backup(config, scheduled=True)["status"] == "complete"


def test_namespace_sync_failure_cannot_start_remote_work(configured, clients, monkeypatch):
    config = load(configured)
    def fail(directory):
        if directory == config["storage"]:
            raise OSError(SENTINEL)
    monkeypatch.setattr(nas, "sync_directory", fail)
    with pytest.raises(OSError):
        nas.backup(config, scheduled=True)
    assert not clients
    assert nas.read_state(nas.namespace(config))["result"] == "running"


def test_recipient_rotation_cannot_mislabel_encryption_custody(configured, clients, monkeypatch):
    config = load(configured)
    original_bytes = config["recipients_file"].read_bytes()
    original_run = nas.subprocess.run
    used = []
    def rotate(args, **kwargs):
        if args[0] == "age" and "--recipients-file" in args:
            copy = Path(args[args.index("--recipients-file") + 1])
            assert copy != config["recipients_file"]
            config["recipients_file"].write_bytes(b"age1rotated-public-recipient")
            used.append(copy.read_bytes())
        return original_run(args, **kwargs)
    monkeypatch.setattr(nas.subprocess, "run", rotate)
    result = nas.backup(config)
    manifest = nas.verify_generation(config, result["generation"])
    assert used == [original_bytes]
    assert manifest["recipients_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert manifest["recipients_sha256"] != nas.digest(config["recipients_file"])
    complete = nas.namespace(config) / "complete" / result["generation"]
    assert not (complete / "recipients.txt").exists()


def test_guard_persists_lock_removal_before_return(configured, monkeypatch):
    root = nas.namespace(load(configured))
    root.mkdir(parents=True)
    synced = []
    def sync(directory):
        assert directory == root and not (root / ".lock").exists()
        synced.append(directory)
    monkeypatch.setattr(nas, "sync_directory", sync)
    with nas.guard(root):
        assert (root / ".lock").exists()
    assert synced == [root]



@pytest.mark.parametrize("date,expected", [
    ("2026-03-08T13:59:00+00:00", "2026-03-08T03:00:00+00:00"),
    ("2026-03-08T14:00:00+00:00", "2026-03-08T14:00:00+00:00"),
    ("2026-11-01T14:59:00+00:00", "2026-11-01T02:00:00+00:00"),
    ("2026-11-01T15:00:00+00:00", "2026-11-01T15:00:00+00:00"),
])
def test_twice_daily_slots_follow_eastern_dst(date, expected):
    assert nas.stamp(nas.latest_slot(datetime.fromisoformat(date), twice_daily=True)) == expected


def test_twice_daily_policy_is_uat_only(configured):
    path, original = configured
    path.write_text(json.dumps({**original, "schedule": "twice_daily"}))
    with pytest.raises(nas.BackupError, match="configuration"):
        load(configured)


def test_twice_daily_capture_and_stale_running_point(configured, clients, monkeypatch):
    path, original = configured
    path.write_text(json.dumps({**original, "environment": "uat", "schedule": "twice_daily"}))
    config = load(configured)
    first = nas.backup(config, scheduled=True)
    root = nas.namespace(config)
    with nas.guard(root):
        with pytest.raises(nas.BackupError, match="overlap"):
            nas.backup(config, scheduled=True)
    monkeypatch.setattr(nas, "instant", lambda: NOW + timedelta(hours=12))
    assert nas.backup(config, scheduled=True)["generation"] != first["generation"]
    assert nas.backup(config, scheduled=True)["status"] == "no_attempt"

    now = NOW + timedelta(hours=32)
    state = nas.read_state(root)
    nas.write_json(root / "status.json", {**state, "result": "running", "started_at": nas.stamp(now)})
    assert nas.health(config, now)["status"] == "stale"



def test_custody_probe_checks_dedicated_schema(metadata_connection):
    _, connection = metadata_connection
    connection.execute.side_effect = [
        MagicMock(), SimpleNamespace(fetchone=lambda: (False,)), MagicMock(),
    ]
    assert source.source_metadata("connection_custody")["schema_version"] == "connection_custody"
    assert "connection_custody.grants" in connection.execute.call_args.args[0]


def test_custody_probe_refuses_ledger(metadata_connection):
    _, connection = metadata_connection
    connection.execute.return_value.fetchone.return_value = (True,)
    with pytest.raises(ValueError, match="source"):
        source.source_metadata("connection_custody")
    assert connection.execute.call_count == 2



def test_custody_capture_has_restricted_dump_and_distinct_manifest(configured, clients):
    path, original = configured
    path.write_text(json.dumps({**original, "environment": "uat", "source_kind": "connection_custody"}))
    config = load(configured)
    generation = nas.backup(config, scheduled=True)["generation"]
    dump = next(args for args, _ in clients if args[0] == "pg_dump" and "--file" in args)
    assert "--schema=connection_custody" in dump and "--strict-names" in dump
    assert nas.verify_generation(config, generation)["source_kind"] == "connection_custody"
    config.pop("source_kind")
    with pytest.raises(nas.BackupError, match="integrity"):
        nas.verify_generation(config, generation)


@pytest.mark.parametrize('failure,expected', [
    (subprocess.CalledProcessError(-9, ['private-command'], output=SENTINEL, stderr=SENTINEL), {'kind': 'exit', 'returncode': -9}),
    (subprocess.TimeoutExpired(['private-command'], 1, output=SENTINEL), {'kind': 'timeout'}),
    (OSError(SENTINEL), {'kind': 'unavailable'}),
])
def test_optional_subprocess_failure_status_contains_only_fixed_metadata(tmp_path, monkeypatch, failure, expected):
    def fail(*a, **k):
        raise failure
    monkeypatch.setattr(nas.subprocess, 'run', fail)
    report = tmp_path / 'process.json'
    with pytest.raises(nas.BackupError, match='source'):
        nas.command(['private-command'], {}, 'source', failure_status=report)
    assert json.loads(report.read_text()) == expected
    assert SENTINEL not in report.read_text()
