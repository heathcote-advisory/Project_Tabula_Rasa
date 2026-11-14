"""Receipt capture publication, enrollment isolation and safe failure boundaries."""

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from scripts import nas_receipt_backup as receipts


@pytest.fixture(autouse=True)
def private_runner_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    storage = tmp_path / 'storage'
    storage.mkdir(mode=0o700)
    temporary = tmp_path / 'temporary'
    temporary.mkdir(mode=0o700)
    (storage / '.tabula-nas-storage').write_text('b' * 32)
    recipients = tmp_path / 'recipients.txt'
    recipients.write_text('age1-public-recipient')
    recipients.chmod(0o600)
    bindings = [dict(environment='staging', organization_id='org-' + entity,
        entity_id=entity, provider='google_drive', profile_key='profile-' + entity,
        scope_key='folder-' + entity, google_drive_oauth={}, google_drive={})
        for entity in ('alpha', 'beta')]
    config = dict(environment='staging', source_sha256='a' * 64,
        storage=str(storage), storage_marker='b' * 32,
        temporary_directory=str(temporary), recipients_file=str(recipients),
        minimum_free_bytes=1, approval_sha256='c' * 64, bindings=bindings)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    monkeypatch.setattr(receipts, 'database_identity', lambda database: 'a' * 64)
    monkeypatch.setattr(receipts, 'require_tmpfs', lambda path: None)
    return path, config


def fake_capture(database, secrets, binding, destination, approval):
    destination.mkdir()
    (destination / 'receipt.bin').write_bytes(binding['entity_id'].encode())
    return dict(entity_id=binding['entity_id'], scope_key=binding['scope_key'],
                documents=1, content_sha256='d' * 64)


def fake_age(args, env, code, **kwargs):
    if '--output' in args:
        Path(args[args.index('--output') + 1]).write_bytes(b'ciphertext')


def test_two_businesses_publish_then_detect_ciphertext_corruption(configured, monkeypatch):
    path, _ = configured
    config = receipts.load_config(path)
    seen = []
    def capture(*args):
        seen.append(args[2]['entity_id'])
        return fake_capture(*args)
    monkeypatch.setattr(receipts, 'capture', capture)
    monkeypatch.setattr(receipts.nas, 'command', fake_age)
    result = receipts.backup(config, object(), object())
    assert result['businesses'] == result['documents'] == 2
    assert seen == ['alpha', 'beta']
    assert list(config['temporary_directory'].iterdir()) == []
    root = receipts.namespace(config) / result['generation']
    assert {p.name for p in root.iterdir()} == {'manifest.json', 'receipts.tar.age'}
    assert receipts.verify(config, result['generation'])['status'] == 'verified'
    (root / 'receipts.tar.age').write_bytes(b'corrupt')
    with pytest.raises(receipts.nas.BackupError, match='integrity'):
        receipts.verify(config, result['generation'])


def test_wrong_database_never_fetches_a_grant(configured, monkeypatch):
    path, _ = configured
    monkeypatch.setattr(receipts, 'database_identity', lambda database: 'f' * 64)
    monkeypatch.setattr(receipts, 'capture', lambda *args: pytest.fail('unexpected grant access'))
    with pytest.raises(receipts.nas.BackupError, match='source'):
        receipts.backup(receipts.load_config(path), object(), object())


@pytest.mark.parametrize('failure', ['second_business', 'encryption'])
def test_failed_capture_retains_previous_generation(configured, monkeypatch, failure):
    path, _ = configured
    config = receipts.load_config(path)
    monkeypatch.setattr(receipts, 'capture', fake_capture)
    monkeypatch.setattr(receipts.nas, 'command', fake_age)
    first = receipts.backup(config, object(), object())
    def fail_capture(*args):
        if args[2]['entity_id'] == 'beta':
            raise RuntimeError('private provider diagnostic')
        return fake_capture(*args)
    def fail_encryption(args, *other, **kwargs):
        if '--output' in args:
            raise RuntimeError('private encryption diagnostic')
    monkeypatch.setattr(receipts, 'capture', fail_capture if failure == 'second_business' else fake_capture)
    monkeypatch.setattr(receipts.nas, 'command', fail_encryption if failure == 'encryption' else fake_age)
    with pytest.raises(RuntimeError):
        receipts.backup(config, object(), object())
    assert receipts.verify(config, first['generation'])['status'] == 'verified'
    root = receipts.namespace(config)
    assert len([p for p in root.iterdir() if not p.name.startswith('incomplete-')]) == 1
    assert list(config['temporary_directory'].iterdir()) == []
    assert not (root / '.lock').exists()


@pytest.mark.parametrize('field', ['entity_id', 'profile_key', 'scope_key'])
def test_duplicate_or_cross_business_enrollment_is_rejected(configured, field):
    path, config = configured
    config['bindings'][1][field] = config['bindings'][0][field]
    path.write_text(json.dumps(config))
    with pytest.raises(receipts.nas.BackupError, match='configuration'):
        receipts.load_config(path)


def test_changed_enrollment_cannot_verify_another_business_archive(configured, monkeypatch):
    path, _ = configured
    config = receipts.load_config(path)
    monkeypatch.setattr(receipts, 'capture', fake_capture)
    monkeypatch.setattr(receipts.nas, 'command', fake_age)
    result = receipts.backup(config, object(), object())
    changed = deepcopy(config)
    changed['bindings'][0]['scope_key'] = 'different-folder'
    with pytest.raises(FileNotFoundError):
        receipts.verify(changed, result['generation'])


def test_storage_inside_temporary_directory_is_rejected(configured):
    path, config = configured
    storage = Path(config['temporary_directory']) / 'storage'
    storage.mkdir(mode=0o700)
    (storage / '.tabula-nas-storage').write_text('b' * 32)
    config['storage'] = str(storage)
    path.write_text(json.dumps(config))
    with pytest.raises(receipts.nas.BackupError, match='configuration'):
        receipts.load_config(path)


def test_namespace_ancestors_are_synced_before_capture(configured, monkeypatch):
    path, _ = configured
    config = receipts.load_config(path)
    root = receipts.namespace(config)
    synced = []
    monkeypatch.setattr(receipts.nas, 'sync_directory', synced.append)
    def capture(*args):
        assert synced[:3] == [root.parent, root.parent.parent, config['storage']]
        return fake_capture(*args)
    monkeypatch.setattr(receipts, 'capture', capture)
    monkeypatch.setattr(receipts.nas, 'command', fake_age)
    assert receipts.backup(config, object(), object())['status'] == 'complete'


def test_cli_suppresses_private_failures(configured, monkeypatch, capsys):
    path, _ = configured
    def fail(*args):
        raise RuntimeError('private-password-and-receipt-content')
    monkeypatch.setattr(receipts, 'load_config', fail)
    assert receipts.main(['run', '--config', str(path)]) == 1
    assert 'private-password-and-receipt-content' not in capsys.readouterr().out
