"""Inventory diagnostics expose phase codes, never private failure text."""
from contextlib import contextmanager
import json

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
