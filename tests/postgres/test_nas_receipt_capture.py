"""NAS capture reuses real two-business canonical exports and the Drive adapter."""

from scripts.nas_receipt_backup import capture
from tabula_rasa.infrastructure.document_archive import DocumentArchive
from tests.postgres.test_drive_document_recovery import fixture


def test_canonical_capture_keeps_two_businesses_and_receipt_bytes_separate(database, tmp_path, monkeypatch):
    (payloads, secrets), _ = fixture(database, tmp_path, monkeypatch)
    results = []
    for entity in ('alpha', 'beta'):
        selector = {**payloads[entity]['source'], 'google_drive_oauth': {}, 'google_drive': {}}
        destination = tmp_path / ('capture-' + entity)
        result = capture(database, secrets, selector, destination, 'a' * 64)
        assert result['documents'] == 2
        archive = DocumentArchive(destination, entity)
        assert {archive.fetch(entity, document.storage_key) for document in archive.documents} == {
            entity.encode() + bytes([1]), entity.encode() + bytes([2])}
        results.append(result)
    assert results[0]['scope_key'] != results[1]['scope_key']
