"""Synthetic handoff packages reuse canonical validation and never deliver partial files."""

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from tabula_rasa.application.customer_export import CustomerExportService
from tabula_rasa.application.entity_export import ExportError
from tabula_rasa.bookkeeping_export.format import build_artifact, validate_artifact, content_digest
from tests.test_bookkeeping_export import empty_snapshot


def package_service(content=b'synthetic receipt', *, max_bytes=200 * 1024 * 1024):
    artifact = build_artifact(empty_snapshot())
    root = artifact['domains']['business_entities'][0]
    artifact['domains']['documents'] = [{
        'entity_id': 'alpha', 'id': 'doc', 'incarnation_id': 'doc', 'version': 1,
        'created_at': root['created_at'], 'updated_at': root['updated_at'],
        'stored_filename': 'receipt.pdf', 'original_filename': '../../receipt.pdf',
        'mime_type': 'application/pdf', 'byte_size': len(content), 'sha256': sha256(content).hexdigest(),
        'store_kind': 'fake', 'store_scope_key': 'alpha-store', 'storage_key': 'opaque', 'open_url': '',
    }]
    artifact['manifest']['counts']['documents'] = 1
    artifact['manifest']['content_sha256'] = content_digest(artifact['domains'])
    receipts = Mock(profile_key='alpha')
    reference = SimpleNamespace(profile_key='alpha', metadata=SimpleNamespace(document_id='doc'))
    receipts.export_inventory.return_value = SimpleNamespace(documents=(reference,), unverified_storage_keys=())
    receipts.download_reference.return_value = reference, content
    exporter = Mock()
    exporter.export.return_value = artifact
    return CustomerExportService(exporter, receipts, 'alpha', max_bytes=max_bytes), artifact


def prepare(service, include=True):
    return service.prepare(include_documents=include, history_boundary={
        'activated_at': '2026-01-01T00:00:00+00:00', 'pre_activation_history': 'unavailable',
        'mutation_history': 'excluded'})


def test_complete_versioned_package_preserves_canonical_and_maps_safe_attachment_paths():
    service, artifact = package_service(b'x' * (20 * 1024 * 1024))
    with prepare(service) as stream, ZipFile(stream) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['format_version'] == 1 and manifest['scope'] == 'documents_inclusive'
        assert manifest['audit_history'] == 'excluded'
        assert manifest['history_boundary']['pre_activation_history'] == 'unavailable'
        assert manifest['attachments'] == [{'document_id': 'doc', 'path': 'documents/000000.pdf',
            'origin': 'canonical',
            'original_filename': '../../receipt.pdf', 'mime_type': 'application/pdf'}]
        for path, expected in manifest['files'].items():
            content = archive.read(path)
            assert len(content) == expected['bytes'] and sha256(content).hexdigest() == expected['sha256']
        canonical = json.loads(archive.read('bookkeeping.json'))
        assert validate_artifact(canonical, entity_id='alpha') == artifact
        assert len(canonical['domains']) == 27
        assert all('..' not in name for name in archive.namelist())


def test_metadata_only_discloses_missing_bytes_without_calling_provider():
    service, _ = package_service()
    with prepare(service, False) as stream, ZipFile(stream) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['scope'] == 'metadata_only' and manifest['attachments'] == []
        assert 'documents/000000.pdf' not in archive.namelist()
    service.receipts.download_reference.assert_not_called()


def test_unlinked_provider_files_are_included_without_rewriting_canonical_json():
    service, artifact = package_service()
    reference = SimpleNamespace(profile_key='alpha', metadata=SimpleNamespace(
        document_id='unlinked', byte_size=3, sha256=sha256(b'new').hexdigest(),
        original_filename='new.pdf', mime_type='application/pdf'))
    canonical_reference, original = service.receipts.download_reference.return_value
    service.receipts.export_inventory.return_value = SimpleNamespace(documents=(canonical_reference, reference), unverified_storage_keys=())
    service.receipts.download_reference.side_effect = lambda document: (reference, b'new') if document.metadata.document_id == 'unlinked' else (canonical_reference, original)
    with prepare(service) as stream, ZipFile(stream) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert [row['origin'] for row in manifest['attachments']] == ['canonical', 'unlinked_inventory']
        assert archive.read('documents/000001.pdf') == b'new'
        assert json.loads(archive.read('bookkeeping.json')) == artifact


