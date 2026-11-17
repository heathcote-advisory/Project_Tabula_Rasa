"""Production selectors over disposable SQL and synthetic Drive boundaries."""
import json
import shutil
import subprocess
import tarfile

import pytest
from sqlalchemy import text

from scripts import nas_receipt_backup as runner
from scripts.manage_document_storage import execute
from tabula_rasa.application.documents import DocumentValidationError
from tabula_rasa.infrastructure.document_archive import DocumentArchive
from tabula_rasa.infrastructure.postgres.identity import database_identity
from tests.postgres.document_restore_support import cloned_database
from tests.postgres.test_drive_document_recovery import fixture


def production(database, tmp_path, monkeypatch):
    (payloads, secrets), clients = fixture(database, tmp_path, monkeypatch)
    rows = []
    for entity in ('alpha', 'beta'):
        grant = {**payloads[entity]['source'], 'environment': 'production'}
        reference = secrets.put(grant)
        with database.transaction() as connection:
            connection.execute(text('UPDATE external_connections SET secret_ref=:ref WHERE entity_id=:entity'),
                               {'ref': reference, 'entity': entity})
        rows.append({key: grant[key] for key in ('organization_id', 'entity_id', 'profile_key', 'scope_key')})
    config = dict(database_sha256=database_identity(database), entities=rows, storage=tmp_path,
                  minimum_free_bytes=1, max_capture_bytes=1024, max_documents=4,
                  delay_seconds=1, approval_sha256='a'*64, temporary_directory=tmp_path / 'memory')
    config['temporary_directory'].mkdir(mode=0o700)
    def workspace(config, partial, *, create=False):
        work = config['temporary_directory'] / partial.name
        if create:
            work.mkdir(mode=0o700)
        return work
    monkeypatch.setattr(runner, 'memory_workspace', workspace)
    monkeypatch.setenv('TABULA_RASA_DATABASE_URL', database.engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr('tabula_rasa.infrastructure.postgres.runtime.Database', lambda *a, **k: database)
    monkeypatch.setattr('tabula_rasa.infrastructure.connection_secrets.ConnectionSecrets.from_environment', lambda _: secrets)
    monkeypatch.setattr(database, 'dispose', lambda: None)
    monkeypatch.setattr(runner.time, 'sleep', lambda _: None)
    return config, payloads, secrets, clients


def test_current_production_capture_offline_integrity_and_tenant_isolation(database, tmp_path, monkeypatch):
    config, _, _, clients = production(database, tmp_path, monkeypatch)
    partial = tmp_path / 'attempt'
    partial.mkdir()
    runner.capture(config, partial)
    assert json.loads((partial / 'inventory.json').read_text())['documents'] == 4
    work = config['temporary_directory'] / partial.name
    alpha = DocumentArchive(work / 'plain' / '0', 'alpha')
    beta = DocumentArchive(work / 'plain' / '1', 'beta')
    assert alpha.scope_key != beta.scope_key
    for archive in (alpha, beta):
        assert len(archive.documents) == 2
        for doc in archive.documents:
            assert archive.fetch(archive.entity_id, doc.storage_key).startswith(archive.entity_id.encode())
    with pytest.raises(DocumentValidationError):
        DocumentArchive(work / 'plain' / '0', 'beta')
    # Source files and SQL locations remain authoritative and untouched.
    assert all(client.deletes == 0 for client in clients.values())
    assert sum(client.uploads for client in clients.values()) == 4
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM document_locations')) == 0


@pytest.mark.parametrize('failure', ['database', 'folder', 'budget', 'profile', 'provider', 'staging_grant'])
def test_capture_refuses_wrong_binding_budget_and_unavailable_provider(database, tmp_path, monkeypatch, failure):
    config, payloads, secrets, clients = production(database, tmp_path, monkeypatch)
    selectors = config['entities']
    if failure == 'database':
        config['database_sha256'] = 'f'*64
    elif failure == 'folder':
        selectors[0]['scope_key'] = selectors[1]['scope_key']
    elif failure == 'profile':
        selectors[0]['profile_key'] = selectors[1]['profile_key']
    elif failure == 'budget':
        config['max_documents'] = 1
    elif failure == 'provider':
        clients[selectors[0]['scope_key']].fail = True
    else:
        reference = secrets.put(payloads['alpha']['source'])
        with database.transaction() as connection:
            connection.execute(text("UPDATE external_connections SET secret_ref=:ref WHERE entity_id='alpha'"), {'ref': reference})
    partial = tmp_path / 'attempt'
    partial.mkdir()
    with pytest.raises(Exception):
        runner.capture(config, partial)
    assert not (partial / 'inventory.json').exists()
    assert all(client.deletes == 0 for client in clients.values())


def test_inventory_uses_sql_metadata_without_reading_grants(database, tmp_path, monkeypatch, capsys):
    config, _, _, _ = production(database, tmp_path, monkeypatch)
    def denied(*args, **kwargs):
        raise AssertionError('Inventory must not open custody or Drive.')
    monkeypatch.setattr('tabula_rasa.infrastructure.connection_secrets.ConnectionSecrets.from_environment', denied)
    monkeypatch.setattr('tabula_rasa.google_drive.GoogleDriveClient', denied)
    report = runner.inventory(database, config['database_sha256'])
    rows = report['businesses']
    assert {row['entity_id'] for row in rows} == {'alpha', 'beta'}
    assert [row['documents'] for row in rows] == [2, 2]
    assert all(row['receipt_bytes'] > 0 and row['connection_reference'] for row in rows)
    # The CLI must persist this private report as JSON, not just return SQL values.
    assert json.loads(json.dumps(report))['businesses'] == rows
    output = tmp_path / 'inventory.json'
    arguments = ['inventory', '--database-sha256', config['database_sha256'], '--output', str(output)]
    assert runner.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'inventoried'
    retained = output.read_bytes()
    assert json.loads(retained)['businesses'] == rows
    assert runner.main(arguments) == 1  # Existing private evidence is never overwritten.
    assert output.read_bytes() == retained
    with pytest.raises(ValueError):
        runner.inventory(database, 'f'*64)


@pytest.mark.skipif(not shutil.which('age') or not shutil.which('age-keygen'), reason='Local age tools required.')
def test_encrypted_production_capture_restores_only_to_isolated_targets(database, tmp_path, monkeypatch):
    config, payloads, secrets, _ = production(database, tmp_path, monkeypatch)
    partial = tmp_path / 'attempt'
    partial.mkdir()
    runner.capture(config, partial)
    key = tmp_path / 'identity'
    subprocess.run(['age-keygen', '-o', str(key)], capture_output=True, check=True)
    recipients = tmp_path / 'recipients'
    recipients.write_bytes(subprocess.run(['age-keygen', '-y', str(key)], capture_output=True, check=True).stdout)
    config.update(recipients_file=recipients, recipients_sha256=runner.nas.digest(recipients),
                  binding_sha256='b'*64, timeout_seconds=60)
    runner.encrypt(config, partial, runner.nas.stamp(runner.nas.instant()))
    restored_tar = tmp_path / 'isolated.tar'
    subprocess.run(['age', '-d', '-i', str(key), '-o', str(restored_tar), str(partial / 'receipts.tar.age')],
                   capture_output=True, check=True)
    recovered = tmp_path / 'recovered'
    with tarfile.open(restored_tar) as bundle:
        bundle.extractall(recovered, filter='data')
    # cloned_database needs the real constructor, which production() stubbed.
    monkeypatch.undo()
    # Restore still uses the synthetic Drive clients created by the fixture.
    clients = {}
    from tests.postgres.test_drive_document_recovery import RecoveryDrive
    for entity in ('alpha', 'beta'):
        grant = payloads[entity]['target']
        clients[grant['scope_key']] = RecoveryDrive(grant['profile_key'], grant['scope_key'])
    monkeypatch.setattr('tabula_rasa.google_drive.GoogleDriveClient',
                        lambda config, grant: clients[grant['receipt_folder']['id']])
    with cloned_database(database) as restored:
        for index, entity in enumerate(('alpha', 'beta')):
            path = recovered / 'receipts' / str(index)
            options = dict(operation='restore', environment='staging', archive_path=path, revision=1,
                           source_database_sha256=database_identity(database),
                           target_database_sha256=database_identity(restored))
            plan = execute(restored, secrets, payloads[entity]['target'], **options)
            result = execute(restored, secrets, payloads[entity]['target'], **options, apply=True,
                             expected_plan=plan['plan_sha256'], approval_sha256='c'*64)
            assert result['promoted'] and result['verified'] == 2
            archive = DocumentArchive(path, entity)
            expected = sorted(archive.fetch(entity, doc.storage_key) for doc in archive.documents)
            assert sorted(clients[payloads[entity]['target']['scope_key']].bytes.values()) == expected
            assert not (partial / 'plain').exists()
