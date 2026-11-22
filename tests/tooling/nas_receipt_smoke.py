"""Synthetic tmpfs/encryption probe for the application-capable NAS image.

Run explicitly in a disposable container with /work mounted as private tmpfs.
No database, Google grant, network or persistent output is needed.
"""
import json
from pathlib import Path
import subprocess
import tarfile
from uuid import uuid4

from scripts import nas_receipt_backup as runner
from scripts.manage_document_storage import execute  # noqa: F401 -- Verify packaged capture imports.


def main():
    with runner.private_files():
        base = Path('/work')
        storage, temporary = base / 'archive', base / 'scratch'
        storage.mkdir(mode=0o700)
        temporary.mkdir(mode=0o700)
        (storage / '.receipt-backup-marker').write_text('b'*32)
        key = base / 'synthetic-identity'
        subprocess.run(['age-keygen', '-o', str(key)], capture_output=True, check=True)
        recipients = base / 'recipients'
        recipients.write_bytes(subprocess.run(['age-keygen', '-y', str(key)], capture_output=True, check=True).stdout)
        raw = dict(environment='production', database_sha256='a'*64, storage=str(storage),
            storage_marker='b'*32, recipients_file=str(recipients), temporary_directory=str(temporary),
            approval_sha256='c'*64, entities=[dict(organization_id=str(uuid4()), entity_id='synthetic',
            profile_key='synthetic', scope_key='synthetic-folder')], schedule='wednesday_sunday',
            minimum_free_bytes=1, max_capture_bytes=1024**2, max_documents=10,
            timeout_seconds=60, delay_seconds=1)
        config_path = base / 'config.json'
        config_path.write_text(json.dumps(raw))
        config = runner.load_config(config_path)
        generation = '20261117T120000000000Z-' + uuid4().hex
        partial = config['root'] / 'incomplete' / generation
        partial.mkdir(parents=True, mode=0o700)
        work = runner.memory_workspace(config, partial, create=True)
        (work / 'plain').mkdir(mode=0o700)
        (work / 'plain' / 'synthetic.bin').write_bytes(b'exact-synthetic-bytes')
        runner.nas.write_json(partial / 'inventory.json', {'documents': 1, 'receipt_bytes': 21})
        runner.encrypt(config, partial, runner.nas.stamp(runner.nas.instant()))
        assert not work.exists()
        assert not (partial / 'plain').exists() and not (partial / 'receipts.tar').exists()
        recovered = base / 'recovered.tar'
        subprocess.run(['age', '-d', '-i', str(key), '-o', str(recovered), str(partial / 'receipts.tar.age')],
                       capture_output=True, check=True)
        with tarfile.open(recovered) as bundle:
            assert bundle.extractfile('receipts/synthetic.bin').read() == b'exact-synthetic-bytes'
        complete = config['root'] / 'complete'
        complete.mkdir(mode=0o700)
        partial.rename(complete / generation)
        assert runner.verify_generation(config, generation)['documents'] == 1
        assert runner.main(['status', '--config', str(config_path)]) == 1  # Missing operating success pointer.
        rejected = False
        try:
            runner.memory_workspace({**config, 'temporary_directory': Path('/app')}, partial, create=True)
        except runner.nas.BackupError:
            rejected = True
        assert rejected  # Disk-backed scratch must fail before any source access.
        print(json.dumps({'status': 'passed', 'scope': 'synthetic_tmpfs_age_roundtrip'}))


if __name__ == '__main__':
    main()
