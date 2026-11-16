"""Exact canonical encoding and independent completeness/relationship checks."""

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import re

from tabula_rasa.application.entity_export import ExportError
from .schema import DOMAINS, FORMAT_NAME, FORMAT_VERSION, SCALES, SUPPORTED_DATABASE_REVISIONS, domains_for_revision, V1_DOMAINS


def encoded_value(value, kind):
    if value is None and kind.endswith("?"):
        return None
    kind = kind.rstrip("?")
    if kind in SCALES and isinstance(value, Decimal) and value.is_finite():
        encoded = format(value, f".{SCALES[kind]}f")
        if Decimal(encoded) == value:
            return encoded
    elif kind == "instant" and isinstance(value, datetime) and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    elif kind == "date" and type(value) is date:
        return value.isoformat()
    elif ((kind == "text" and isinstance(value, str))
          or (kind == "boolean" and type(value) is bool)
          or (kind == "integer" and type(value) is int)):
        return value
    raise ExportError("unrepresentable_value")


def _valid(value, kind):
    if value is None:
        return kind.endswith("?")
    kind = kind.rstrip("?")
    if kind in {"integer", "boolean"}:
        return type(value) is (int if kind == "integer" else bool)
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    if kind in SCALES:
        scale = SCALES[kind]
        precision = 8 if kind == "percentage" else 28
        return bool(re.fullmatch(rf"-?(0|[1-9][0-9]*)\.[0-9]{{{scale}}}", value)) and len(
            value.lstrip("-").split(".")[0]) <= precision - scale
    if kind in {"date", "instant"}:
        try:
            parsed = date.fromisoformat(value) if kind == "date" else datetime.fromisoformat(value)
            return (parsed.isoformat() == value if kind == "date" else
                    parsed.utcoffset() is not None and value == parsed.astimezone(timezone.utc).isoformat(
                        timespec="microseconds").replace("+00:00", "Z"))
        except ValueError:
            return False
    return kind == "text"


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def content_digest(domains):
    return hashlib.sha256(canonical_json(domains).encode("utf-8")).hexdigest()


def validate_domains(domains, entity_id, *, inventory=None):
    inventory = DOMAINS if inventory is None else inventory
    if not isinstance(domains, dict) or set(domains) != set(inventory):
        raise ExportError("domain_coverage")
    indexes = {}
    for name, spec in inventory.items():
        rows = domains[name]
        if not isinstance(rows, list):
            raise ExportError("format")
        keys = []
        for row in rows:
            if (not isinstance(row, dict) or set(row) != set(spec.fields)
                    or any(not _valid(row[field], kind) for field, kind in spec.fields.items())):
                raise ExportError("record_format")
            if name != "business_entities" and row["entity_id"] != entity_id:
                raise ExportError("entity_scope")
            key = tuple(row[field] for field in spec.key)
            if any(value is None or (isinstance(value, str) and not value.strip()) for value in key):
                raise ExportError("record_identity")
            keys.append(key)
            if "version" in row and (row["version"] <= 0 or not row["incarnation_id"].strip()):
                raise ExportError("record_identity")
        if len(set(keys)) != len(keys) or keys != sorted(keys):
            raise ExportError("record_identity")
        indexes[name] = {row["id"]: row for row in rows if "id" in row}
    if len(domains["business_entities"]) != 1 or domains["business_entities"][0]["id"] != entity_id:
        raise ExportError("entity_scope")
    if domains["business_entities"][0]["currency_code"] not in {"USD", "CAD"}:
        raise ExportError("unsupported_currency")
    if 'vehicles' in inventory:
        names = [row['name'] for row in domains['vehicles']]
        if (len(set(names)) != len(names) or any(not row['name'].strip() or row['name_key'] != row['name']
                for row in domains['vehicles'])):
            raise ExportError('record_identity')
    for name, spec in inventory.items():
        for row in domains[name]:
            for field, target in spec.references.items():
                if row[field] is not None and row[field] not in indexes[target]:
                    raise ExportError("relationship")
    from .validation import validate_bookkeeping
    return validate_bookkeeping(domains, indexes)


