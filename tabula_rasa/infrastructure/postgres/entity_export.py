"""Complete entity-bound canonical records from one committed database snapshot."""

from datetime import timezone

from sqlalchemy import text

from tabula_rasa.application.entity_export import EntityExportSnapshot, ExportError
from tabula_rasa.bookkeeping_export.schema import SUPPORTED_DATABASE_REVISIONS, domains_for_revision
from tabula_rasa.errors import StorageNotFoundError
from .migrations import current_revision
from .operations import repository_operation
from .repository import EntityRepository


class PostgresEntityExportRepository(EntityRepository):
    def read_export(self):
        with repository_operation(self.database, read_only=True, require_outer=True) as connection:
            return self.read_snapshot(connection)

    def read_snapshot(self, connection):
        """Share the caller's coherent acceptance snapshot; never open a second one."""
        revision = current_revision(connection)
        if revision not in SUPPORTED_DATABASE_REVISIONS:
            raise ExportError("unsupported_database_schema")
        inventory = domains_for_revision(revision)
        columns = {}
        for table, column in connection.execute(text(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=ANY(:tables)"
        ), {"tables": list(inventory)}):
            columns.setdefault(table, set()).add(column)
        if columns != {name: set(spec.fields) for name, spec in inventory.items()}:
            raise ExportError("unsupported_database_schema")
        params = {"scope": self.entity_id}
        timestamp = connection.scalar(text("SELECT transaction_timestamp()"))
        records = {}
        for name, spec in inventory.items():
            scope_column = "id" if name == "business_entities" else "entity_id"
            # All identifiers are private format constants, all data bound.
            records[name] = tuple(dict(row) for row in connection.execute(text(
                f"SELECT {','.join(spec.fields)} FROM {name} "
                f"WHERE {scope_column}=:scope ORDER BY {','.join(spec.key)}"
            ), params).mappings())
        if not records["business_entities"]:
            raise StorageNotFoundError("The selected business entity does not exist.")
        return EntityExportSnapshot(
            self.entity_id, revision,
            timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"), records,
        )
