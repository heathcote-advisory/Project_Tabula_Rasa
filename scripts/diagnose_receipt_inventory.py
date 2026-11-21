"""One explicitly authorized metadata inventory with payload-free phase results.

Reuse the production runner's read-only query and private report format. Never
print connection strings, SQL, provider messages, grants or business metadata.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path

from sqlalchemy import text

from scripts import nas_receipt_backup as runner
from tabula_rasa.infrastructure.postgres.identity import database_identity


def database_failure_category(error):
    """Map only recognized SQLSTATE values; never render database error text."""
    from sqlalchemy.exc import DBAPIError
    from tabula_rasa.infrastructure.postgres.runtime import DatabaseError

    categories = {'insufficient_privilege', 'undefined_table', 'undefined_column',
                  'query_canceled', 'undefined_function'}
    if isinstance(error, DatabaseError):
        return error.code if error.code in categories else 'unclassified'
    if not isinstance(error, DBAPIError):
        return 'unclassified'
    return {
        '42501': 'insufficient_privilege',
        '42P01': 'undefined_table',
        '42703': 'undefined_column',
        '57014': 'query_canceled',
        '42883': 'undefined_function',
    }.get(getattr(error.orig, 'sqlstate', None), 'unclassified')


class DiagnosticDatabase:
    """Preserve only a fixed category before normal runtime sanitization."""

    def __init__(self, database):
        self.database = database

    @property
    def engine(self):
        return self.database.engine

    @contextmanager
    def transaction(self, *, read_only):
        from sqlalchemy.exc import DBAPIError
        from tabula_rasa.infrastructure.postgres.runtime import DatabaseError, raise_database_error

        if read_only is not True:
            raise ValueError('Diagnostics require a read-only transaction.')
        with self.database.transaction(read_only=True) as connection:
            try:
                yield connection
            except DBAPIError as error:
                failure = DatabaseError(database_failure_category(error))
            else:
                return
            raise_database_error(failure)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    database = None
    completed = []
    phase = 'private_output_configuration'
    result = None
    with runner.private_files():
        try:
            runner.checksum(args.database_sha256)
            if not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
                raise ValueError('A new private report is required.')
            runner.private_directory(args.output.parent)
            completed.append(phase)
            phase = 'database_configuration'
            database = runner.open_database()
            completed.append(phase)
            phase = 'source_binding'
            if database_identity(database) != args.database_sha256:
                raise ValueError('Database binding differs.')
            completed.append(phase)
            phase = 'database_read_session'
            read_database = DiagnosticDatabase(database)
            with read_database.transaction(read_only=True) as connection:
                if connection.scalar(text('SELECT 1')) != 1:
                    raise ValueError('Read session unavailable.')
            completed.append(phase)
            phase = 'metadata_inventory_read'
            report = runner.inventory(read_database, args.database_sha256)
            completed.append(phase)
            phase = 'report_serialization'
            content = json.dumps(report, sort_keys=True, indent=2)
            completed.append(phase)
            phase = 'private_report_write'
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(content)
            completed.append(phase)
            result = {'status': 'inventoried', 'completed_phases': completed}
        except (Exception, KeyboardInterrupt) as error:
            result = {'status': 'failed', 'failed_phase': phase, 'completed_phases': completed,
                      'action': 'Inspect the failed boundary privately; do not retry or expand access without approval.'}
            if phase in {'database_read_session', 'metadata_inventory_read'}:
                result['database_failure'] = database_failure_category(error)
        finally:
            if database is not None:
                try:
                    database.dispose()
                except Exception:
                    result = {'status': 'failed', 'failed_phase': 'database_cleanup',
                              'completed_phases': completed}
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] == 'inventoried' else 1


if __name__ == '__main__':
    raise SystemExit(main())
