"""Explicitly enrolled production Drive byte captures; never installs a timer.

Run with python -m scripts.nas_receipt_backup. Private configuration and output
stay outside Git. Status needs neither a database nor a Google grant.
"""
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import time
from uuid import UUID, uuid4

from scripts import nas_postgres_backup as nas
from scripts.operational_heartbeat import signal
from scripts.recovery_point_health import private_directory, private_files, sha, checksum


def load_config(path):
    raw = json.loads(nas.protected(path).read_text(encoding='utf-8'))
    required = {'environment', 'database_sha256', 'storage', 'storage_marker', 'recipients_file',
                'approval_sha256', 'entities', 'schedule', 'minimum_free_bytes', 'max_capture_bytes',
                'max_documents', 'timeout_seconds', 'delay_seconds', 'temporary_directory'}
    if set(raw) != required or raw['environment'] != 'production':
        raise ValueError('Exact production receipt configuration required.')
    for key in ('database_sha256', 'approval_sha256'):
        checksum(raw[key])
    if not re.fullmatch('[a-f0-9]{32}', raw['storage_marker']):
        raise ValueError('Invalid storage marker.')
    if raw['schedule'] not in {'wednesday_sunday', 'twice_daily'}:
        raise ValueError('Explicit receipt cadence required.')
    for key, maximum in [('minimum_free_bytes', 10**15), ('max_capture_bytes', 10**12),
                         ('max_documents', 100000), ('timeout_seconds', 7200), ('delay_seconds', 60)]:
        if type(raw[key]) is not int or not 1 <= raw[key] <= maximum:
            raise ValueError('Invalid workload limit.')
    entities = raw['entities']
    if not isinstance(entities, list) or not 1 <= len(entities) <= 20:
        raise ValueError('Explicit production enrollment required.')
    for row in entities:
        if set(row) != {'organization_id', 'entity_id', 'profile_key', 'scope_key'}:
            raise ValueError('Exact tenant/profile/folder binding required.')
        if str(UUID(row['organization_id'])) != row['organization_id']:
            raise ValueError('Canonical organization required.')
        if not isinstance(row['entity_id'], str) or not row['entity_id'].strip():
            raise ValueError('Exact canonical entity required.')
        if any(not isinstance(row[key], str) or not row[key].strip() for key in ('profile_key', 'scope_key')):
            raise ValueError('Explicit profile and folder required.')
    for key in ('entity_id', 'scope_key'):
        if len({row[key] for row in entities}) != len(entities):
            raise ValueError('Distinct entities and folders required.')
    storage = private_directory(raw['storage'])
    temporary = private_directory(raw['temporary_directory']).resolve()
    if (temporary.is_relative_to(storage.resolve()) or storage.resolve().is_relative_to(temporary)):
        raise ValueError('Temporary memory storage must be separate from archives.')
    marker = nas.protected(storage / '.receipt-backup-marker')
    if marker.read_text(encoding='utf-8').strip() != raw['storage_marker']:
        raise ValueError('Storage mount differs.')
    recipients = nas.protected(raw['recipients_file'])
    # Changing enrollment, cadence, approval or recipients cannot inherit old health.
    recipients_sha256 = nas.digest(recipients)
    binding = sha({**raw, 'recipients_sha256': recipients_sha256})
    return {**raw, 'recipients_sha256': recipients_sha256, 'storage': storage,
            'recipients_file': recipients, 'binding_sha256': binding,
            'temporary_directory': temporary,
            'root': storage / 'receipt-bytes' / 'production' / raw['database_sha256'] / binding}


def selector(row):
    return {**row, 'environment': 'production', 'provider': 'google_drive',
            'google_drive_oauth': {}, 'google_drive': {}}


def capacity(config):
    # Only ciphertext reaches persistent storage. Plaintext and tar use tmpfs.
    free = shutil.disk_usage(config['storage']).free
    if free < config['minimum_free_bytes'] + config['max_capture_bytes'] + 64 * 1024**2:
        raise nas.BackupError('storage')
    return free


