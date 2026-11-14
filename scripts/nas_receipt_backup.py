"""Explicit staging receipt captures for an externally scheduled NAS task.

Reuse the canonical Drive archive workflow; use private tmpfs for plaintext.
DSM owns the Wednesday/Sunday schedule and abnormal-termination notifications.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
from uuid import uuid4

from scripts import nas_postgres_backup as nas
from scripts.manage_document_storage import execute
from tabula_rasa.application.document_recovery import verify_entity_documents
from tabula_rasa.infrastructure.connection_secrets import ConnectionSecrets
from tabula_rasa.infrastructure.document_archive import DocumentArchive
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.cutover import database_identity
from tabula_rasa.infrastructure.postgres.documents import PostgresReceiptRepository
from tabula_rasa.infrastructure.postgres.runtime import Database


def load_config(path):
    config = json.loads(nas.protected(path).read_text(encoding='utf-8'))
    required = {'environment', 'source_sha256', 'storage', 'storage_marker',
                'recipients_file', 'minimum_free_bytes', 'temporary_directory',
                'approval_sha256', 'bindings'}
    if set(config) != required or config['environment'] != 'staging':
        raise nas.BackupError('configuration')
    for key in ('source_sha256', 'approval_sha256'):
        if not re.fullmatch('[a-f0-9]{64}', config[key]):
            raise nas.BackupError('configuration')
    if (not re.fullmatch('[a-f0-9]{32}', config['storage_marker'])
            or type(config['minimum_free_bytes']) is not int
            or config['minimum_free_bytes'] < 1):
        raise nas.BackupError('configuration')
    bindings = config['bindings']
    fields = {'environment', 'organization_id', 'entity_id', 'provider',
              'profile_key', 'scope_key', 'google_drive_oauth', 'google_drive'}
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= 20:
        raise nas.BackupError('configuration')
    for binding in bindings:
        if (not isinstance(binding, dict) or set(binding) != fields
                or binding['environment'] != 'staging'
                or binding['provider'] != 'google_drive'
                or binding['google_drive_oauth'] != {} or binding['google_drive'] != {}
                or any(not isinstance(binding[key], str) or not binding[key]
                       for key in ('entity_id', 'organization_id', 'profile_key', 'scope_key'))):
            raise nas.BackupError('configuration')
    for key in ('entity_id', 'scope_key', 'profile_key'):
        if len({binding[key] for binding in bindings}) != len(bindings):
            raise nas.BackupError('configuration')
    for key in ('storage', 'temporary_directory'):
        config[key] = nas.protected(config[key], directory=True).resolve()
        if any((parent / '.git').exists() for parent in (config[key], *config[key].parents)):
            raise nas.BackupError('configuration')
    if (config['storage'] == config['temporary_directory']
            or config['temporary_directory'].is_relative_to(config['storage'])
            or config['storage'].is_relative_to(config['temporary_directory'])):
        raise nas.BackupError('configuration')
    if (config['storage'] / '.tabula-nas-storage').read_text().strip() != config['storage_marker']:
        raise nas.BackupError('storage')
    config['recipients_file'] = nas.protected(config['recipients_file'])
    return config


def namespace(config):
    # A changed enrollment has its own generations and health evidence.
    enrollment = hashlib.sha256(json.dumps(config['bindings'], sort_keys=True).encode()).hexdigest()
    return config['storage'] / 'staging-receipts' / config['source_sha256'] / enrollment


def require_tmpfs(path):
    if os.name != 'posix':
        raise nas.BackupError('configuration')
    mounts = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        details, filesystem = line.split(' - ', 1)
        mount = Path(details.split()[4])
        if path.is_relative_to(mount):
            mounts.append((len(mount.parts), filesystem.split()[0]))
    if not mounts or max(mounts)[1] != 'tmpfs':
        raise nas.BackupError('configuration')


def capture(database, secrets, binding, destination, approval):
    options = dict(operation='backup', environment='staging', archive_path=destination)
    plan = execute(database, secrets, binding, **options)
    result = execute(database, secrets, binding, **options, apply=True,
                     expected_plan=plan['plan_sha256'], approval_sha256=approval)
    # Offline readback uses the existing archive parser and canonical byte verifier.
    archive = DocumentArchive(destination, binding['entity_id'])
    repository = PostgresReceiptRepository(database, binding['entity_id'],
        store_kind='google_drive', store_scope_key=binding['scope_key'])
    verified = verify_entity_documents(repository, archive)
    if verified != result['verified']:
        raise nas.BackupError('integrity')
    return {'entity_id': binding['entity_id'], 'scope_key': binding['scope_key'],
            'documents': verified, 'content_sha256': archive.artifact['manifest']['content_sha256']}


def backup(config, database, secrets):
    # Fingerprint validation precedes any grant retrieval or Drive access.
    if database_identity(database) != config['source_sha256']:
        raise nas.BackupError('source')
    require_tmpfs(config['temporary_directory'])
    root = namespace(config)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with nas.guard(root):
        for directory in (root.parent, root.parent.parent, config['storage']):
            nas.sync_directory(directory)
        nas.available_capacity(config)
        generation = nas.instant().strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex
        partial = root / ('incomplete-' + generation)
        partial.mkdir(mode=0o700)
        started = nas.stamp(nas.instant())
        recipients = config['recipients_file'].read_bytes()
        with tempfile.TemporaryDirectory(dir=config['temporary_directory']) as directory:
            temporary = Path(directory)
            recipient_copy = temporary / 'recipients.txt'
            recipient_copy.write_bytes(recipients)
            nas.command(['age', '--version'], dict(os.environ), 'encryption', timeout=30)
            receipts = []
            for index, binding in enumerate(config['bindings']):
                receipts.append(capture(database, secrets, binding,
                    temporary / str(index), config['approval_sha256']))
            plain = temporary / 'receipts.tar'
            with tarfile.open(plain, mode='w') as archive:
                for index in range(len(receipts)):
                    archive.add(temporary / str(index), arcname=str(index))
            ciphertext = partial / 'receipts.tar.age'
            nas.command(['age', '--recipients-file', str(recipient_copy),
                         '--output', str(ciphertext), str(plain)], dict(os.environ), 'encryption')
            if not ciphertext.stat().st_size:
                raise nas.BackupError('encryption')
            with ciphertext.open('r+b') as stream:
                os.fsync(stream.fileno())
            manifest = {'format': 'tabula-receipt-capture-v1', 'environment': 'staging',
                        'source_sha256': config['source_sha256'], 'receipts': receipts,
                        'started_at': started, 'completed_at': nas.stamp(nas.instant()),
                        'archive_bytes': ciphertext.stat().st_size,
                        'archive_sha256': nas.digest(ciphertext),
                        'recipients_sha256': hashlib.sha256(recipients).hexdigest()}
            nas.write_json(partial / 'manifest.json', manifest)
        complete = root / generation
        nas.sync_directory(partial)
        partial.rename(complete)
        nas.sync_directory(root)
        verify(config, generation)
        return {'status': 'complete', 'generation': generation,
                'businesses': len(receipts), 'documents': sum(row['documents'] for row in receipts)}


def verify(config, generation):
    if not re.fullmatch(r'\d{8}T\d{12}Z-[a-f0-9]{32}', generation):
        raise nas.BackupError('integrity')
    root = namespace(config) / generation
    if root.is_symlink():
        raise nas.BackupError('integrity')
    manifest = json.loads(nas.protected(root / 'manifest.json').read_text(encoding='utf-8'))
    ciphertext = nas.protected(root / 'receipts.tar.age')
    expected = [(binding['entity_id'], binding['scope_key']) for binding in config['bindings']]
    if (manifest.get('format') != 'tabula-receipt-capture-v1'
            or manifest.get('environment') != 'staging'
            or manifest.get('source_sha256') != config['source_sha256']
            or [(row['entity_id'], row['scope_key']) for row in manifest['receipts']] != expected
            or ciphertext.stat().st_size != manifest['archive_bytes']
            or nas.digest(ciphertext) != manifest['archive_sha256']):
        raise nas.BackupError('integrity')
    return {'status': 'verified', 'scope': 'ciphertext_checksum'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'verify'])
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--generation')
    args = parser.parse_args(argv)
    previous = os.umask(0o077)
    database = None
    try:
        config = load_config(args.config)
        if args.action == 'verify':
            result = verify(config, args.generation or '')
        else:
            values = dict(os.environ)  # Never read workstation .env fallbacks.
            settings = DatabaseSettings.from_environment(values, required=True)
            if settings.url.query.get('sslmode') != 'verify-full':
                raise nas.BackupError('configuration')
            database = Database(settings)
            result = backup(config, database, ConnectionSecrets.from_environment(values))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'status': 'failed', 'code': 'receipt_capture_failed',
                          'guidance': 'Inspect the protected configuration, source and incomplete generation privately.'}))
        return 1
    finally:
        if database is not None:
            database.dispose()
        os.umask(previous)


if __name__ == '__main__':
    raise SystemExit(main())
