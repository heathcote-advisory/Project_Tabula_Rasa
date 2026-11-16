"""Explicit entity-bound migration checkpoints on the canonical core path.

Only inactive migration targets are supported. No retry, schema initialization,
backend selection or generation of recurring/depreciation history occurs here.
"""

from dataclasses import asdict

from sqlalchemy import text

from tabula_rasa.accounting_postings import accounting_postings
from tabula_rasa.reference_data import as_bool, normalize_payee_name, normalize_tag_ids, normalize_tag_name
from tabula_rasa.migration.model import (
    MANIFEST_VERSION, MAPPING_VERSION, SYNTHESIZED_TIMESTAMP, MigrationError, stable_id,
)
from tabula_rasa.migration.planning import build_plan
from tabula_rasa.migration.documents import validate_context
from tabula_rasa.infrastructure.sheets_migration import snapshot_from_values, timestamp
from tabula_rasa.storage.google_sheets.schema import WORKSHEET_HEADERS

from .accounting import PostgresJournalRepository, PostgresTransactionRepository, entry_values
from .reference_data import PostgresAccountRepository, PostgresCategoryRepository
from .repository import insert
from .runtime import DatabaseError
from .migrations import current_revision, head_revision

ROOT_TABLES = ("vehicles", "accounts", "categories", "payees", "tags", "entries", "mixed_use_sources",
               "recurring_schedules", "fixed_assets", "mileage_records", "documents", "import_batches")


def validate_plan(plan):
    """Revalidate public plans, including the private source context."""
    snapshot = plan.snapshot
    tables = snapshot.tables
    values = {title: [list(fields), *[[row[field] for field in fields] for row in tables[title]]]
              for title, fields in WORKSHEET_HEADERS.items()}
    checked = snapshot_from_values(snapshot.binding, values, extracted_at=snapshot.extracted_at)
    checked = validate_context(type(snapshot)(checked.binding, checked.extracted_at, checked.content, snapshot.context))
    if build_plan(checked) != plan:
        raise MigrationError("The plan does not match its validated frozen snapshot.")


def _object(plan, kind, identity, row):
    created = row.get("created_at") or SYNTHESIZED_TIMESTAMP
    return {"entity_id": plan.snapshot.binding.entity_id, "id": identity,
            "incarnation_id": stable_id(plan.run_id, kind, identity),
            "created_at": created, "updated_at": row.get("updated_at") or created}


class CoreLoader:
    """Representative core load; secondary owners register complete unit loaders."""

    kinds = {"foundation", "simple_entry", "journal", "schedule", "recurring_entry"}

    def load(self, connection, plan, unit):
        scope = plan.snapshot.binding.entity_id
        rows = unit.rows
        if unit.kind == "foundation":
            for title, table, builder in (
                ("Accounts", "accounts", PostgresAccountRepository.values),
                ("Categories", "categories", PostgresCategoryRepository.values),
                ("Payees", "payees", lambda row: self.managed(row, normalize_payee_name)),
                ("Tags", "tags", lambda row: self.managed(row, normalize_tag_name)),
            ):
                # Source hierarchy preflight guarantees at most parent/child depth.
                for row in sorted(rows[title], key=lambda row: (bool(row.get("parent_id")), row["id"])):
                    insert(connection, table, {**_object(plan, title, plan.target_id(title, row["id"]), row), **builder(row)})
        elif unit.kind == "schedule":
            row = rows["RecurringSchedules"][0]
            identity = plan.target_id("RecurringSchedules", row["id"])
            values = {field: row[field] for field in WORKSHEET_HEADERS["RecurringSchedules"]
                      if field not in {"id", "kind", "tag_ids", "created_at", "updated_at"}}
            for field in ("end_date", "payee_id", "total_expense", "business_use_percentage", "attention_at"):
                values[field] = values[field] or None
            insert(connection, "recurring_schedules", {**_object(plan, "schedule", identity, row), **values})
            for tag in normalize_tag_ids(row["tag_ids"]):
                insert(connection, "schedule_tags", {"entity_id": scope, "schedule_id": identity, "tag_id": tag})
        elif unit.kind in {"simple_entry", "recurring_entry", "simple_entry_receipts", "recurring_entry_receipts"}:
            row = rows["Transactions"][0]
            identity = plan.target_id("Transactions", row["id"])
            repository = PostgresTransactionRepository(None, scope)
            insert(connection, "entries", {**_object(plan, "entry", identity, row),
                   "projection_kind": "simple", **entry_values(row)})
            repository.write_lines(connection, identity, accounting_postings([{**row, "id": identity}]))
            for tag in normalize_tag_ids(row["tag_ids"]):
                insert(connection, "entry_tags", {"entity_id": scope, "entry_id": identity, "tag_id": tag})
            if row["recurring_schedule_id"]:
                insert(connection, "recurring_occurrences", {
                    "entity_id": scope, "schedule_id": plan.target_id("RecurringSchedules", row["recurring_schedule_id"]),
                    "scheduled_date": row["recurring_scheduled_date"], "source_kind": row["recurring_source_kind"],
                    "occurrence_type": row["recurring_occurrence_type"], "entry_id": identity,
                    "created_at": row["created_at"],
                })
        elif unit.kind == "journal":
            row = rows["JournalEntries"][0]
            identity = plan.target_id("JournalEntries", row["id"])
            insert(connection, "entries", {**_object(plan, "journal", identity, row),
                   "projection_kind": "journal", "txn_date": row["txn_date"], "description": row["description"]})
            PostgresJournalRepository(None, scope).write_lines(
                connection, identity, sorted(rows["JournalPostings"], key=lambda line: int(line["position"])))

    @staticmethod
    def managed(row, normalizer):
        return {"name": row["name"], "name_key": normalizer(row["name"]), "active": as_bool(row["active"])}

    def verify(self, connection, plan, unit):
        """On operator restart only, reject altered/deleted committed targets."""
        from .migration_readback import unit_differences
        if unit.kind == "foundation":
            for title, table in (("Accounts", "accounts"), ("Categories", "categories"),
                                 ("Payees", "payees"), ("Tags", "tags")):
                count = connection.scalar(text(f"SELECT count(*) FROM {table} WHERE entity_id=:scope"),
                                          {"scope": plan.snapshot.binding.entity_id})
                if count != len(unit.rows[title]):
                    return False
        return not unit_differences(connection, plan, unit)