def build_artifact(snapshot):
    if snapshot.database_schema_version not in SUPPORTED_DATABASE_REVISIONS:
        raise ExportError("unsupported_database_schema")
    if not snapshot.entity_id.strip() or not _valid(snapshot.snapshot_at, "instant"):
        raise ExportError("snapshot_metadata")
    inventory = domains_for_revision(snapshot.database_schema_version)
    if set(snapshot.domains) != set(inventory):
        raise ExportError("domain_coverage")
    domains = {}
    for name, spec in inventory.items():
        domains[name] = []
        for row in snapshot.domains[name]:
            if set(row) != set(spec.fields):
                raise ExportError("record_format")
            domains[name].append({field: encoded_value(row[field], kind) for field, kind in spec.fields.items()})
        domains[name].sort(key=lambda row: tuple(row[field] for field in spec.key))
    diagnostics = validate_domains(domains, snapshot.entity_id, inventory=inventory)
    return {"manifest": {
        "format": FORMAT_NAME, "format_version": FORMAT_VERSION if inventory is DOMAINS else 1,
        "database_schema_version": snapshot.database_schema_version, "entity_id": snapshot.entity_id,
        "snapshot_at": snapshot.snapshot_at, "counts": {name: len(rows) for name, rows in domains.items()},
        "content_sha256": content_digest(domains),
        "status": "complete_with_diagnostics" if diagnostics else "complete",
        "diagnostics": diagnostics, "document_bytes": "external",
    }, "domains": domains}


def validate_artifact(artifact, *, entity_id):
    """Verify one selected entity offline. A checksum is integrity, not authorship."""
    if not isinstance(artifact, dict) or set(artifact) != {"manifest", "domains"}:
        raise ExportError("format")
    manifest = artifact["manifest"]
    required = {"format", "format_version", "database_schema_version", "entity_id", "snapshot_at", "counts",
                "content_sha256", "status", "diagnostics", "document_bytes"}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ExportError("manifest")
    if (manifest["format"] != FORMAT_NAME or type(manifest["format_version"]) is not int
            or manifest["format_version"] not in {1, FORMAT_VERSION} or manifest["document_bytes"] != "external"
            or not isinstance(manifest["database_schema_version"], str) or not manifest["database_schema_version"]
            or not _valid(manifest["snapshot_at"], "instant")):
        raise ExportError("manifest")
    if manifest["database_schema_version"] not in SUPPORTED_DATABASE_REVISIONS:
        raise ExportError("unsupported_database_schema")
    if not isinstance(entity_id, str) or not entity_id.strip() or manifest["entity_id"] != entity_id:
        raise ExportError("entity_scope")
    inventory = DOMAINS if manifest["format_version"] == FORMAT_VERSION else V1_DOMAINS
    if inventory is not domains_for_revision(manifest["database_schema_version"]):
        raise ExportError("unsupported_database_schema")
    diagnostics = validate_domains(artifact["domains"], entity_id, inventory=inventory)
    expected_counts = {name: len(rows) for name, rows in artifact["domains"].items()}
    if (not isinstance(manifest["counts"], dict) or manifest["counts"] != expected_counts
            or any(type(value) is not int for value in manifest["counts"].values())):
        raise ExportError("domain_counts")
    if manifest["content_sha256"] != content_digest(artifact["domains"]):
        raise ExportError("checksum")
    if manifest["diagnostics"] != diagnostics or manifest["status"] != (
            "complete_with_diagnostics" if diagnostics else "complete"):
        raise ExportError("diagnostics")
    return artifact


def parse_artifact(payload, *, entity_id):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ExportError("format")
            result[key] = value
        return result

    try:
        artifact = json.loads(payload, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        artifact = None
    if artifact is None:
        raise ExportError("format")
    return validate_artifact(artifact, entity_id=entity_id)
