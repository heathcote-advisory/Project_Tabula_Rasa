"""Public format and private operator delivery work without Sheets or live data."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from scripts import export_postgres_entity as command
from tabula_rasa.application.entity_export import EntityExportSnapshot, ExportError
from tabula_rasa.bookkeeping_export.files import private_output_path, read_artifact, write_artifact
from tabula_rasa.bookkeeping_export.format import build_artifact, canonical_json, encoded_value, parse_artifact, validate_artifact
from tabula_rasa.bookkeeping_export.schema import DOMAINS


def empty_snapshot():
    return EntityExportSnapshot("alpha", "0005_bookkeeping_cutover", "2026-10-05T12:00:00.123456Z", {
        name: ({"id": "alpha", "name": "Synthetic entity", "currency_code": "USD",
                "incarnation_id": "synthetic-incarnation", "version": 1,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},) if name == "business_entities" else ()
        for name in DOMAINS
    })


def test_exact_values_and_determinism_do_not_use_float_or_cent_serialization_for_other_scales():
    assert encoded_value(Decimal("-99999999999999999999999999.99"), "money") == "-99999999999999999999999999.99"
    assert encoded_value(Decimal("33.33333"), "percentage") == "33.33333"
    assert encoded_value(Decimal("123.4"), "miles") == "123.4"
    assert encoded_value(None, "date?") is None
    first = build_artifact(empty_snapshot())
    second = build_artifact(replace(empty_snapshot(), snapshot_at="2026-10-05T12:00:01.000000Z"))
    assert first["domains"] == second["domains"]
    assert first["manifest"]["content_sha256"] == second["manifest"]["content_sha256"]
    assert parse_artifact(canonical_json(first), entity_id="alpha") == first


@pytest.mark.parametrize("value,kind", [
    (1.1, "money"), (Decimal("NaN"), "money"), (Decimal("Infinity"), "money"),
    (Decimal("1.001"), "money"), (Decimal("1.234567"), "percentage"), (Decimal("1.11"), "miles"),
    (datetime(2026, 1, 1), "instant"), (True, "integer"), (None, "text"),
])
def test_unrepresentable_values_fail_without_retaining_private_values(value, kind):
    with pytest.raises(ExportError, match="unrepresentable_value") as caught:
        encoded_value(value, kind)
    assert caught.value.__context__ is None


def test_duplicate_json_fields_and_invalid_private_json_do_not_escape_errors():
    for payload in ('{"private-sentinel":', '{"domains":{},"domains":{},"manifest":{}}'):
        with pytest.raises(ExportError, match="format") as caught:
            parse_artifact(payload, entity_id="alpha")
        assert "private-sentinel" not in str(caught.value)
        assert caught.value.__context__ is None


def test_output_is_private_no_clobber_and_atomically_published(tmp_path, monkeypatch):
    artifact = build_artifact(empty_snapshot())
    path = tmp_path / "synthetic.bookkeeping.json"
    write_artifact(path, artifact)
    assert read_artifact(path, entity_id="alpha") == artifact
    before = path.read_bytes()
    with pytest.raises(ExportError, match="output_exists"):
        write_artifact(path, artifact)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".bookkeeping-*.tmp"))

    def fail_publish(*args):
        raise OSError("private-file-and-credential-sentinel")
    monkeypatch.setattr("tabula_rasa.bookkeeping_export.files.os.link", fail_publish)
    other = tmp_path / "failed.json"
    with pytest.raises(ExportError, match="output_write") as caught:
        write_artifact(other, artifact)
    assert caught.value.__context__ is None and "sentinel" not in str(caught.value)
    assert not other.exists() and not list(tmp_path.glob(".bookkeeping-*.tmp"))


def test_output_rejects_git_checkouts_and_missing_directories(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: synthetic", encoding="utf-8")
    child = repo / "nested"
    child.mkdir()
    with pytest.raises(ExportError, match="repository_output"):
        private_output_path(child / "private.json")
    with pytest.raises(ExportError, match="repository_output"):
        private_output_path(Path(__file__).resolve().parents[1] / "private.json")
    with pytest.raises(ExportError, match="output_directory"):
        private_output_path(tmp_path / "missing" / "private.json")


def test_operator_offline_validation_and_schema_do_not_construct_database(tmp_path, monkeypatch, capsys):
    path = tmp_path / "synthetic.json"
    write_artifact(path, build_artifact(empty_snapshot()))
    # Neither mode imports database configuration; malformed configured secrets
    # cannot interfere and are never printed.
    monkeypatch.setenv("TABULA_RASA_DATABASE_URL", "private-url-and-credential-sentinel")
    assert command.main(["--validate", str(path), "--entity", "alpha"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "complete", "format_version": 1, "diagnostic_count": 0}
    assert command.main(["--schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert set(schema["properties"]["domains"]["required"]) == set(DOMAINS)
    assert command.main(["--validate", str(path), "--entity", "beta"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "code": "entity_scope"}


def test_operator_refuses_repository_delivery_before_reading_data(monkeypatch, capsys):
    monkeypatch.setenv("TABULA_RASA_DATABASE_URL", "private-url-and-credential-sentinel")
    path = Path(__file__).resolve().parents[1] / "private.json"
    assert command.main(["--output", str(path), "--entity", "alpha"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "code": "repository_output"}
    assert not path.exists()


def test_manifest_rejects_unsupported_versions_dates_and_byte_boundary():
    artifact = build_artifact(empty_snapshot())
    for field, value in (("format_version", True), ("document_bytes", "embedded"), ("snapshot_at", "2026-02-30T12:00:00Z")):
        changed = {**artifact, "manifest": {**artifact["manifest"], field: value}}
        with pytest.raises(ExportError):
            validate_artifact(changed, entity_id="alpha")


@pytest.mark.parametrize("revision", ["0004_mileage_order", "0005_bookkeeping_cutover",
                                      "0006_identity_control_plane", "0007_identity_sessions", "0008_owner_lock_scope",
                                      "0009_external_connections", "0010_workspace_lifecycle", "0010_onboarding_routes", "0011_control_plane_join", "0012_operations", "0013_accounting_controls", "0014_document_locations", "0015_support_controls", "0016_business_tenancy", "0017_customer_exports", "0018_drive_oauth", "0019_beta_admission", "0020_book_currency", "0021_customer_lifecycle", "0022_drive_recovery_binding"])
def test_control_plane_revision_preserves_the_existing_v1_portable_inventory(revision):
    from jsonschema import Draft202012Validator
    from tabula_rasa.bookkeeping_export.schema import json_schema
    artifact = build_artifact(replace(empty_snapshot(), database_schema_version=revision))
    assert parse_artifact(canonical_json(artifact), entity_id="alpha") == artifact
    Draft202012Validator(json_schema()).validate(artifact)


@pytest.mark.parametrize("revision", ["0003_migration_provenance", "unreviewed-revision"])
def test_supported_database_revision_is_shared_by_export_offline_validation_and_schema(revision):
    from jsonschema import Draft202012Validator, ValidationError
    from tabula_rasa.bookkeeping_export.schema import json_schema

    artifact = build_artifact(empty_snapshot())
    artifact["manifest"]["database_schema_version"] = revision
    with pytest.raises(ExportError, match="unsupported_database_schema"):
        parse_artifact(canonical_json(artifact), entity_id="alpha")
    with pytest.raises(ExportError, match="unsupported_database_schema"):
        build_artifact(replace(empty_snapshot(), database_schema_version=revision))
    with pytest.raises(ValidationError):
        Draft202012Validator(json_schema()).validate(artifact)


@pytest.mark.parametrize("publish_fails", [False, True])
def test_temporary_cleanup_failure_does_not_change_publication_status(tmp_path, monkeypatch, publish_fails):
    artifact = build_artifact(empty_snapshot())
    path = tmp_path / "synthetic.json"

    def fail_cleanup(*args, **kwargs):
        raise OSError("private-cleanup-sentinel")

    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    if publish_fails:
        def fail_publish(*args):
            raise OSError("private-publish-sentinel")
        monkeypatch.setattr("tabula_rasa.bookkeeping_export.files.os.link", fail_publish)
        with pytest.raises(ExportError, match="output_write") as caught:
            write_artifact(path, artifact)
        assert caught.value.__context__ is None
        assert not path.exists()
    else:
        write_artifact(path, artifact)
        assert read_artifact(path, entity_id="alpha") == artifact
