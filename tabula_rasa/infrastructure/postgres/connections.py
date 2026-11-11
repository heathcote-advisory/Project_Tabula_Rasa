"""Fresh tenant/capability checks precede provider secret selection."""
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from tabula_rasa.application.authorization import authorize_action
from tabula_rasa.application.connections import ConnectionStatus
from tabula_rasa.application.identity import IdentityError, capabilities
from .control_plane import ENTITY_ACCESS_SQL, identity_operation


def authorized_entity(connection, context, action):
    row = connection.execute(text(ENTITY_ACCESS_SQL + '''
        AND e.entity_id=:entity AND e.organization_id=:org'''),
        {'user': context.actor_id, 'entity': context.entity_id, 'org': context.organization_id}).mappings().first()
    if row is None or context.trusted_development:
        raise IdentityError('forbidden')
    authorize_action(capabilities(row['role']), action)


def require_recent_authentication(authenticated_at):
    now = datetime.now(timezone.utc)
    if (authenticated_at.tzinfo is None or not now - timedelta(minutes=5) <= authenticated_at <= now + timedelta(seconds=60)):
        raise IdentityError('forbidden')


def lock_connection_entity(connection, entity_id):
    # Serialize connection setup without blocking foreign-key KEY SHARE locks
    # used by bookkeeping writes. Competing setup must not queue behind Google.
    try:
        connection.execute(text('SELECT id FROM business_entities WHERE id=:id FOR NO KEY UPDATE NOWAIT'),
                           {'id': entity_id})
    except SQLAlchemyError as error:
        if getattr(getattr(error, 'orig', None), 'sqlstate', None) != '55P03':
            raise
    else:
        return
    # Only this explicit NOWAIT boundary classifies contention as a stale command.
    # Leave unrelated lock timeouts unchanged and discard raw SQL exception context.
    raise IdentityError('concurrent_write')


