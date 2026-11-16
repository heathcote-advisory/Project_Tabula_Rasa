"""Export or validate one explicitly selected entity's complete bookkeeping."""

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tabula_rasa.application.entity_export import ExportError
from tabula_rasa.bookkeeping_export.files import private_output_path, read_artifact, write_artifact
from tabula_rasa.bookkeeping_export.schema import json_schema
from tabula_rasa.errors import StorageError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="New file in a private directory outside any Git checkout.")
    action.add_argument("--validate", type=Path, help="Validate an existing export offline.")
    action.add_argument("--schema", action="store_true", help="Print the public versioned JSON Schema without connecting.")
    parser.add_argument("--entity", help="Explicit authorized bookkeeping entity identity.")
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(json_schema(), indent=2))
        return 0
    if not args.entity or not args.entity.strip():
        parser.error("--entity is required for export and validation")
    database = None
    try:
        if args.validate:
            artifact = read_artifact(args.validate, entity_id=args.entity)
        else:
            # Validate delivery before allocating a pool or reading private data.
            path = private_output_path(args.output)
            from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
            from tabula_rasa.infrastructure.postgres.composition import create_postgres_export_service
            from tabula_rasa.infrastructure.postgres.runtime import Database
            database = Database(DatabaseSettings.from_environment(required=True))
            artifact = create_postgres_export_service(database, args.entity).export()
            write_artifact(path, artifact)
        print(json.dumps({"status": artifact["manifest"]["status"], "format_version": artifact["manifest"]["format_version"],
                          "diagnostic_count": len(artifact["manifest"]["diagnostics"])}))
        return 0
    except (ExportError, StorageError) as error:
        print(json.dumps({"status": "failed", "code": error.code or "storage"}))
        return 1
    except Exception:
        # Configuration and filesystem exceptions may contain secrets/paths.
        print(json.dumps({"status": "failed", "code": "unavailable"}))
        return 1
    finally:
        if database is not None:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
