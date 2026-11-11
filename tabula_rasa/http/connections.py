"""Authorized provider composition; existing Drive adapters own byte behavior."""
from tabula_rasa.application.documents import DocumentError
from tabula_rasa.application.identity import IdentityError
from tabula_rasa.infrastructure.postgres.connections import PostgresConnections


class TenantConnections:
    def __init__(self, database, environment, secrets, *, store_factory=None):
        self.repository = PostgresConnections(database)
        self.environment, self.secrets = environment, secrets
        self.store_factory = store_factory or provider_store
        self.oauth = None

    def _store(self, row, *, validate=False):
        if self.secrets is None:
            raise IdentityError('unavailable')
        payload = self.secrets.get(row['secret_ref'], environment=self.environment,
            organization_id=row['organization_id'], entity_id=row['entity_id'], provider=row['provider'])
        if (payload.get('profile_key') != row['profile_key']
                or payload.get('scope_key') != row['store_scope_key']):
            raise IdentityError('unavailable')
        try:
            store = self.store_factory(row, payload, validate=validate)
        except Exception:
            pass
        else:
            if store.scope_key == row['store_scope_key']:
                return store
        raise IdentityError('unavailable')

    def document_store(self, context):
        row = self.repository.document_grant(context)
        return None if row is None else AuthorizedDocumentStore(self, context, row['store_scope_key'], row['provider'])

    def validate_migrated_binding(self, **binding):
        """Check the exact startup binding without provider I/O."""
        row = self.repository.validate_migrated_binding(**binding)
        # Enrolled Drive bindings keep bookkeeping available during a grant outage.
        # Each document operation still requires fresh authorization and readable custody.
        if row['provider'] != 'google_drive' or not row['drive_recovery_binding']:
            self._store(row)

    def change(self, context, session, *, revision, connect):
        return self.repository.change(context, authenticated_at=session.authenticated_at,
            expected_revision=revision, connect=connect, validate=lambda row: self._store(row, validate=True))


def provider_store(row, payload, *, validate=False):
    if row['provider'] == 'google_drive':
        return google_store(row, payload, validate=validate)
    if row['provider'] != 's3':
        raise IdentityError('unavailable')
    from tabula_rasa.infrastructure.s3_documents import create_s3_document_store
    if (payload.get('provider') != 's3' or payload.get('entity_id') != row['entity_id']
            or payload.get('environment') not in {'staging', 'production'}
            or not payload.get('s3', {}).get('namespace', '').startswith(payload['environment'] + '/')):
        raise IdentityError('unavailable')
    store = create_s3_document_store(row['entity_id'], payload['s3'])
    if store.scope_key != row['store_scope_key']:
        raise IdentityError('unavailable')
    if validate:
        store.validate()
    return store


def google_store(row, payload, *, validate=False):
    from tabula_rasa.google_drive import DRIVE_SCOPE, GoogleDriveClient, load_oauth_config, validate_drive_profile_settings
    from tabula_rasa.infrastructure.drive_composition import create_drive_document_store
    from tabula_rasa.infrastructure.entity_documents import EntityDocumentStore

    config = load_oauth_config(payload)
    grant = validate_drive_profile_settings(payload.get('google_drive'))
    if (grant['scopes'] != row['required_scopes'] or grant['scopes'] != [DRIVE_SCOPE]
            or grant.get('receipt_folder', {}).get('id') != row['store_scope_key']):
        raise IdentityError('unavailable')
    # Connection activation validates managed folder/profile ownership freshly.
    # Ordinary document calls retain the existing adapter's scoped validation.
    if validate:
        GoogleDriveClient(config, grant).validate_storage_configuration(row['profile_key'])
    store = create_drive_document_store(row['profile_key'], {'google_drive': grant}, payload)
    return EntityDocumentStore(row['entity_id'], row['profile_key'], store)


class AuthorizedDocumentStore:
    """Resolve fresh connection/membership before each byte operation, no grant cache."""
    def __init__(self, manager, context, scope_key, store_kind='google_drive'):
        self.manager, self.context, self.scope_key = manager, context, scope_key
        self.store_kind = store_kind

    def _call(self, method, entity_id, *args, **kwargs):
        from tabula_rasa.google_drive.errors import GoogleDriveAuthorizationError
        if entity_id != self.context.entity_id:
            raise IdentityError('forbidden')
        row = self.manager.repository.grant(self.context, provider=self.store_kind,
            action='write' if method in {'upload', 'delete'} else 'read')
        if row is None or row['store_scope_key'] != self.scope_key:
            raise IdentityError('unavailable')
        reconnect = False
        try:
            return getattr(self.manager._store(row), method)(entity_id, *args, **kwargs)
        except Exception as error:
            # Existing Drive facade wraps authorization failures in DocumentError.
            reconnect = isinstance(error, GoogleDriveAuthorizationError) or isinstance(error.__cause__, GoogleDriveAuthorizationError)
        if reconnect and self.store_kind == 'google_drive':
            self.manager.repository.require_reconnect(row)
        raise DocumentError('Document storage is unavailable.')

    def upload(self, entity_id, upload):
        return self._call('upload', entity_id, upload)

    def inventory(self, entity_id, *, fresh=False):
        return self._call('inventory', entity_id, fresh=fresh)

    def metadata(self, entity_id, storage_key):
        return self._call('metadata', entity_id, storage_key)

    def fetch(self, entity_id, storage_key):
        return self._call('fetch', entity_id, storage_key)

    def delete(self, entity_id, storage_key):
        return self._call('delete', entity_id, storage_key)