class PostgresConnections:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _row(connection, context, *, lock=False, provider='google_drive'):
        return connection.execute(text("""SELECT * FROM external_connections
            WHERE entity_id=:entity AND organization_id=:org AND provider=:provider""" +
            (' FOR UPDATE' if lock else '')),
            {'entity': context.entity_id, 'org': context.organization_id, 'provider': provider}).mappings().first()

    def status(self, context):
        with identity_operation(self.database, read_only=True) as connection:
            authorized_entity(connection, context, 'connections_manage')
            row = self._row(connection, context)
            return ConnectionStatus(status=row['status'], revision=row['revision']) if row else ConnectionStatus()

    @classmethod
    def _document_row(cls, connection, context):
        row = cls._row(connection, context, provider='s3')
        return cls._row(connection, context) if row is None else row

    def validate_migrated_binding(self, *, organization_id, entity_id, profile_key, scope_key):
        """Operator startup check; request access still requires fresh membership."""
        from types import SimpleNamespace
        context = SimpleNamespace(organization_id=organization_id, entity_id=entity_id)
        with identity_operation(self.database, read_only=True) as connection:
            row = self._document_row(connection, context)
            if (row is None
                    or (row['status'] != 'connected' and not (
                        row['provider'] == 'google_drive' and row['drive_recovery_binding']
                        and row['status'] in {'disconnected', 'reconnect_required'}))
                    or row['profile_key'] != profile_key
                    or row['provider'] == 'google_drive' and row['store_scope_key'] != scope_key):
                raise IdentityError('unavailable')
            return dict(row)

    def grant(self, context, *, require_connected=True, action='read', provider='google_drive'):
        with identity_operation(self.database, read_only=True) as connection:
            authorized_entity(connection, context, action)
            row = self._row(connection, context, provider=provider)
            if row is None:
                return None
            if require_connected and row['status'] != 'connected':
                raise IdentityError('unavailable')
            return dict(row)

    def document_grant(self, context):
        """Explicit managed binding wins; disconnected managed storage never falls back."""
        with identity_operation(self.database, read_only=True) as connection:
            authorized_entity(connection, context, 'read')
            row = self._document_row(connection, context)
            return dict(row) if row else None

    def change(self, context, *, authenticated_at, expected_revision, connect, validate):
        require_recent_authentication(authenticated_at)
        with identity_operation(self.database) as connection:
            authorized_entity(connection, context, 'connections_manage')
            lock_connection_entity(connection, context.entity_id)
            row = self._row(connection, context, lock=True)
            if row is None:
                raise IdentityError('unavailable')
            if row['revision'] != expected_revision:
                raise IdentityError('concurrent_write')
            # Validate exact secret/folders only after fresh membership and baseline.
            if connect:
                validate(dict(row))
            status = 'connected' if connect else 'disconnected'
            connection.execute(text("""UPDATE external_connections SET status=:status,revision=revision+1,
                updated_at=CURRENT_TIMESTAMP WHERE entity_id=:entity AND provider='google_drive'"""),
                {'status': status, 'entity': context.entity_id})
            return ConnectionStatus(status=status, revision=row['revision'] + 1)

    def require_reconnect(self, row):
        with identity_operation(self.database) as connection:
            connection.execute(text("""UPDATE external_connections SET status='reconnect_required',revision=revision+1,
                updated_at=CURRENT_TIMESTAMP WHERE entity_id=:entity AND organization_id=:org
                AND provider='google_drive' AND revision=:revision AND status='connected'"""),
                {'entity': row['entity_id'], 'org': row['organization_id'], 'revision': row['revision']})

    def stage(self, *, organization_id, entity_id, profile_key, scope_key, secret_ref, expected_revision, dry_run=False):
        """Operator-only: a tenant Owner must separately activate this exact candidate."""
        with identity_operation(self.database, read_only=dry_run) as connection:
            return self._stage(connection, organization_id=organization_id, entity_id=entity_id,
                profile_key=profile_key, scope_key=scope_key, secret_ref=secret_ref,
                expected_revision=expected_revision, dry_run=dry_run)

    def _stage(self, connection, *, organization_id, entity_id, profile_key, scope_key, secret_ref,
               expected_revision, dry_run=False):
        from tabula_rasa.google_drive import DRIVE_SCOPE
        # Entity lock also serializes exact first registration.
        if not dry_run:
            lock_connection_entity(connection, entity_id)
        owner = connection.scalar(text("""SELECT e.entity_id FROM organization_entities e
            JOIN organizations o ON o.id=e.organization_id AND o.state='active'
            WHERE e.entity_id=:entity AND e.organization_id=:org AND e.state='active'"""),
            {'entity': entity_id, 'org': organization_id})
        if owner is None:
            raise IdentityError('forbidden')
        from types import SimpleNamespace
        row = self._row(connection, SimpleNamespace(entity_id=entity_id, organization_id=organization_id), lock=not dry_run)
        if (row['revision'] if row else 0) != expected_revision:
            raise IdentityError('concurrent_write')
        if row and (row['profile_key'] != profile_key or row['store_scope_key'] != scope_key):
            raise IdentityError('forbidden')
        collision = connection.scalar(text("""SELECT EXISTS (SELECT 1 FROM external_connections
            WHERE provider='google_drive' AND entity_id<>:entity
            AND (profile_key=:profile OR store_scope_key=:scope))"""),
            {'entity': entity_id, 'profile': profile_key, 'scope': scope_key})
        if collision:
            raise IdentityError('forbidden')
        mismatch = connection.scalar(text("""SELECT EXISTS (SELECT 1 FROM documents WHERE entity_id=:entity
            AND (store_kind<>'google_drive' OR store_scope_key<>:scope))"""),
            {'entity': entity_id, 'scope': scope_key})
        if mismatch:
            raise IdentityError('forbidden')
        from tabula_rasa.migration.model import digest
        cutover = connection.execute(text('SELECT * FROM bookkeeping_cutovers WHERE entity_id=:entity'),
                                     {'entity': entity_id}).mappings().first()
        if cutover and (cutover['source_profile_key'] != profile_key
                or cutover['evidence'].get('document_scope_sha256') != digest(scope_key)):
            raise IdentityError('forbidden')
        if dry_run:
            return
        connection.execute(text("""INSERT INTO external_connections
            (entity_id,organization_id,provider,secret_ref,profile_key,store_scope_key,required_scopes,status)
            VALUES (:entity,:org,'google_drive',:secret,:profile,:scope,CAST(:scopes AS jsonb),'pending')
            ON CONFLICT (entity_id,provider) DO UPDATE SET secret_ref=EXCLUDED.secret_ref,
            drive_recovery_binding=NULL, status='pending',
            revision=external_connections.revision+1,updated_at=CURRENT_TIMESTAMP"""),
            {'entity': entity_id, 'org': organization_id, 'secret': secret_ref, 'profile': profile_key,
             'scope': scope_key, 'scopes': json.dumps([DRIVE_SCOPE])})