def test_disconnected_or_unverified_inventory_fails_inclusive_and_closes_partial_stream(monkeypatch):
    from tabula_rasa.application import customer_export
    service, _ = package_service()
    service.receipts.export_inventory.return_value = SimpleNamespace(documents=(), unverified_storage_keys=('opaque',))
    with pytest.raises(ExportError, match='document_integrity_unavailable'):
        prepare(service)
    service.receipts = None
    with pytest.raises(ExportError, match='documents_unavailable'):
        prepare(service)
    service, _ = package_service()
    temporary = customer_export.TemporaryFile()
    monkeypatch.setattr(customer_export, 'TemporaryFile', lambda **kwargs: temporary)
    service.receipts.download_reference.side_effect = RuntimeError('synthetic read failure')
    with pytest.raises(RuntimeError):
        prepare(service)
    assert temporary.closed


@pytest.mark.parametrize('failure', ['missing', 'changed', 'unverified', 'foreign', 'quota'])
def test_missing_changed_foreign_or_oversized_documents_never_produce_a_complete_package(failure):
    service, artifact = package_service(max_bytes=100 if failure == 'quota' else 200 * 1024 * 1024)
    if failure == 'missing':
        service.receipts.download_reference.side_effect = RuntimeError('private provider text')
    elif failure == 'changed':
        reference, _ = service.receipts.download_reference.return_value
        service.receipts.download_reference.return_value = reference, b'changed'
    elif failure == 'foreign':
        service.receipts.download_reference.return_value[0].profile_key = 'beta'
    elif failure == 'unverified':
        artifact['domains']['documents'][0]['sha256'] = None
        artifact['manifest']['content_sha256'] = content_digest(artifact['domains'])
    with pytest.raises((ExportError, RuntimeError)):
        prepare(service)


def test_deadline_and_forged_canonical_entity_fail_then_read_retry_builds_a_new_package():
    service, artifact = package_service()
    original = deepcopy(artifact)
    artifact['manifest']['entity_id'] = 'beta'
    with pytest.raises(ExportError, match='entity_scope'):
        prepare(service)
    service.exporter.export.return_value = original
    service.clock = Mock(side_effect=[0, 301])
    with pytest.raises(ExportError, match='export_limit'):
        prepare(service)
    service.clock = lambda: 0
    with prepare(service) as stream:
        assert stream.read(2) == b'PK'


@pytest.mark.parametrize('provider', ['drive', 's3'])
def test_real_receipt_service_exports_many_unlinked_files_with_one_inventory(provider):
    from tabula_rasa.application.documents import ReceiptService
    from tests.test_application_documents import FakeReceiptRepository
    if provider == 's3':
        from tests.s3_support import s3_store
        from tests.test_s3_documents import upload
        store = s3_store()
        profile = 'alpha'
        for number in range(100):
            store.upload(profile, upload(identity=f'{number:032x}'))
        store.client.calls.clear()
    else:
        from tabula_rasa.infrastructure.google_drive_documents import GoogleDriveDocumentStore
        from tests.drive_fixtures import receipt_drive_file
        profile = 'north'
        files = [receipt_drive_file(f'receipt{number:08d}', 'receipt.pdf', profile_key=profile,
                 receipt_id=f'{number:032x}', sha256=sha256(b'receipt').hexdigest(), size=7)
                 for number in range(100)]
        client = Mock()
        client.list_profile_receipts.return_value = files
        client.receipt_file.side_effect = lambda **kwargs: next(file for file in files if file['id'] == kwargs['file_id'])
        client.download_file.return_value = b'receipt'
        store = GoogleDriveDocumentStore(profile, 'folder123456', lambda: client)
    exporter = Mock()
    exporter.export.return_value = build_artifact(empty_snapshot())
    service = CustomerExportService(exporter, ReceiptService(profile, FakeReceiptRepository(), store), 'alpha')
    with prepare(service) as stream, ZipFile(stream) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert len(manifest['attachments']) == 100
        assert all(row['path'].endswith('.pdf') for row in manifest['attachments'])
    if provider == 's3':
        assert [method for method, _ in store.client.calls].count('list') == 1
        assert [method for method, _ in store.client.calls].count('head') == 100
        assert [method for method, _ in store.client.calls].count('get') == 100
    else:
        client.list_profile_receipts.assert_called_once()
        assert client.receipt_file.call_count == client.download_file.call_count == 100


@pytest.mark.parametrize('name,suffix', [('receipt.PDF', '.pdf'), ('photo.jpg', '.jpg'),
                                       ('receipt.pdf/../../bad', ''), ('receipt.exe', ''), ('receipt', '')])
def test_attachment_extension_is_limited_to_supported_receipt_types(name, suffix):
    service, artifact = package_service()
    artifact['domains']['documents'][0]['original_filename'] = name
    artifact['manifest']['content_sha256'] = content_digest(artifact['domains'])
    with prepare(service) as stream, ZipFile(stream) as archive:
        assert json.loads(archive.read('manifest.json'))['attachments'][0]['path'] == 'documents/000000' + suffix
