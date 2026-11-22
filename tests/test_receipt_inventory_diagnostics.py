"""Inventory diagnostics expose phase codes, never private failure text."""
from contextlib import contextmanager
import json

import pytest
from sqlalchemy.exc import ProgrammingError

from scripts import diagnose_receipt_inventory as diagnostic


def test_invalid_output_stops_before_database_access(tmp_path, monkeypatch, capsys):
    retained = tmp_path / 'inventory.json'
    retained.write_text('retained evidence')
    def forbidden():
        raise AssertionError('No database access for an existing output.')
    monkeypatch.setattr(diagnostic.runner, 'open_database', forbidden)
    assert diagnostic.main(['--database-sha256', 'a'*64, '--output', str(retained)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['failed_phase'] == 'private_output_configuration'
    assert retained.read_text() == 'retained evidence'


def test_connection_failure_exposes_only_its_boundary(tmp_path, monkeypatch, capsys):
    class Unavailable:
        disposed = False

        @contextmanager
        def transaction(self, **kwargs):
            assert kwargs == {'read_only': True}
            raise RuntimeError('private-provider-url-password-and-diagnostics')
            yield

        def dispose(self):
            self.disposed = True

    database = Unavailable()
    monkeypatch.setattr(diagnostic.runner, 'open_database', lambda: database)
    monkeypatch.setattr(diagnostic, 'database_identity', lambda _: 'a'*64)
    output = tmp_path / 'inventory.json'
    assert diagnostic.main(['--database-sha256', 'a'*64, '--output', str(output)]) == 1
    captured = capsys.readouterr().out
    assert 'private-provider' not in captured
    assert json.loads(captured)['failed_phase'] == 'database_read_session'
    assert database.disposed and not output.exists()


@pytest.mark.parametrize('query_fails', [False, True])
def test_cleanup_failure_preserves_first_failure(tmp_path, monkeypatch, capsys, query_fails):
    from tabula_rasa.infrastructure.postgres.runtime import DatabaseError

    class Database:
        @contextmanager
        def transaction(self, **kwargs):
            yield self

        def scalar(self, statement):
            return 1

        def dispose(self):
            raise RuntimeError('private-cleanup-details')

    def inventory(*args):
        if query_fails:
            raise DatabaseError('insufficient_privilege')
        return {'businesses': []}

    monkeypatch.setattr(diagnostic.runner, 'open_database', Database)
    monkeypatch.setattr(diagnostic, 'database_identity', lambda _: 'a'*64)
    monkeypatch.setattr(diagnostic.runner, 'inventory', inventory)
    output = tmp_path / 'inventory.json'
    assert diagnostic.main(['--database-sha256', 'a'*64, '--output', str(output)]) == 1
    captured = capsys.readouterr().out
    assert 'private-cleanup-details' not in captured
    result = json.loads(captured)
    assert result['failed_phase'] == ('metadata_inventory_read' if query_fails else 'database_cleanup')
    if query_fails:
        assert result['database_failure'] == 'insufficient_privilege'
    assert output.exists() is not query_fails


@pytest.mark.parametrize('sqlstate,category', [
    ('42501', 'insufficient_privilege'), ('42P01', 'undefined_table'),
    ('42703', 'undefined_column'), ('57014', 'query_canceled'),
    ('42883', 'undefined_function'), ('private-provider-text', 'unclassified'),
])
def test_query_failure_reports_only_known_categories(tmp_path, monkeypatch, capsys, sqlstate, category):
    class Database:
        @contextmanager
        def transaction(self, **kwargs):
            assert kwargs == {'read_only': True}
            yield self

        def scalar(self, statement):
            return 1

        def dispose(self):
            pass

    class ProviderError(Exception):
        pass

    original = ProviderError('private-provider-password-and-business-data')
    original.sqlstate = sqlstate
    def unavailable(*args):
        raise ProgrammingError('private SQL', {'private': 'parameters'}, original)
    monkeypatch.setattr(diagnostic.runner, 'open_database', Database)
    monkeypatch.setattr(diagnostic, 'database_identity', lambda _: 'a'*64)
    monkeypatch.setattr(diagnostic.runner, 'inventory', unavailable)
    output = tmp_path / 'inventory.json'
    assert diagnostic.main(['--database-sha256', 'a'*64, '--output', str(output)]) == 1
    captured = capsys.readouterr().out
    assert 'private-provider' not in captured and 'parameters' not in captured
    result = json.loads(captured)
    assert result['failed_phase'] == 'metadata_inventory_read'
    assert result['database_failure'] == category
    assert result['completed_phases'][-1] == 'database_read_session'
    assert not output.exists()
