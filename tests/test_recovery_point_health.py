"""Actual component/receipt integrity with offline operator-attested publication."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import UUID

import pytest

from scripts import nas_postgres_backup as nas
from scripts import recovery_point_health as health
from tabula_rasa.bookkeeping_export.files import write_artifact
from tabula_rasa.bookkeeping_export.format import build_artifact
from tests.test_bookkeeping_export import empty_snapshot

START = datetime(2026, 11, 2, 3, tzinfo=timezone.utc)  # Sunday 22:00 EST.
NOW = START + timedelta(hours=10)
ENTITY_A, ENTITY_B = str(UUID(int=1)), str(UUID(int=2))
SENTINEL = 'private-secret-and-receipt-sentinel'


def private(path, data):
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding='utf-8', newline='\n')
    path.chmod(0o600)
    return path


def make_archive(parent, entity, scope):
    root = parent / entity
    root.mkdir(mode=0o700)
    snapshot = empty_snapshot()
    domains = deepcopy(snapshot.domains)
    domains['business_entities'][0]['id'] = entity
    document = {'id': 'a' * 32, 'entity_id': entity, 'created_at': START, 'updated_at': START,
                'incarnation_id': 'synthetic', 'version': 1, 'stored_filename': 'receipt.pdf',
                'original_filename': 'receipt.pdf', 'mime_type': 'application/pdf',
                'byte_size': len(SENTINEL), 'sha256': hashlib.sha256(SENTINEL.encode()).hexdigest(),
                'store_kind': 'google_drive', 'store_scope_key': scope, 'storage_key': 'file-' + entity,
                'open_url': ''}
    domains['documents'] = [document]
    artifact = build_artifact(replace(snapshot, entity_id=entity, domains=domains,
                                     snapshot_at=START.isoformat(timespec='microseconds').replace('+00:00', 'Z')))
    write_artifact(root / 'bookkeeping.json', artifact)
    private(root / 'binding.json', {'format': 'tabula-rasa-receipt-bytes', 'version': 1,
            'entity_id': entity, 'store_kind': 'google_drive', 'scope_key': scope,
            'content_sha256': artifact['manifest']['content_sha256']})
    private(root / ('a' * 32 + '.bin'), SENTINEL)
    cipher = private(parent / (entity + '.age'), 'age-encryption.org/v1\nsynthetic-ciphertext')
    return {'entity_id': entity, 'archive': str(root), 'ciphertext': str(cipher)}


@pytest.fixture
def point(tmp_path):
    storage = tmp_path / 'storage'
    storage.mkdir(mode=0o700)
    private(storage / '.tabula-nas-storage', 'c' * 32)
    configs = {}
    generations = {}
    for key in ('ledger', 'custody'):
        source_id = hashlib.sha256(('db.example.invalid:5432/' + key).encode()).hexdigest()
        service = private(tmp_path / (key + '.service'), '[backup]\nhost=db.example.invalid\nport=5432\n'
                          + 'dbname=' + key + '\nuser=backup\nsslmode=verify-full\n'
                          + 'sslrootcert=/synthetic-ca.pem\nchannel_binding=require\nconnect_timeout=15\n')
        raw = {'environment': 'uat', 'storage': str(storage), 'storage_marker': 'c' * 32,
               'source_sha256': source_id, 'service_file': str(service),
               'passfile': str(private(tmp_path / (key + '.pass'), 'synthetic-only')),
               'recipients_file': str(private(tmp_path / (key + '.recipients'), 'age1synthetic')),
               'minimum_free_bytes': 1}
        if key == 'custody':
            raw['source_kind'] = 'connection_custody'
        config_path = private(tmp_path / (key + '.json'), raw)
        configs[key] = config_path
        source = nas.load_config(config_path)
        generation = '20261102T030000000000Z-' + ('a' if key == 'ledger' else 'b') * 32
        generations[key] = generation
        folder = nas.namespace(source) / 'complete' / generation
        folder.mkdir(parents=True, mode=0o700)
        cipher = private(folder / 'database.dump.age', 'age-encryption.org/v1\nsynthetic-dump')
        manifest = {'format': 1, 'status': 'complete', 'source_sha256': source_id,
                    'environment': 'uat', 'schema_version': 'connection_custody' if key == 'custody' else '0021_customer_lifecycle',
                    'capture_started_at': nas.stamp(START), 'completed_at': nas.stamp(START + timedelta(minutes=1)),
                    'archive_bytes': cipher.stat().st_size, 'archive_sha256': nas.digest(cipher)}
        if key == 'custody':
            manifest['source_kind'] = 'connection_custody'
        private(folder / 'manifest.json', manifest)
        private(nas.namespace(source) / 'status.json', {'result': 'complete', 'last_success': generation})
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    raw = {'ledger_config': str(configs['ledger']), 'custody_config': str(configs['custody']),
           'state_directory': str(state), 'entities': [{'entity_id': ENTITY_A, 'scope_key': 'folder-a'},
                                                     {'entity_id': ENTITY_B, 'scope_key': 'folder-b'}]}
    config_path = private(tmp_path / 'coordinated.json', raw)
    config = health.load_config(config_path)
    spec = {'quiesced_from': nas.stamp(START), 'quiesced_until': nas.stamp(START + timedelta(minutes=5)),
            'approval_sha256': 'd' * 64, 'references_sha256': 'e' * 64, 'verified_references': 6,
            'ledger_generation': generations['ledger'], 'custody_generation': generations['custody'],
            'receipts': [make_archive(tmp_path, ENTITY_A, 'folder-a'), make_archive(tmp_path, ENTITY_B, 'folder-b')]}
    return config, spec, config_path, tmp_path


def test_real_components_and_two_business_archives_publish_then_remain_healthy_monday(point):
    config, spec, _, _ = point
    result = health.publish(config, spec, attested=True, now=NOW)
    assert result['consistency'] == 'operator_attested_quiescence'
    assert health.inspect(config, now=NOW)['status'] == 'healthy'
    assert 'complete_point_stale' in health.inspect(config, now=START + timedelta(days=3, hours=3))['codes']


@pytest.mark.parametrize('fault', ['missing_business', 'duplicate_business', 'foreign_archive', 'bad_byte',
                                   'bad_sql', 'missing_cipher', 'wrong_scope', 'outside_window', 'old_receipt'])
def test_failed_publication_retains_previous_point_and_reports_failure(point, fault):
    config, spec, _, parent = point
    health.publish(config, spec, attested=True, now=NOW)
    previous = nas.read_state(config['root'])['last_success']
    if fault == 'missing_business':
        spec['receipts'].pop()
    elif fault == 'duplicate_business':
        spec['receipts'][1] = spec['receipts'][0]
    elif fault == 'foreign_archive':
        spec['receipts'][0]['archive'] = spec['receipts'][1]['archive']
    elif fault == 'bad_byte':
        (Path(spec['receipts'][0]['archive']) / ('a' * 32 + '.bin')).write_text('changed')
    elif fault == 'bad_sql':
        source = config['ledger']
        (nas.namespace(source) / 'complete' / spec['ledger_generation'] / 'database.dump.age').write_text('changed')
    elif fault == 'missing_cipher':
        Path(spec['receipts'][0]['ciphertext']).unlink()
    elif fault == 'wrong_scope':
        config['entities'][0]['scope_key'] = 'other-folder'
    elif fault == 'old_receipt':
        path = Path(spec['receipts'][0]['archive']) / 'bookkeeping.json'
        artifact = json.loads(path.read_text())
        artifact['manifest']['snapshot_at'] = '2026-10-01T00:00:00.000000Z'
        private(path, artifact)
    else:
        spec['quiesced_from'] = nas.stamp(START + timedelta(seconds=1))
    with pytest.raises(Exception):
        health.publish(config, spec, attested=True, now=NOW)
    assert nas.read_state(config['root'])['last_success'] == previous
    if fault in {'missing_business', 'duplicate_business', 'foreign_archive', 'outside_window'}:
        assert 'point_attempt_failed' in health.inspect(config, now=NOW)['codes']


def test_health_checks_retained_ciphertexts_and_attempts_not_just_reporter_liveness(point):
    config, spec, _, _ = point
    health.publish(config, spec, attested=True, now=NOW)
    source = config['custody']
    state = nas.read_state(nas.namespace(source))
    state['result'] = 'failed'
    nas.write_json(nas.namespace(source) / 'status.json', state)
    assert 'custody_failed' in health.inspect(config, now=NOW)['codes']
    Path(spec['receipts'][0]['ciphertext']).write_text('tampered')
    with pytest.raises(ValueError):
        health.inspect(config, now=NOW)


def test_explicit_optional_twice_daily_does_not_change_default(point):
    config, spec, config_path, _ = point
    assert config['schedule'] == 'wednesday_sunday'
    raw = json.loads(config_path.read_text())
    raw['schedule'] = 'twice_daily'
    for field in ('ledger_config', 'custody_config'):
        path = Path(raw[field])
        component = json.loads(path.read_text())
        component['schedule'] = 'twice_daily'
        private(path, component)
    private(config_path, raw)
    selected = health.load_config(config_path)
    health.publish(selected, spec, attested=True, now=NOW)
    assert 'complete_point_stale' in health.inspect(selected, now=START + timedelta(hours=20))['codes']


def test_operator_attestation_and_chronology_are_explicit(point):
    config, spec, _, _ = point
    with pytest.raises(ValueError):
        health.publish(config, spec, now=NOW)
    spec['quiesced_until'] = nas.stamp(NOW + timedelta(minutes=1))
    with pytest.raises(ValueError):
        health.publish(config, spec, attested=True, now=NOW)
    assert health.inspect(config, now=NOW)['status'] == 'missing'


def test_changed_enrollment_cannot_reuse_an_existing_complete_point(point):
    config, spec, config_path, _ = point
    health.publish(config, spec, attested=True, now=NOW)
    raw = json.loads(config_path.read_text())
    raw['entities'][0]['scope_key'] = 'changed'
    private(config_path, raw)
    with pytest.raises(ValueError):
        health.inspect(health.load_config(config_path), now=NOW)


def test_cli_sanitizes_errors_and_explicit_signal_uses_unhealthy_result(point, monkeypatch, capsys):
    _, _, config_path, _ = point
    calls = []
    monkeypatch.setattr('scripts.operational_heartbeat.signal', lambda url, healthy: calls.append(healthy))
    monkeypatch.setattr(health, 'load_config', lambda path: (_ for _ in ()).throw(ValueError(SENTINEL)))
    assert health.main(['status', '--config', str(config_path), '--notify']) == 1
    output = capsys.readouterr().out
    assert SENTINEL not in output and calls == [False]
    assert json.loads(output)['signal'] == 'confirmed'


def test_signal_failure_does_not_report_success(point, monkeypatch, capsys):
    config, spec, config_path, _ = point
    health.publish(config, spec, attested=True, now=NOW)
    monkeypatch.setattr(health, 'inspect', lambda selected: {'status': 'healthy'})
    def failed(*args):
        raise OSError(SENTINEL)
    monkeypatch.setattr('scripts.operational_heartbeat.signal', failed)
    assert health.main(['status', '--config', str(config_path), '--notify']) == 1
    assert json.loads(capsys.readouterr().out) == {'status': 'healthy', 'signal': 'unconfirmed'}

def test_point_window_cannot_be_advanced_past_retained_sql_generations(point):
    config, spec, _, _ = point
    published = health.publish(config, spec, attested=True, now=NOW)
    path = config['root'] / 'points' / (published['point'] + '.json')
    value = json.loads(path.read_text())
    value['captured_at'] = nas.stamp(START + timedelta(minutes=1))
    private(path, value)
    with pytest.raises(ValueError, match='common window'):
        health.inspect(config, now=NOW)


def test_one_encrypted_bundle_can_cover_two_independently_verified_archives(point):
    config, spec, _, _ = point
    spec['receipts'][1]['ciphertext'] = spec['receipts'][0]['ciphertext']
    health.publish(config, spec, attested=True, now=NOW)
    assert health.inspect(config, now=NOW)['status'] == 'healthy'


def test_protected_sources_cannot_be_swapped(point):
    _, _, config_path, _ = point
    raw = json.loads(config_path.read_text())
    raw['ledger_config'], raw['custody_config'] = raw['custody_config'], raw['ledger_config']
    private(config_path, raw)
    with pytest.raises(ValueError):
        health.load_config(config_path)


def test_status_cli_runs_with_only_the_nas_status_modules(point):
    _, _, config_path, parent = point
    scripts = parent / 'scripts'
    scripts.mkdir()
    source = Path(health.__file__).parent
    for name in ('recovery_point_health.py', 'nas_postgres_backup.py', 'operational_heartbeat.py'):
        shutil.copyfile(source / name, scripts / name)

    result = subprocess.run(
        [sys.executable, '-E', '-S', '-m', 'scripts.recovery_point_health', 'status', '--config', str(config_path)],
        cwd=parent, capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 1, result.stderr
    assert result.stderr == ''
    assert json.loads(result.stdout) == {'status': 'missing', 'codes': ['complete_point_missing']}


def test_status_is_read_only_and_does_not_notify_by_default(point, monkeypatch, capsys):
    config, spec, config_path, _ = point
    health.publish(config, spec, attested=True, now=NOW)
    monkeypatch.setattr(health, 'inspect', lambda selected: {'status': 'healthy'})
    def unexpected(*args):
        pytest.fail('Default status attempted notification')
    monkeypatch.setattr('scripts.operational_heartbeat.signal', unexpected)
    state_before = (config['root'] / 'status.json').read_bytes()
    assert health.main(['status', '--config', str(config_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'healthy'}
    assert (config['root'] / 'status.json').read_bytes() == state_before

def test_publish_cli_checks_artifacts_without_signalling_and_creates_private_state(point, monkeypatch, capsys):
    import os
    _, spec, config_path, parent = point
    spec_path = private(parent / 'point.json', spec)
    def unexpected(*args):
        pytest.fail('Publication attempted notification')
    monkeypatch.setattr('scripts.operational_heartbeat.signal', unexpected)
    assert health.main(['publish', '--config', str(config_path), '--point', str(spec_path),
                        '--attest-quiesced-point']) == 0
    result = json.loads(capsys.readouterr().out)
    config = health.load_config(config_path)
    assert result['consistency'] == 'operator_attested_quiescence'
    if os.name == 'posix':
        for path in (config['root'] / 'status.json', config['root'] / 'points' / (result['point'] + '.json')):
            assert path.stat().st_mode & 0o077 == 0


def test_overlap_is_unhealthy_without_overwriting_completed_point(point):
    config, spec, _, _ = point
    health.publish(config, spec, attested=True, now=NOW)
    previous = (config['root'] / 'status.json').read_bytes()
    (config['root'] / '.lock').mkdir()
    assert 'point_overlap' in health.inspect(config, now=NOW)['codes']
    with pytest.raises(nas.BackupError):
        health.publish(config, spec, attested=True, now=NOW)
    assert (config['root'] / 'status.json').read_bytes() == previous


@pytest.mark.parametrize('elapsed,locked,expected', [
    (timedelta(minutes=1), True, None),
    (nas.GRACE + timedelta(seconds=1), True, 'point_attempt_overdue'),
    (timedelta(minutes=1), False, 'point_attempt_unconfirmed'),
])
def test_active_publication_can_retain_a_healthy_prior_point_within_grace(point, elapsed, locked, expected):
    config, spec, _, _ = point
    health.publish(config, spec, attested=True, now=NOW)
    state = nas.read_state(config['root'])
    started = NOW + timedelta(minutes=1)
    nas.write_json(config['root'] / 'status.json', {
        **state, 'result': 'running', 'started_at': nas.stamp(started),
    })
    if locked:
        (config['root'] / '.lock').mkdir()
    result = health.inspect(config, now=started + elapsed)
    assert result['status'] == ('healthy' if expected is None else 'unhealthy')
    assert result['codes'] == ([] if expected is None else [expected])
    assert result['captured_at'] == nas.stamp(START)


def test_active_publication_does_not_hide_an_overdue_prior_point(point):
    config, spec, _, _ = point
    health.publish(config, spec, attested=True, now=NOW)
    state = nas.read_state(config['root'])
    polled = START + timedelta(days=3, hours=3)
    nas.write_json(config['root'] / 'status.json', {
        **state, 'result': 'running', 'started_at': nas.stamp(polled - timedelta(minutes=1)),
    })
    (config['root'] / '.lock').mkdir()
    result = health.inspect(config, now=polled)
    assert result['status'] == 'unhealthy'
    assert 'complete_point_stale' in result['codes']


def test_first_publication_has_no_prior_point_during_grace(point):
    config, _, _, _ = point
    nas.write_json(config['root'] / 'status.json', {'result': 'running', 'started_at': nas.stamp(NOW)})
    (config['root'] / '.lock').mkdir()
    assert health.inspect(config, now=NOW)['status'] == 'missing'


def test_state_readback_handles_publication_start_crossing_poll(point, monkeypatch):
    config, spec, _, _ = point
    health.publish(config, spec, attested=True, now=NOW)
    real_read = nas.read_state
    started = NOW + timedelta(minutes=1)

    def read_state(root):
        state = real_read(root)
        if root == config['root'] and not (root / '.lock').exists():
            (root / '.lock').mkdir()
            nas.write_json(root / 'status.json', {**state, 'result': 'running', 'started_at': nas.stamp(started)})
        return state

    monkeypatch.setattr(nas, 'read_state', read_state)
    assert health.inspect(config, now=started)['status'] == 'healthy'
