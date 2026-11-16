"""Complete secondary units; existing history is loaded, never generated."""

from tabula_rasa.accounting_postings import accounting_postings
from tabula_rasa.depreciation_posting import asset_schedule, depreciation_entry_id
from tabula_rasa.ledger_validation import decode_receipt_references
from tabula_rasa.reference_data import normalize_tag_ids
from tabula_rasa.migration.documents import document_from_record, enrich_snapshot
from .accounting import PostgresTransactionRepository, entry_values
from .assets import PostgresAssetRepository
from .documents import PostgresReceiptRepository
from .migration import CoreLoader, _object
from .migration_readback import unit_differences
from .repository import insert
from .reference_data import ensure_vehicle_for_label
from .source_accounting import write_tags


class SecondaryLoader:
    kinds = {"documents", "simple_entry_receipts", "recurring_entry_receipts",
             "mixed_pair", "asset_history", "mileage"}

    def load(self, connection, plan, unit):
        scope = plan.snapshot.binding.entity_id
        if unit.kind == "documents":
            repository = PostgresReceiptRepository(None, scope, store_kind="google_drive",
                                                   store_scope_key=plan.snapshot.metadata["document_scope"])
            for row in unit.rows["Documents"]:
                document = document_from_record(row, profile_key=scope)
                insert(connection, "documents", {**_object(plan, "document", document.metadata.document_id, row),
                                                **repository.metadata_values(document)})
        elif unit.kind.endswith("_receipts"):
            CoreLoader().load(connection, plan, unit)
            row = unit.rows["Transactions"][0]
            PostgresTransactionRepository(None, scope).write_receipts(
                connection, plan.target_id("Transactions", row["id"]), row["receipt_references"])
        elif unit.kind == "mixed_pair":
            source = unit.rows["MixedUseExpenses"][0]
            projection = unit.rows["Transactions"][0]
            identity = plan.target_id("MixedUseExpenses", source["id"])
            values = {field: source[field] for field in (
                "txn_date", "total_expense", "business_use_percentage", "allocated_amount", "account_id",
                "category_id", "description", "reference", "allocation_rationale")}
            insert(connection, "mixed_use_sources", {
                **_object(plan, "mixed_use", identity, source), **values, "payee_id": source["payee_id"] or None,
                "expected_projection_entry_id": plan.target_id("Transactions", projection["id"]),
                "relationship_state": "complete",
            })
            write_tags(connection, scope, "mixed_use_tags", "source_id", identity, source["tag_ids"])
            self.entry(connection, plan, projection, owner=("mixed_use_source_id", identity))
            PostgresTransactionRepository(None, scope).write_receipts(
                connection, identity, source["receipt_references"], target_column="mixed_use_source_id")
            if source["recurring_schedule_id"]:
                self.occurrence(connection, plan, source, source_id=identity)
        elif unit.kind == "asset_history":
            source = unit.rows["Assets"][0]
            identity = plan.target_id("Assets", source["id"])
            # The existing asset writer owns overrides, tags and column mapping.
            # It never posts history; the frozen entries below are the only writes.
            PostgresAssetRepository(None, scope).write(connection, {
                **source, "id": identity,
                "source_transaction_id": plan.target_id("Transactions", source["source_transaction_id"])
                if source["source_transaction_id"] else "",
            }, create=True)
            periods = {depreciation_entry_id(source["id"], period.sequence): period for period in asset_schedule(source)}
            for row in unit.rows["Transactions"]:
                self.entry(connection, plan, row, owner=("depreciation_asset_id", identity))
                period = periods[row["id"]]
                insert(connection, "depreciation_events", {
                    "entity_id": scope, "asset_id": identity, "sequence": period.sequence,
                    "entry_id": plan.target_id("Transactions", row["id"]),
                    "scheduled_date": row["txn_date"], "amount": row["amount"], "created_at": row["created_at"],
                })
        elif unit.kind == "mileage":
            row = unit.rows["Mileage"][0]
            identity = plan.target_id("Mileage", row["id"])
            fields = ("trip_date", "miles", "vehicle", "start_location", "destination", "business_purpose", "notes")
            vehicle = ensure_vehicle_for_label(connection, scope, row['vehicle'])
            insert(connection, "mileage_records", {**_object(plan, "mileage", identity, row),
                                                  'vehicle_id': vehicle['id'] if vehicle else None,
                                                  **{field: row[field] for field in fields}})
            write_tags(connection, scope, "mileage_tags", "mileage_id", identity, row["tag_ids"])

    @staticmethod
    def entry(connection, plan, row, *, owner):
        scope = plan.snapshot.binding.entity_id
        identity = plan.target_id("Transactions", row["id"])
        insert(connection, "entries", {**_object(plan, "entry", identity, row), "projection_kind": "simple",
                                      **entry_values(row), owner[0]: owner[1]})
        repository = PostgresTransactionRepository(None, scope)
        repository.write_lines(connection, identity, accounting_postings([{**row, "id": identity}]))
        write_tags(connection, scope, "entry_tags", "entry_id", identity, normalize_tag_ids(row["tag_ids"]))

    @staticmethod
    def occurrence(connection, plan, row, *, source_id):
        insert(connection, "recurring_occurrences", {
            "entity_id": plan.snapshot.binding.entity_id,
            "schedule_id": plan.target_id("RecurringSchedules", row["recurring_schedule_id"]),
            "scheduled_date": row["recurring_scheduled_date"], "source_kind": row["recurring_source_kind"],
            "occurrence_type": row["recurring_occurrence_type"], "mixed_use_source_id": source_id,
            "created_at": row["created_at"],
        })

    def preflight(self, plan):
        # Validate every inventory item before preparation can write run metadata.
        if not plan.snapshot.metadata:
            enrich_snapshot(plan.snapshot)
        if plan.snapshot.metadata.get("documents"):
            repository = PostgresReceiptRepository(None, plan.snapshot.binding.entity_id, store_kind="google_drive",
                                                   store_scope_key=plan.snapshot.metadata["document_scope"])
            for row in plan.snapshot.metadata["documents"]:
                repository.metadata_values(document_from_record(row, profile_key=plan.snapshot.binding.entity_id))
        for row in plan.snapshot.tables["Transactions"]:
            if row["source_type"] == "depreciation" and decode_receipt_references(row["receipt_references"]):
                from tabula_rasa.migration.model import MigrationError
                raise MigrationError("Depreciation entries cannot own receipts.")
        entries = {row["id"] for row in plan.snapshot.tables["Transactions"]}
        for row in plan.snapshot.tables["Assets"]:
            if row["source_transaction_id"] and row["source_transaction_id"] not in entries:
                from tabula_rasa.migration.model import MigrationError
                raise MigrationError("An asset acquisition reference has no source entry; inspect its provenance before loading.")

    def verify(self, connection, plan, unit):
        return not unit_differences(connection, plan, unit)
