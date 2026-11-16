"""Bounded accountant handoff around the canonical bookkeeping exporter."""

from hashlib import sha256
from tempfile import TemporaryFile
from time import monotonic
from zipfile import ZipFile, ZIP_STORED

from .entity_export import ExportError
from .documents import RECEIPT_UPLOAD_TYPES
from tabula_rasa.bookkeeping_export.format import canonical_json, validate_artifact

MAX_EXPORT_BYTES = 200 * 1024 * 1024
MAX_EXPORT_RECORDS = 50_000
MAX_EXPORT_SECONDS = 300

INSTRUCTIONS = """Tabula Rasa accountant handoff
bookkeeping.json is the complete versioned canonical financial archive.
manifest.json lists scope, snapshot time, attachment paths, sizes and SHA-256 checksums.
Checksums detect corruption; they do not establish authorship. Retain this archive privately.
Documents-inclusive exports contain snapshot documents and verified unlinked inventory files.
Metadata-only exports contain no receipt bytes; provider URLs are not a portable attachment.
Audit events, identity, credentials, jobs and operational policies are excluded.
Pre-audit records do not have a complete mutation history. This is not a database backup
or an automatic restore format. Use the existing CSV downloads for readable ledger tables.
"""


class CustomerExportService:
    def __init__(self, exporter, receipts, entity_id, *, max_bytes=MAX_EXPORT_BYTES,
                 clock=monotonic):
        self.exporter, self.receipts, self.entity_id = exporter, receipts, entity_id
        self.max_bytes, self.clock = max_bytes, clock

    def prepare(self, *, include_documents, history_boundary):
        """Return a complete anonymous private stream, or close it before failing.

        No export is retained for later download. Provider retries remain owned by
        DocumentStore. A failed read may safely be retried as a new snapshot.
        """
        deadline = self.clock() + MAX_EXPORT_SECONDS
        artifact = self.exporter.export()
        validate_artifact(artifact, entity_id=self.entity_id)
        if sum(artifact['manifest']['counts'].values()) > MAX_EXPORT_RECORDS:
            raise ExportError('export_limit')
        documents = list(artifact['domains']['documents'])
        canonical_ids = {row['id'] for row in documents}
        references = {}
        if include_documents and self.receipts is None:
            raise ExportError('documents_unavailable')
        if include_documents:
            # Uploads survive failed links in the scoped provider inventory and
            # may not yet have a canonical SQL row. Preserve those recovery files.
            inventory = self.receipts.export_inventory()
            if inventory.unverified_storage_keys:
                raise ExportError('document_integrity_unavailable')
            seen = set(canonical_ids)
            for reference in inventory.documents:
                metadata = reference.metadata
                if reference.profile_key != self.receipts.profile_key:
                    raise ExportError('entity_scope')
                references[metadata.document_id] = reference
                if metadata.document_id not in seen:
                    seen.add(metadata.document_id)
                    documents.append({'id': metadata.document_id, 'byte_size': metadata.byte_size,
                        'sha256': metadata.sha256, 'original_filename': metadata.original_filename,
                        'mime_type': metadata.mime_type})
            if len(documents) > MAX_EXPORT_RECORDS:
                raise ExportError('export_limit')
        if include_documents and any(row['byte_size'] is None or not row['sha256'] for row in documents):
            raise ExportError('document_integrity_unavailable')
        if include_documents and sum(row['byte_size'] for row in documents) > self.max_bytes:
            raise ExportError('export_limit')
        stream = TemporaryFile(mode='w+b')
        total = 0
        checksums = {}

        def add(archive, path, content):
            nonlocal total
            total += len(content)
            if total > self.max_bytes or self.clock() >= deadline:
                raise ExportError('export_limit')
            archive.writestr(path, content)
            checksums[path] = {'bytes': len(content), 'sha256': sha256(content).hexdigest()}

        try:
            attachments = []
            with ZipFile(stream, 'w', compression=ZIP_STORED) as archive:
                add(archive, 'bookkeeping.json', canonical_json(artifact).encode('utf-8'))
                currency = artifact['domains']['business_entities'][0]['currency_code']
                add(archive, 'README.txt', (INSTRUCTIONS + f'Book currency: {currency}. All monetary amounts use this fixed unit; no FX is performed.\n').encode('utf-8'))
                if include_documents:
                    for index, row in enumerate(documents):
                        if self.clock() >= deadline:
                            raise ExportError('export_limit')
                        if row['id'] not in references:
                            raise ExportError('documents_unavailable')
                        reference, content = self.receipts.download_reference(references[row['id']])
                        if (reference.metadata.document_id != row['id']
                                or reference.profile_key != self.receipts.profile_key
                                or len(content) != row['byte_size']
                                or sha256(content).hexdigest() != row['sha256']):
                            raise ExportError('document_integrity')
                        # Never use user/provider names as ZIP paths.
                        extension = row['original_filename'].rsplit('.', 1)[-1].lower()
                        suffix = '.' + extension if extension in RECEIPT_UPLOAD_TYPES else ''
                        path = f'documents/{index:06d}{suffix}'
                        add(archive, path, content)
                        attachments.append({'document_id': row['id'], 'path': path,
                                            'origin': 'canonical' if row['id'] in canonical_ids else 'unlinked_inventory',
                                            'original_filename': row['original_filename'],
                                            'mime_type': row['mime_type']})
                manifest = {
                    'format': 'tabula-rasa-customer-handoff', 'format_version': 1,
                    'entity_id': self.entity_id, 'snapshot_at': artifact['manifest']['snapshot_at'],
                    'canonical_format_version': artifact['manifest']['format_version'],
                    'scope': 'documents_inclusive' if include_documents else 'metadata_only',
                    'status': artifact['manifest']['status'], 'audit_history': 'excluded',
                    'history_boundary': history_boundary, 'files': dict(checksums),
                    'attachments': attachments,
                }
                add(archive, 'manifest.json', canonical_json(manifest).encode('utf-8'))
            stream.seek(0)
            return stream
        except BaseException:
            stream.close()
            raise