def memory_workspace(config, partial, *, create=False):
    """Fail closed on a disk-backed scratch mount, including symlink aliases."""
    temporary = config['temporary_directory'].resolve()
    if os.name != 'posix':
        raise nas.BackupError('configuration')
    mounts = []
    for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
        details, filesystem = line.split(' - ', 1)
        mount = Path(details.split()[4].replace('\\040', ' '))
        if temporary.is_relative_to(mount):
            mounts.append((len(mount.parts), filesystem.split()[0]))
    if not mounts or max(mounts)[1] != 'tmpfs':
        raise nas.BackupError('configuration')
    if shutil.disk_usage(temporary).free < (2 if create else 1) * config['max_capture_bytes'] + 64 * 1024**2:
        raise nas.BackupError('storage')
    work = temporary / partial.name
    if work.is_symlink() or work.resolve().parent != temporary:
        raise nas.BackupError('configuration')
    if create:
        work.mkdir(mode=0o700)
    return work


def slot(config, now):
    return nas.latest_slot(now, twice_daily=config['schedule'] == 'twice_daily')


def verify_generation(config, generation):
    if not re.fullmatch(r'\d{8}T\d{12}Z-[a-f0-9]{32}', generation):
        raise nas.BackupError('integrity')
    folder = config['root'] / 'complete' / generation
    manifest = json.loads(nas.protected(folder / 'manifest.json').read_text(encoding='utf-8'))
    archive = nas.protected(folder / 'receipts.tar.age')
    if (manifest.get('binding_sha256') != config['binding_sha256']
            or manifest.get('format') != 1 or manifest.get('status') != 'complete'
            or manifest.get('archive_bytes') != archive.stat().st_size
            or manifest.get('archive_sha256') != nas.digest(archive)):
        raise nas.BackupError('integrity')
    return manifest


def health(config, now, *, lock_held=False):
    state = nas.read_state(config['root'])
    result = {'status': 'missing', 'action': 'Inspect receipt task, storage and last verified generation before one manual run.'}
    if state.get('last_success'):
        manifest = verify_generation(config, state['last_success'])
        captured = nas.parse_time(manifest['capture_started_at'])
        stale = captured > now or (captured < slot(config, now) and now-slot(config, now) > nas.GRACE)
        if config['schedule'] == 'twice_daily' and now-captured >= timedelta(hours=20):
            stale = True
        result.update(status='stale' if stale else 'healthy', last_success=manifest['completed_at'],
                      backup_age_seconds=int((now-captured).total_seconds()))
    if state.get('result') in {'running', 'failed'}:
        result['status'] = state['result']
        if state['result'] == 'running' and now-nas.parse_time(state['started_at']) > nas.GRACE:
            result['status'] = 'overdue'
    if not lock_held and (config['root'] / '.lock').exists() != (state.get('result') == 'running'):
        result['status'] = 'unconfirmed'
    capacity(config)
    return result


def open_database():
    from dataclasses import replace
    from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
    from tabula_rasa.infrastructure.postgres.runtime import Database

    settings = DatabaseSettings.from_environment(dict(os.environ), required=True)
    return Database(replace(settings, pool_size=1, max_overflow=0), statement_timeout_ms=30000)


def inventory(database, expected_database):
    """SQL metadata only; no custody reads, Drive calls or automatic enrollment."""
    from sqlalchemy import text
    from tabula_rasa.infrastructure.postgres.identity import database_identity

    if database_identity(database) != checksum(expected_database):
        raise ValueError('Database binding differs.')
    with database.transaction(read_only=True) as connection:
        rows = connection.execute(text('''SELECT e.id AS entity_id, o.organization_id,
            c.profile_key, c.store_scope_key AS scope_key, c.status, c.revision,
            c.required_scopes, c.secret_ref AS connection_reference,
            (SELECT count(*) FROM documents d WHERE d.entity_id=e.id) AS documents,
            (SELECT coalesce(sum(byte_size),0)::bigint FROM documents d WHERE d.entity_id=e.id) AS receipt_bytes
            FROM business_entities e
            LEFT JOIN organization_entities o ON o.entity_id=e.id
            LEFT JOIN external_connections c ON c.entity_id=e.id AND c.provider='google_drive'
            ORDER BY e.id''')).mappings().all()
    return {'database_sha256': expected_database, 'checked_at': nas.stamp(nas.instant()),
            'businesses': [dict(row) for row in rows],
            'limits': 'SQL metadata only; verify actual grants, Drive inventory, NAS jobs/capacity and decrypt custody separately.'}


