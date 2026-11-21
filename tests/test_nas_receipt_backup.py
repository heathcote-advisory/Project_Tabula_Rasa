"""Receipt timer policy, private publication and failure handoff contracts."""
from datetime import datetime, timedelta, timezone
import json
import shutil
import subprocess
from uuid import uuid4

import pytest

from scripts import nas_receipt_backup as runner


def private_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    path.chmod(0o600)


def test_disk_scratch_is_refused_before_any_private_bytes_are_written(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.Path, 'read_text', lambda *args, **kwargs: '1 0 0:1 / / rw - ext4 /dev/fake rw\n')
    config = {'temporary_directory': tmp_path, 'max_capture_bytes': 1024}
    with pytest.raises(runner.nas.BackupError, match='configuration'):
        runner.memory_workspace(config, tmp_path / 'attempt', create=True)
    assert not (tmp_path / 'attempt').exists()


@pytest.fixture
def configured(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    storage = tmp_path / 'storage'
    storage.mkdir(mode=0o700)
    temporary = tmp_path / 'memory'
    temporary.mkdir(mode=0o700)
    def workspace(config, partial, *, create=False):
        work = config['temporary_directory'] / partial.name
        if create:
            work.mkdir(mode=0o700)
        return work
    monkeypatch.setattr(runner, 'memory_workspace', workspace)
    marker = storage / '.receipt-backup-marker'
    marker.write_text('b' * 32)
    marker.chmod(0o600)
    recipients = tmp_path / 'recipients.txt'
    recipients.write_text('synthetic-public-recipient')
    recipients.chmod(0o600)
    raw = dict(environment='production', database_sha256='a' * 64, storage=str(storage), temporary_directory=str(temporary),
        storage_marker='b' * 32, recipients_file=str(recipients), approval_sha256='c' * 64,
        entities=[dict(organization_id=str(uuid4()), entity_id=str(uuid4()), profile_key='owner-a', scope_key='folder-a'),
                  dict(organization_id=str(uuid4()), entity_id=str(uuid4()), profile_key='owner-b', scope_key='folder-b')],
        schedule='twice_daily', minimum_free_bytes=1, max_capture_bytes=1024,
        max_documents=10, timeout_seconds=120, delay_seconds=1)
    path = tmp_path / 'config.json'
    private_json(path, raw)
    return path, raw, runner.load_config(path)


@pytest.mark.parametrize('change', [dict(environment='staging'), dict(schedule=''), dict(delay_seconds=0),
    dict(timeout_seconds=True), dict(approval_sha256=''), dict(storage_marker='d'*32)])
def test_invalid_config_refused(configured, change):
    path, raw, _ = configured
    private_json(path, {**raw, **change})
    with pytest.raises((ValueError, runner.nas.BackupError)):
        runner.load_config(path)


def test_distinct_tenant_folder_enrollment_and_new_policy_cannot_inherit_health(configured):
    path, raw, config = configured
    raw['entities'][1]['scope_key'] = raw['entities'][0]['scope_key']
    private_json(path, raw)
    with pytest.raises(ValueError):
        runner.load_config(path)
    raw['entities'][1]['scope_key'] = 'new-folder'
    private_json(path, raw)
    assert runner.load_config(path)['root'] != config['root']


def test_text_entity_ids_and_recipient_rotation_are_bound(configured):
    path, raw, config = configured
    raw['entities'][0]['entity_id'] = 'entity-owner'
    private_json(path, raw)
    selected = runner.load_config(path)
    assert selected['entities'][0]['entity_id'] == 'entity-owner'
    assert selected['root'] != config['root']
    config['recipients_file'].write_text('replacement-public-recipient')
    assert runner.load_config(path)['root'] != selected['root']


def test_application_organization_ids_are_preserved_for_both_profiles(configured):
    path, raw, _ = configured
    for row in raw['entities']:
        row['organization_id'] = uuid4().hex  # Same format as create_organization.
    private_json(path, raw)
    assert runner.load_config(path)['entities'] == raw['entities']


@pytest.mark.parametrize('organization', ['', ' ', None, 1])
def test_missing_organization_binding_is_refused(configured, organization):
    path, raw, _ = configured
    raw['entities'][0]['organization_id'] = organization
    private_json(path, raw)
    with pytest.raises(ValueError):
        runner.load_config(path)


def install_capture(monkeypatch, config, *, fail=False):
    calls = []
    def command(args, *unused, **kwargs):
        calls.append(args)
        if fail:
            raise TimeoutError('private-grant-must-not-escape')
        generation = args[-1]
        partial = config['root'] / 'incomplete' / generation
        plain = config['temporary_directory'] / generation / 'plain'
        plain.mkdir(parents=True, mode=0o700)
        (plain / 'synthetic.bin').write_bytes(b'private-receipt')
        private_json(partial / 'inventory.json', {'documents': 1, 'receipt_bytes': 15})
    monkeypatch.setattr(runner.nas, 'command', command)
    def encrypt(selected, partial, started_at):
        cipher = partial / 'receipts.tar.age'
        cipher.write_bytes(b'encrypted-synthetic')
        cipher.chmod(0o600)
        private_json(partial / 'manifest.json', dict(format=1, status='complete',
            binding_sha256=config['binding_sha256'], archive_bytes=cipher.stat().st_size,
            archive_sha256=runner.nas.digest(cipher), capture_started_at=started_at, completed_at=started_at))
    monkeypatch.setattr(runner, 'encrypt', encrypt)
    return calls


def test_scheduled_success_is_once_per_slot_and_checksum_is_read_back(configured, monkeypatch):
    path, _, config = configured
    now = datetime(2026, 11, 17, 16, tzinfo=timezone.utc)
    monkeypatch.setattr(runner.nas, 'instant', lambda: now)
    calls = install_capture(monkeypatch, config)
    first = runner.backup(config, path, scheduled=True)
    assert first['status'] == 'complete'
    assert runner.backup(config, path, scheduled=True) == {'status': 'no_attempt', 'health': 'healthy'}
    assert len(calls) == 1
    assert runner.health(config, now + timedelta(hours=21))['status'] == 'stale'
    cipher = config['root'] / 'complete' / first['generation'] / 'receipts.tar.age'
    cipher.write_bytes(b'corrupt')
    with pytest.raises(runner.nas.BackupError):
        runner.health(config, now)


def test_failed_slot_is_consumed_preserves_prior_success_and_manual_retry_allowed(configured, monkeypatch):
    path, _, config = configured
    now = datetime(2026, 11, 17, 16, tzinfo=timezone.utc)
    monkeypatch.setattr(runner.nas, 'instant', lambda: now)
    install_capture(monkeypatch, config)
    previous = runner.backup(config, path)['generation']
    now += timedelta(hours=12)
    calls = install_capture(monkeypatch, config, fail=True)
    with pytest.raises(TimeoutError):
        runner.backup(config, path, scheduled=True)
    assert runner.backup(config, path, scheduled=True) == {'status': 'no_attempt', 'health': 'failed'}
    assert len(calls) == 1
    assert runner.nas.read_state(config['root'])['last_success'] == previous
    assert not (config['root'] / '.lock').exists()
    install_capture(monkeypatch, config)
    assert runner.backup(config, path)['status'] == 'complete'


def test_overlap_capacity_and_missing_or_running_state_fail_closed(configured, monkeypatch):
    path, _, config = configured
    config['root'].mkdir(parents=True)
    now = datetime.now(timezone.utc)
    assert runner.health(config, now)['status'] == 'missing'
    with runner.nas.guard(config['root']):
        with pytest.raises(runner.nas.BackupError, match='overlap'):
            runner.backup(config, path)
    private_json(config['root'] / 'status.json', dict(result='running', started_at=runner.nas.stamp(now-timedelta(hours=3))))
    (config['root'] / '.lock').mkdir()
    assert runner.health(config, now)['status'] == 'overdue'
    (config['root'] / '.lock').rmdir()
    assert runner.health(config, now)['status'] == 'unconfirmed'
    monkeypatch.setattr(runner.shutil, 'disk_usage', lambda _: shutil._ntuple_diskusage(10, 9, 1))
    with pytest.raises(runner.nas.BackupError, match='storage'):
        runner.backup(config, path)


def test_safe_failure_output_and_explicit_heartbeat(configured, monkeypatch, capsys):
    path, _, config = configured
    install_capture(monkeypatch, config, fail=True)
    signals = []
    monkeypatch.setattr(runner, 'signal', lambda url, healthy: signals.append(healthy))
    assert runner.main(['run', '--config', str(path), '--notify']) == 1
    assert signals == [False]
    output = capsys.readouterr().out
    assert 'private-grant' not in output and 'folder-a' not in output
    assert json.loads(output)['status'] == 'failed'


def test_encryption_failure_never_advances_complete_state(configured, monkeypatch):
    path, _, config = configured
    install_capture(monkeypatch, config)
    def failure(*args):
        raise runner.nas.BackupError('encryption')
    monkeypatch.setattr(runner, 'encrypt', failure)
    with pytest.raises(runner.nas.BackupError):
        runner.backup(config, path)
    state = runner.nas.read_state(config['root'])
    assert state['result'] == 'failed' and 'last_success' not in state
    assert list(config['temporary_directory'].glob('*/plain/synthetic.bin'))
    assert not list((config['root'] / 'incomplete').glob('*/plain'))
    assert not (config['root'] / 'complete').exists()


def test_historical_verification_cannot_send_healthy_freshness_signal(configured, monkeypatch, capsys):
    path, _, _ = configured
    signals = []
    monkeypatch.setattr(runner, 'signal', lambda url, healthy: signals.append(healthy))
    assert runner.main(['verify', '--config', str(path), '--notify']) == 1
    assert signals == [False]
    assert json.loads(capsys.readouterr().out)['status'] == 'failed'


@pytest.mark.skipif(not shutil.which('age') or not shutil.which('age-keygen'), reason='Local age tools required.')
def test_real_age_ciphertext_roundtrip_and_transient_plaintext_removal(configured, tmp_path):
    _, _, config = configured
    key = tmp_path / 'identity'
    subprocess.run(['age-keygen', '-o', str(key)], capture_output=True, check=True)
    public = subprocess.run(['age-keygen', '-y', str(key)], capture_output=True, check=True).stdout
    config['recipients_file'].write_bytes(public)
    config['recipients_sha256'] = runner.nas.digest(config['recipients_file'])
    partial = tmp_path / 'partial'
    partial.mkdir()
    work = runner.memory_workspace(config, partial, create=True)
    (work / 'plain').mkdir()
    (work / 'plain' / 'synthetic.bin').write_bytes(b'exact-synthetic-bytes')
    private_json(partial / 'inventory.json', {'documents': 1, 'receipt_bytes': 21})
    runner.encrypt(config, partial, runner.nas.stamp(datetime.now(timezone.utc)))
    restored = tmp_path / 'restored.tar'
    subprocess.run(['age', '-d', '-i', str(key), '-o', str(restored), str(partial / 'receipts.tar.age')],
                   capture_output=True, check=True)
    import tarfile
    with tarfile.open(restored) as bundle:
        assert bundle.extractfile('receipts/synthetic.bin').read() == b'exact-synthetic-bytes'
    assert not work.exists()
    assert (partial / 'receipts.tar.age').is_file()


@pytest.mark.parametrize('fail_second_profile', [False, True])
def test_capture_retains_only_safe_phase_across_profiles(configured, monkeypatch, fail_second_profile):
    from types import SimpleNamespace

    _, _, config = configured
    partial = config['storage'] / 'attempt'
    partial.mkdir(mode=0o700)
    disposed = []
    database = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setattr(runner, 'open_database', lambda: database)
    monkeypatch.setattr('tabula_rasa.infrastructure.postgres.identity.database_identity', lambda _: config['database_sha256'])
    monkeypatch.setattr('tabula_rasa.infrastructure.connection_secrets.ConnectionSecrets.from_environment', lambda _: object())
    monkeypatch.setattr('tabula_rasa.infrastructure.postgres.documents.PostgresReceiptRepository',
                        lambda *a, **k: SimpleNamespace(retained_documents=lambda: []))
    rows = {row['entity_id']: row for row in config['entities']}
    monkeypatch.setattr('tabula_rasa.infrastructure.document_archive.DocumentArchive',
                        lambda path, entity: SimpleNamespace(scope_key=rows[entity]['scope_key']))
    calls = []
    def execute(database, secrets, payload, **options):
        calls.append((payload['entity_id'], options['operation'], options.get('apply', False)))
        if fail_second_profile and payload == runner.selector(config['entities'][1]) and options.get('apply'):
            raise RuntimeError('private-provider-message-and-grant')
        return {'plan_sha256': 'f'*64}
    monkeypatch.setattr('scripts.manage_document_storage.execute', execute)
    if fail_second_profile:
        with pytest.raises(RuntimeError):
            runner.capture(config, partial)
        assert not (partial / 'inventory.json').exists()
        expected = 'receipt_capture'
    else:
        runner.capture(config, partial)
        assert len(calls) == 6
        expected = 'inventory_persistence'
    assert disposed == [True]
    phase = (partial / 'capture-phase.json').read_text()
    assert json.loads(phase) == {'phase': expected}
    assert all(row['entity_id'] not in phase and row['scope_key'] not in phase for row in rows.values())
    assert 'private-provider' not in phase