class PostgresMigrationRunner:
    def __init__(self, database, *, loaders=None, secondary=False):
        self.database = database
        core = CoreLoader()
        self.loaders = {kind: core for kind in core.kinds}
        if secondary:
            from .migration_secondary import SecondaryLoader
            additional = SecondaryLoader()
            self.loaders.update({kind: additional for kind in additional.kinds})
        self.loaders.update(loaders or {})

    def execute(self, plan, *, dry_run=True):
        # Public callers cannot bypass extraction by forging mutable Plan objects.
        validate_plan(plan)
        for loader in {id(value): value for value in self.loaders.values()}.values():
            if hasattr(loader, "preflight"):
                loader.preflight(plan)
        report = plan.report(self.loaders)
        if dry_run:
            return {**report, "dry_run": True, "load_complete": False}
        if report["missing_loaders"]:
            raise MigrationError("Required complete-unit loaders are unavailable; no target writes were attempted.")
        self._prepare(plan)
        loaded = skipped = 0
        try:
            for unit in plan.units:
                with self.database.transaction(require_outer=True) as connection:
                    self._lock(connection, plan)
                    checkpoint = connection.execute(text(
                        "SELECT * FROM migration_units WHERE entity_id=:scope AND run_id=:run AND unit_key=:key FOR UPDATE"
                    ), self._params(plan, key=unit.key)).mappings().one()
                    if checkpoint["unit_sha256"] != unit.sha256:
                        raise MigrationError("A migration unit checkpoint differs from the frozen plan.")
                    if checkpoint["status"] == "loaded":
                        if not self.loaders[unit.kind].verify(connection, plan, unit):
                            raise MigrationError("A committed migration target has changed; stop and inspect the inactive target.")
                        skipped += 1
                        continue
                    self.loaders[unit.kind].load(connection, plan, unit)
                    connection.execute(text(
                        "UPDATE migration_units SET status='loaded',loaded_at=CURRENT_TIMESTAMP "
                        "WHERE entity_id=:scope AND run_id=:run AND unit_key=:key"
                    ), self._params(plan, key=unit.key))
                loaded += 1
        except (MigrationError, DatabaseError):
            # An uncertain commit may already have its atomic loaded checkpoint.
            # Never overwrite it or automatically retry; next explicit run inspects it.
            try:
                with self.database.transaction(require_outer=True) as connection:
                    self._lock(connection, plan)
                    connection.execute(text("UPDATE migration_runs SET status='failed' WHERE entity_id=:scope AND id=:run"), self._params(plan))
            except DatabaseError:
                pass
            raise
        return {**report, "dry_run": False, "loaded_units": loaded, "skipped_units": skipped,
                "load_complete": True, "run_status": "loading"}

    @staticmethod
    def _params(plan, **extra):
        return {"scope": plan.snapshot.binding.entity_id, "run": plan.run_id, **extra}

    def _lock(self, connection, plan):
        # Serializes preparations for a not-yet-created root as well as resumes.
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,254))"), self._params(plan))
        connection.execute(text("SELECT id FROM business_entities WHERE id=:scope FOR UPDATE"), self._params(plan))

    def _prepare(self, plan):
        with self.database.transaction(require_outer=True) as connection:
            if current_revision(connection) != head_revision():
                raise MigrationError("Target schema is not at this checkout's current revision; initialize it explicitly before loading.")
            self._lock(connection, plan)
            root = connection.execute(text("SELECT name,currency_code FROM business_entities WHERE id=:scope"),
                                      self._params(plan)).mappings().first()
            if root is not None and (root["name"] != plan.snapshot.metadata.get("entity_name", plan.snapshot.binding.entity_id)
                                     or root["currency_code"] != "USD"):
                raise MigrationError("Target entity identity/defaults differ from the source binding; use a matching empty inactive target.")
            existing = connection.execute(text("SELECT * FROM migration_runs WHERE entity_id=:scope"), self._params(plan)).mappings().all()
            if existing:
                if len(existing) != 1 or existing[0]["id"] != plan.run_id:
                    raise MigrationError("Target is bound to a different run/source snapshot; use an empty inactive target.")
                run = existing[0]
                binding = plan.snapshot.binding
                if (run["source_profile_key"], run["source_workbook_key"], run["source_snapshot_sha256"],
                    run["manifest_version"], run["mapping_version"]) != (
                        binding.profile_key, binding.workbook_key, plan.snapshot.sha256, MANIFEST_VERSION, MAPPING_VERSION):
                    raise MigrationError("Stored migration binding differs from the frozen plan.")
                mappings = connection.execute(text("SELECT source_record_kind,source_id,target_record_kind,target_id,mapping_reason,source_updated_at "
                                                  "FROM migration_id_mappings WHERE entity_id=:scope AND run_id=:run"), self._params(plan)).mappings().all()
                normalized_mappings = [{**row, 'source_updated_at': timestamp(row['source_updated_at'].isoformat())
                                        if row['source_updated_at'] else None} for row in mappings]
                if sorted(normalized_mappings, key=lambda row: (row['source_record_kind'], row['source_id'])) != sorted(
                    [asdict(row) for row in plan.mappings], key=lambda row: (row['source_record_kind'], row['source_id'])):
                    raise MigrationError("Stored migration identity map differs from the frozen plan.")
                checkpoints = connection.execute(text("SELECT unit_key,unit_kind,unit_sha256 FROM migration_units "
                                                      "WHERE entity_id=:scope AND run_id=:run"), self._params(plan)).all()
                if set(checkpoints) != {(unit.key, unit.kind, unit.sha256) for unit in plan.units}:
                    raise MigrationError("Stored unit manifest differs from the frozen plan.")
            else:
                for table in ROOT_TABLES:
                    if connection.execute(text(f"SELECT 1 FROM {table} WHERE entity_id=:scope LIMIT 1"), self._params(plan)).first():
                        raise MigrationError("Migration requires an empty inactive target entity.")
                scope = plan.snapshot.binding.entity_id
                if not connection.execute(text("SELECT id FROM business_entities WHERE id=:scope"), self._params(plan)).first():
                    insert(connection, "business_entities", {"id": scope, "name": plan.snapshot.metadata.get("entity_name", scope),
                           "incarnation_id": stable_id(plan.run_id, "entity"),
                           "created_at": SYNTHESIZED_TIMESTAMP, "updated_at": SYNTHESIZED_TIMESTAMP})
                insert(connection, "migration_runs", {**_object(plan, "run", plan.run_id, {}),
                    "source_profile_key": plan.snapshot.binding.profile_key,
                    "source_workbook_key": plan.snapshot.binding.workbook_key,
                    "source_kind": "current_workbook", "manifest_version": MANIFEST_VERSION,
                    "mapping_version": MAPPING_VERSION, "source_snapshot_sha256": plan.snapshot.sha256,
                    "extracted_at": plan.snapshot.extracted_at, "status": "prepared"})
                for mapping in plan.mappings:
                    insert(connection, "migration_id_mappings", {"entity_id": scope, "run_id": plan.run_id, **asdict(mapping)})
                for unit in plan.units:
                    insert(connection, "migration_units", {"entity_id": scope, "run_id": plan.run_id,
                           "unit_key": unit.key, "unit_kind": unit.kind, "unit_sha256": unit.sha256, "status": "pending"})
            connection.execute(text("UPDATE migration_runs SET status='loading' WHERE entity_id=:scope AND id=:run"), self._params(plan))