def capture(config, partial):
    """Bounded child process; reuse the canonical plan/capture/offline verifier."""
    from scripts.manage_document_storage import execute
    from tabula_rasa.infrastructure.connection_secrets import ConnectionSecrets
    from tabula_rasa.infrastructure.document_archive import DocumentArchive
    from tabula_rasa.infrastructure.postgres.documents import PostgresReceiptRepository
    from tabula_rasa.infrastructure.postgres.identity import database_identity

    work = memory_workspace(config, partial, create=True)
    database = open_database()
    try:
        if database_identity(database) != config['database_sha256']:
            raise ValueError('Production database binding differs.')
        secrets = ConnectionSecrets.from_environment(dict(os.environ))
        plain = work / 'plain'
        plain.mkdir(mode=0o700)
        total_bytes = total_documents = 0
        for index, row in enumerate(config['entities']):
            capacity(config)
            payload = selector(row)
            archive = plain / str(index)
            # Bound the supported inventory before downloading source bytes.
            repository = PostgresReceiptRepository(database, row['entity_id'], store_kind='google_drive',
                                                   store_scope_key=row['scope_key'])
            documents = repository.retained_documents()
            total_documents += len(documents)
            total_bytes += sum(doc.metadata.byte_size for doc in documents)
            if total_documents > config['max_documents'] or total_bytes > config['max_capture_bytes']:
                raise ValueError('Approved capture budget exceeded.')
            options = dict(operation='backup', environment='production', archive_path=archive,
                           progress=lambda _: time.sleep(config['delay_seconds']))
            plan = execute(database, secrets, payload, **options)
            # The dry-run plan includes the whole canonical point. Do not let
            # a changed point bypass the earlier workload budget.
            if repository.retained_documents() != documents:
                raise ValueError('Receipt inventory changed.')
            execute(database, secrets, payload, **options, apply=True,
                    expected_plan=plan['plan_sha256'], approval_sha256=config['approval_sha256'])
            execute(database, secrets, payload, operation='verify-archive', environment='production', archive_path=archive)
            retained = DocumentArchive(archive, row['entity_id'])
            if retained.scope_key != row['scope_key']:
                raise ValueError('Archive folder differs.')
        nas.write_json(partial / 'inventory.json', {'documents': total_documents, 'receipt_bytes': total_bytes})
    finally:
        database.dispose()


def encrypt(config, partial, started_at):
    """Encrypt locally after the bounded network child has exited."""
    work = memory_workspace(config, partial)
    plain = work / 'plain'
    tar_path = work / 'receipts.tar'
    with tarfile.open(tar_path, 'w') as bundle:
        bundle.add(plain, arcname='receipts')
    recipients = config['recipients_file'].read_bytes()
    recipient_copy = work / 'recipients.txt'
    recipient_copy.write_bytes(recipients)
    if hashlib.sha256(recipients).hexdigest() != config['recipients_sha256']:
        raise ValueError('Recipients changed during capture.')
    cipher = partial / 'receipts.tar.age'
    nas.command(['age', '--recipients-file', str(recipient_copy), '--output', str(cipher), str(tar_path)],
                dict(os.environ), 'encryption', timeout=config['timeout_seconds'])
    if not cipher.stat().st_size:
        raise nas.BackupError('encryption')
    with cipher.open('r+b') as stream:
        os.fsync(stream.fileno())
    cipher_hash = nas.digest(cipher)
    # Readback is local ciphertext integrity; decryptability remains a separate drill.
    if nas.digest(cipher) != cipher_hash:
        raise nas.BackupError('integrity')
    inventory = json.loads((partial / 'inventory.json').read_text(encoding='utf-8'))
    nas.write_json(partial / 'manifest.json', {
        'format': 1, 'status': 'complete', 'binding_sha256': config['binding_sha256'],
        'capture_started_at': started_at, 'completed_at': nas.stamp(nas.instant()),
        'archive_sha256': cipher_hash, 'archive_bytes': cipher.stat().st_size,
        **inventory, 'recipients_sha256': config['recipients_sha256']})
    # Remove only this invocation's temporary plaintext after encryption/readback.
    if work.resolve().parent != config['temporary_directory'].resolve() or work.is_symlink():
        raise ValueError('Unsafe transient directory.')
    shutil.rmtree(work)

