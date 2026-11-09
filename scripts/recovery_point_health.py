"""Operator-attested coordinated backup publication and offline health inspection.

No capture, restore, provider access, scheduler or decryption. Component integrity
is machine checked; quiescence and encrypted-receipt correspondence are attested
by the authorized operator, not inferred from supplied timestamps.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from uuid import UUID, uuid4

from scripts import nas_postgres_backup as nas

UTC = timezone.utc


@contextmanager
def private_files():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def checksum(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('Invalid digest.')
    return value


def private_directory(path):
    root = nas.protected(path, directory=True)
    if any((parent / '.git').exists() for parent in (root.resolve(), *root.resolve().parents)):
        raise ValueError('Recovery state must be outside Git.')
    return root


def load_config(path):
    raw = json.loads(nas.protected(path).read_text(encoding='utf-8'))
    required = {'ledger_config', 'custody_config', 'state_directory', 'entities'}
    if not required <= raw.keys() <= required | {'schedule'}:
        raise ValueError('Invalid coordinated configuration.')
    ledger, custody = (nas.load_config(raw[key]) for key in ('ledger_config', 'custody_config'))
    if (ledger['environment'] != 'uat' or custody['environment'] != 'uat'
            or ledger.get('source_kind') is not None
            or custody.get('source_kind') != 'connection_custody'
            or ledger['source_sha256'] == custody['source_sha256']):
        raise ValueError('Distinct staging ledger and custody sources required.')
    entities = raw['entities']
    if not isinstance(entities, list) or not entities or len(entities) > 20:
        raise ValueError('Explicit enrolled businesses required.')
    for row in entities:
        if set(row) != {'entity_id', 'scope_key'} or not isinstance(row['scope_key'], str) or not row['scope_key']:
            raise ValueError('Invalid enrolled business.')
        if str(UUID(row['entity_id'])) != row['entity_id']:
            raise ValueError('Use canonical business identities.')
    if len({row['entity_id'] for row in entities}) != len(entities):
        raise ValueError('Duplicate business.')
    if len({row['scope_key'] for row in entities}) != len(entities):
        raise ValueError('Distinct receipt folders required.')
    schedule = raw.get('schedule', 'wednesday_sunday')
    if schedule not in {'wednesday_sunday', 'twice_daily'}:
        raise ValueError('Unsupported cadence.')
    if any((item.get('schedule') == 'twice_daily') != (schedule == 'twice_daily') for item in (ledger, custody)):
        raise ValueError('Component and coordinated cadence differ.')
    binding = {'ledger': ledger['source_sha256'], 'custody': custody['source_sha256'],
               'entities': sorted(entities, key=lambda row: row['entity_id']), 'schedule': schedule}
    return {'ledger': ledger, 'custody': custody, 'entities': binding['entities'],
            'root': private_directory(raw['state_directory']), 'schedule': schedule,
            'binding_sha256': sha(binding)}


def component(config, generation):
    manifest = nas.verify_generation(config, generation)
    folder = nas.namespace(config) / 'complete' / generation
    return {'generation': generation, 'manifest_sha256': nas.digest(folder / 'manifest.json'),
            'archive_sha256': manifest['archive_sha256'],
            'capture_started_at': manifest['capture_started_at'], 'completed_at': manifest['completed_at']}


def receipt(row, selected, *, start, end):
    # Only publication needs the application's canonical receipt verifier.
    from tabula_rasa.infrastructure.document_archive import DocumentArchive

    if set(row) != {'entity_id', 'archive', 'ciphertext'} or row['entity_id'] != selected['entity_id']:
        raise ValueError('Receipt enrollment differs.')
    archive = DocumentArchive(row['archive'], selected['entity_id'])
    if archive.store_kind != 'google_drive' or archive.scope_key != selected['scope_key']:
        raise ValueError('Receipt source differs.')
    snapshot_at = archive.artifact['manifest']['snapshot_at']
    if not start <= nas.parse_time(snapshot_at) <= end:
        raise ValueError('Receipt snapshot outside attested window.')
    for doc in archive.documents:
        archive.fetch(selected['entity_id'], doc.storage_key)  # Existing exact size/hash verifier.
    cipher = nas.protected(row['ciphertext'])
    with cipher.open('rb') as stream:
        header = b'age-encryption.org/v1\n'
        if stream.read(len(header)) != header:
            raise ValueError('Expected age ciphertext.')
    return {'entity_id': selected['entity_id'], 'scope_key': selected['scope_key'],
            'content_sha256': archive.artifact['manifest']['content_sha256'],
            'snapshot_at': snapshot_at,
            'documents': len(archive.documents), 'ciphertext': str(cipher),
            'ciphertext_sha256': nas.digest(cipher), 'ciphertext_bytes': cipher.stat().st_size}


def publish(config, spec, *, attested=False, now=None):
    """Publish only machine-checked components in an explicitly attested window."""
    if not attested:
        raise ValueError('Explicit operator attestation required.')
    now = now or datetime.now(UTC)
    root = config['root']
    with private_files(), nas.guard(root):
        state = nas.read_state(root)
        state.update(result='running', started_at=nas.stamp(now))
        nas.write_json(root / 'status.json', state)
        try:
            if set(spec) != {'quiesced_from', 'quiesced_until', 'approval_sha256',
                             'references_sha256', 'verified_references', 'ledger_generation',
                             'custody_generation', 'receipts'}:
                raise ValueError('Invalid point specification.')
            start, end = (nas.parse_time(spec[key]) for key in ('quiesced_from', 'quiesced_until'))
            if not start < end <= now:
                raise ValueError('Invalid quiesced window.')
            checksum(spec['approval_sha256'])
            checksum(spec['references_sha256'])
            if type(spec['verified_references']) is not int or spec['verified_references'] < 0:
                raise ValueError('Invalid reference attestation.')
            components = {}
            for key in ('ledger', 'custody'):
                item = component(config[key], spec[key + '_generation'])
                if not start <= nas.parse_time(item['capture_started_at']) <= nas.parse_time(item['completed_at']) <= end:
                    raise ValueError('SQL generation outside attested window.')
                components[key] = item
            receipts = spec['receipts']
            if (not isinstance(receipts, list) or len(receipts) != len(config['entities'])
                    or len({row['entity_id'] for row in receipts}) != len(receipts)):
                raise ValueError('Receipt coverage differs.')
            by_entity = {row['entity_id']: row for row in receipts}
            verified = [receipt(by_entity[row['entity_id']], row, start=start, end=end)
                        for row in config['entities']]
            point = {'format': 1, 'consistency': 'operator_attested_quiescence',
                     'binding_sha256': config['binding_sha256'], 'captured_at': nas.stamp(start),
                     'quiesced_until': nas.stamp(end), 'published_at': nas.stamp(now),
                     'approval_sha256': spec['approval_sha256'],
                     'references_sha256': spec['references_sha256'],
                     'verified_references': spec['verified_references'],
                     **components, 'receipts': verified}
            points = root / 'points'
            points.mkdir(mode=0o700, exist_ok=True)
            name = uuid4().hex
            nas.write_json(points / (name + '.json'), point)
            state.update(result='complete', last_success=name, completed_at=nas.stamp(now))
            nas.write_json(root / 'status.json', state)
            return {'status': 'complete', 'consistency': point['consistency'], 'point': name}
        except Exception:
            state.update(result='failed', code='point_unconfirmed')
            nas.write_json(root / 'status.json', state)
            raise


def inspect(config, *, now=None):
    now = now or datetime.now(UTC)
    root = config['root']
    state = nas.read_state(root)
    locked = (root / '.lock').exists()
    if locked != (state.get('result') == 'running'):
        # Publication may have crossed its atomic state update during this poll.
        # One readback avoids treating an old marker as a new failure.
        state = nas.read_state(root)
        locked = (root / '.lock').exists()
    if not state.get('last_success'):
        return {'status': 'missing', 'codes': ['complete_point_missing']}
    name = state['last_success']
    if not isinstance(name, str) or not re.fullmatch('[a-f0-9]{32}', name):
        raise ValueError('Invalid point identity.')
    point = json.loads(nas.protected(root / 'points' / (name + '.json')).read_text('utf-8'))
    if (point['format'] != 1 or point['binding_sha256'] != config['binding_sha256']
            or point['consistency'] != 'operator_attested_quiescence'):
        raise ValueError('Point binding differs.')
    captured = nas.parse_time(point['captured_at'])
    if not captured < nas.parse_time(point['quiesced_until']) <= nas.parse_time(point['published_at']) <= now:
        raise ValueError('Invalid point chronology.')
    codes = []
    if state.get('result') == 'failed':
        codes.append('point_attempt_failed')
    elif state.get('result') == 'running':
        started = nas.parse_time(state['started_at'])
        if not locked or started > now:
            codes.append('point_attempt_unconfirmed')
        elif now - started > nas.GRACE:
            codes.append('point_attempt_overdue')
        # During grace, only the still-valid prior completed point can pass.
    elif locked:
        codes.append('point_overlap')
    for key in ('ledger', 'custody'):
        source = config[key]
        result = nas.health(source, now)
        if result['status'] not in {'healthy', 'running'}:
            codes.append(key + '_' + result['status'])
        retained = point[key]
        # health already verified the current pointer; avoid hashing the same SQL archive twice.
        current = nas.read_state(nas.namespace(source))
        if current.get('last_success') != retained['generation']:
            nas.verify_generation(source, retained['generation'])
        folder = nas.namespace(source) / 'complete' / retained['generation']
        if nas.digest(folder / 'manifest.json') != retained['manifest_sha256']:
            raise ValueError('Retained SQL manifest changed.')
        manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
        if (manifest['capture_started_at'] != retained['capture_started_at']
                or manifest['completed_at'] != retained['completed_at']
                or not captured <= nas.parse_time(manifest['capture_started_at'])
                <= nas.parse_time(manifest['completed_at']) <= nas.parse_time(point['quiesced_until'])):
            raise ValueError('Retained SQL generation differs from the common window.')
    receipts = point['receipts']
    if ([{'entity_id': row['entity_id'], 'scope_key': row['scope_key']} for row in receipts]
            != config['entities']):
        raise ValueError('Point enrollment differs.')
    verified_ciphers = set()
    for row in receipts:
        if not captured <= nas.parse_time(row['snapshot_at']) <= nas.parse_time(point['quiesced_until']):
            raise ValueError('Receipt snapshot differs from common window.')
        cipher = nas.protected(row['ciphertext'])
        identity = (cipher.resolve(), row['ciphertext_bytes'], row['ciphertext_sha256'])
        if identity in verified_ciphers:
            continue  # One encrypted bundle may contain several enrolled archives.
        if cipher.stat().st_size != row['ciphertext_bytes'] or nas.digest(cipher) != row['ciphertext_sha256']:
            raise ValueError('Receipt ciphertext changed.')
        verified_ciphers.add(identity)
    twice_daily = config['schedule'] == 'twice_daily'
    slot = nas.latest_slot(now, twice_daily=twice_daily)
    if (captured < slot and now - slot > nas.GRACE
            or twice_daily and now - captured >= timedelta(hours=20)):
        codes.append('complete_point_stale')
    return {'status': 'unhealthy' if codes else 'healthy', 'codes': codes,
            'captured_at': point['captured_at'], 'consistency': point['consistency']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('publish', 'status'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--point', help='Protected private component and operator attestation JSON.')
    parser.add_argument('--attest-quiesced-point', action='store_true',
                        help='Attest writer freeze, verified reference completeness and ciphertext/archive correspondence.')
    parser.add_argument('--notify', action='store_true', help='Explicitly approved health signal only.')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.action == 'publish':
            if args.notify:
                raise ValueError('Publish does not send notifications.')
            spec = json.loads(nas.protected(args.point).read_text('utf-8'))
            result = publish(config, spec, attested=args.attest_quiesced_point)
        else:
            if args.point or args.attest_quiesced_point:
                raise ValueError('Status does not publish.')
            result = inspect(config)
    except Exception:
        result = {'status': 'unconfirmed', 'codes': ['recovery_point_unconfirmed']}
    if args.notify and args.action == 'status':
        from scripts.operational_heartbeat import signal
        try:
            signal(os.environ.get('TABULA_RASA_BACKUP_HEARTBEAT_URL', ''), result['status'] == 'healthy')
            result['signal'] = 'confirmed'
        except Exception:
            result['signal'] = 'unconfirmed'
    print(json.dumps(result))
    return 0 if result['status'] in {'healthy', 'complete'} and result.get('signal') != 'unconfirmed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
