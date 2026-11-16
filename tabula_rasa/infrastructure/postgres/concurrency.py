"""Entity-scoped aggregate locks and loaded-token checks, never domain CRUD."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from tabula_rasa.application.baselines import RecordBaseline
from tabula_rasa.errors import StorageConflictError, StorageNotFoundError, StorageValidationError


# SQL identifiers are infrastructure constants, never request values.
AGGREGATE_TABLES = {
    "vehicle": "vehicles", "account": "accounts", "category": "categories", "payee": "payees", "tag": "tags",
    "entry": "entries", "mixed_use": "mixed_use_sources", "schedule": "recurring_schedules",
    "asset": "fixed_assets", "mileage": "mileage_records", "document": "documents",
    "import_batch": "import_batches", "migration_run": "migration_runs",
}


@dataclass(frozen=True, repr=False)
class AggregateKey:
    entity_id: str
    kind: str
    record_id: str

    def __post_init__(self):
        if (not isinstance(self.kind, str) or self.kind not in AGGREGATE_TABLES or
                any(not isinstance(value, str) or not value.strip()
                    for value in (self.entity_id, self.record_id))):
            raise StorageValidationError("Invalid aggregate identity")


def _record_baseline(key: AggregateKey, row: Mapping[str, object]) -> RecordBaseline:
    if row.get("entity_id") != key.entity_id or row.get("id") != key.record_id:
        raise StorageValidationError("Invalid aggregate baseline metadata", code="baseline_invalid")
    return RecordBaseline(
        key.entity_id, key.kind, key.record_id, row["incarnation_id"], row["version"],
    )


def baseline_for(key: AggregateKey, row: Mapping[str, object]) -> str:
    """Attach this token to the mapped record returned by the same load snapshot."""
    return _record_baseline(key, row).encode()


def lock_aggregates(
    connection: Connection, keys: Iterable[AggregateKey],
) -> dict[AggregateKey, Mapping[str, object]]:
    """Lock the complete affected set before details/callbacks, in one order.

    Hierarchy mutations take the existing database entity guard first, matching
    the baseline hierarchy triggers. Include owned sources and merge targets in
    keys. Missing records fail without revealing their identity or another entity.
    """
    ordered = sorted(set(keys), key=lambda key: (key.entity_id, AGGREGATE_TABLES[key.kind], key.record_id))
    for entity_id in sorted({key.entity_id for key in ordered if key.kind in {"account", "category"}}):
        connection.execute(text("SELECT id FROM business_entities WHERE id = :scope FOR UPDATE"), {"scope": entity_id})
    result = {}
    for key in ordered:
        table = AGGREGATE_TABLES[key.kind]
        row = connection.execute(text(
            f"SELECT * FROM {table} WHERE entity_id = :scope AND id = :id FOR UPDATE",
        ), {"scope": key.entity_id, "id": key.record_id}).mappings().one_or_none()
        if row is None:
            raise StorageNotFoundError("The record no longer exists in this entity.", code="not_found")
        result[key] = row
    return result


def check_baseline(
    key: AggregateKey, row: Mapping[str, object], token: str | None, *, required: bool = False,
) -> None:
    """Call on the locked root before any mutation or inline-reference callback.

    A no-token internal operation still takes fresh locks/domain checks but
    cannot detect a stale form. Full legacy snapshots are compared by the domain
    adapter, after the same locks, rather than by SQL-shaped field comparison.
    """
    if token is None:
        if required:
            raise StorageValidationError("A loaded-record baseline is required.", code="baseline_required")
        return
    expected = RecordBaseline.decode(token)
    if expected != _record_baseline(key, row):
        raise StorageConflictError("The record changed. Reload it before trying again.", code="stale")