def backup(config, config_path, *, scheduled=False):
    root = config['root']
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with nas.guard(root):
        now = nas.instant()
        state = nas.read_state(root)
        current_slot = nas.stamp(slot(config, now))
        if scheduled and (state.get('attempted_slot') == current_slot or
                state.get('last_success') and nas.parse_time(verify_generation(config, state['last_success'])['capture_started_at']) >= slot(config, now)):
            return {'status': 'no_attempt', 'health': health(config, now, lock_held=True)['status']}
        state.update(result='running', attempted_slot=current_slot, started_at=nas.stamp(now))
        nas.write_json(root / 'status.json', state)
        for parent in (root, *root.parents):
            nas.sync_directory(parent)
            if parent == config['storage']:
                break
        generation = now.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex
        partial = root / 'incomplete' / generation
        try:
            capacity(config)
            partial.mkdir(parents=True, mode=0o700)
            nas.write_json(partial / 'attempt.json', {'binding_sha256': config['binding_sha256'],
                'capture_started_at': state['started_at']})
            nas.command([sys.executable, '-m', 'scripts.nas_receipt_backup', '_capture',
                         '--config', str(config_path), '--generation', generation],
                        dict(os.environ), 'source', timeout=config['timeout_seconds'])
            encrypt(config, partial, state['started_at'])
            complete = root / 'complete'
            complete.mkdir(exist_ok=True, mode=0o700)
            partial.rename(complete / generation)
            manifest = verify_generation(config, generation)
            for directory in (complete / generation, complete, partial.parent, root):
                nas.sync_directory(directory)
            state.update(result='complete', last_success=generation, completed_at=manifest['completed_at'])
            nas.write_json(root / 'status.json', state)
            return {'status': 'complete', 'generation': generation}
        except BaseException:
            state.update(result='failed')
            nas.write_json(root / 'status.json', state)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inventory', 'run', 'scheduled', 'status', 'verify', '_capture'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--database-sha256')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--generation')
    parser.add_argument('--notify', action='store_true')
    args = parser.parse_args(argv)
    with private_files():
        try:
            if args.notify and args.action == 'verify':
                raise ValueError('Only current operating health may signal backup freshness.')
            if args.action == 'inventory':
                if args.notify or args.output is None or not args.output.is_absolute():
                    raise ValueError('Private inventory output required.')
                private_directory(args.output.parent)
                checksum(args.database_sha256)
                database = open_database()
                try:
                    report = inventory(database, args.database_sha256)
                    content = json.dumps(report, sort_keys=True, indent=2)
                    # Never overwrite a retained private inventory.
                    with args.output.open('x', encoding='utf-8') as stream:
                        stream.write(content)
                finally:
                    database.dispose()
                print(json.dumps({'status': 'inventoried', 'scope': 'sql_metadata_only'}))
                return 0
            config = load_config(args.config)
            if args.action == '_capture':
                if args.notify or not re.fullmatch(r'\d{8}T\d{12}Z-[a-f0-9]{32}', args.generation or ''):
                    raise ValueError('Invalid child invocation.')
                partial = config['root'] / 'incomplete' / args.generation
                attempt = json.loads(nas.protected(partial / 'attempt.json').read_text(encoding='utf-8'))
                if attempt['binding_sha256'] != config['binding_sha256']:
                    raise ValueError('Configuration changed.')
                capture(config, partial)
                return 0
            if args.action == 'status':
                result = health(config, nas.instant())
            elif args.action == 'verify':
                verify_generation(config, args.generation or '')
                result = {'status': 'verified', 'scope': 'ciphertext_checksum'}
            else:
                result = backup(config, args.config, scheduled=args.action == 'scheduled')
        except (Exception, KeyboardInterrupt) as error:
            code = str(error) if isinstance(error, nas.BackupError) and str(error) in nas.GUIDANCE else 'unavailable'
            result = {'status': 'failed', 'code': code,
                      'action': 'Inspect protected receipt configuration, task, grants, capacity and incomplete generations; never delete source receipts.'}
        healthy = result['status'] in {'complete', 'healthy', 'verified'} or (
            result['status'] == 'no_attempt' and result['health'] == 'healthy')
        if args.notify:
            try:
                signal(os.environ.get('TABULA_RASA_RECEIPT_BACKUP_HEARTBEAT_URL', ''), healthy)
            except Exception:
                result['signal'] = 'unconfirmed'
                healthy = False
        print(json.dumps(result, sort_keys=True))
        return 0 if healthy else 1


if __name__ == '__main__':
    raise SystemExit(main())
