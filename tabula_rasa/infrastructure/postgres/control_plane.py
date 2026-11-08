"""Commercial control plane; no ledger/profile/provider-credential ownership."""

from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import text

from tabula_rasa.application.identity import EntityAccess, IdentityError, IdentityStorageUnavailable, ROLE_CAPABILITIES, User
from .runtime import DatabaseError


@contextmanager
def identity_operation(database, *, read_only=False):
    try:
        with database.transaction(read_only=read_only) as connection:
            yield connection
    except DatabaseError as error:
        code = error.code
    else:
        return
    if code in {'duplicate', 'reference', 'invariant', 'required', 'concurrent_write'}:
        raise IdentityError(code) from None
    raise IdentityStorageUnavailable(exhausted=code == 'database_exhausted') from None


ENTITY_ACCESS_SQL = '''SELECT e.entity_id, e.organization_id, b.name, b.currency_code, m.role,
    m.authorization_version, o.name AS organization_name,
    (SELECT recover_until FROM customer_lifecycle_requests WHERE entity_id=e.entity_id
        AND kind='business_closure' AND state='confirmed') AS closure_expires_at,
    COALESCE((SELECT sum(CASE WHEN confirmed_at IS NOT NULL THEN version ELSE 0 END)::bigint
        FROM customer_lifecycle_requests WHERE entity_id=e.entity_id
        AND kind='business_closure'),0) AS lifecycle_version FROM organization_entities e
    JOIN business_entities b ON b.id=e.entity_id
    JOIN organizations o ON o.id=e.organization_id AND o.state='active'
    JOIN memberships m ON m.organization_id=o.id AND m.user_id=:user AND m.state='active'
    JOIN identity_users u ON u.id=m.user_id AND u.state='active'
    WHERE e.state='active'
    AND NOT EXISTS (SELECT 1 FROM customer_lifecycle_requests closed WHERE closed.entity_id=e.entity_id
        AND closed.kind='business_closure' AND closed.state='confirmed' AND closed.recover_until<=clock_timestamp())
    AND NOT EXISTS (SELECT 1 FROM organization_entities other
        WHERE other.organization_id=e.organization_id AND other.entity_id<>e.entity_id) '''


def entity_access(connection, user_id):
    return tuple(EntityAccess(**row) for row in connection.execute(
        text(ENTITY_ACCESS_SQL + 'ORDER BY e.organization_id,e.entity_id'), {'user': user_id}).mappings())


class PostgresIdentityRepository:
    def __init__(self, database):
        self.database = database

    def resolve_user(self, identity):
        if not all(isinstance(v, str) and v.strip() for v in (identity.issuer, identity.subject, identity.email)):
            raise IdentityError('unauthorized')
        with identity_operation(self.database) as connection:
            # Serializes first-login mapping of this exact subject; never links by email.
            connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:binding,311))'),
                               {'binding': identity.issuer + '\x1f' + identity.subject})
            row = connection.execute(text('''SELECT u.* FROM identity_bindings b JOIN identity_users u ON u.id=b.user_id
                WHERE b.issuer=:issuer AND b.subject=:subject FOR UPDATE OF u'''),
                {'issuer': identity.issuer, 'subject': identity.subject}).mappings().first()
            if row is None:
                user_id = uuid4().hex
                connection.execute(text('''INSERT INTO identity_users(id,email,display_name) VALUES (:id,:email,:name)'''),
                    {'id': user_id, 'email': identity.email, 'name': identity.display_name})
                connection.execute(text('''INSERT INTO identity_bindings(id,user_id,issuer,subject)
                    VALUES (:id,:user,:issuer,:subject)'''),
                    {'id': uuid4().hex, 'user': user_id, 'issuer': identity.issuer, 'subject': identity.subject})
                return User(user_id, 'active', 1)
            if row['state'] != 'active':
                raise IdentityError('unauthorized')
            connection.execute(text('UPDATE identity_users SET email=:email,display_name=:name WHERE id=:id'),
                {'id': row['id'], 'email': identity.email, 'name': identity.display_name})
            return User(row['id'], row['state'], row['session_epoch'])

    def create_organization(self, owner_id, name):
        if not isinstance(name, str) or not name.strip():
            raise IdentityError('required')
        with identity_operation(self.database) as connection:
            if not connection.scalar(text("SELECT id FROM identity_users WHERE id=:id AND state='active' FOR UPDATE"), {'id': owner_id}):
                raise IdentityError('forbidden')
            org = uuid4().hex
            connection.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'), {'id': org, 'name': name.strip()})
            connection.execute(text("INSERT INTO memberships(organization_id,user_id,role) VALUES (:org,:user,'owner')"),
                {'org': org, 'user': owner_id})
            return org

    def active_memberships(self, user_id):
        with identity_operation(self.database, read_only=True) as connection:
            return tuple(dict(row) for row in connection.execute(text('''SELECT m.organization_id,m.role,m.authorization_version
                FROM memberships m JOIN organizations o ON o.id=m.organization_id AND o.state='active'
                JOIN identity_users u ON u.id=m.user_id AND u.state='active'
                WHERE m.user_id=:user AND m.state='active' ORDER BY m.organization_id'''), {'user': user_id}).mappings())

    def allowed_entities(self, user_id):
        with identity_operation(self.database, read_only=True) as connection:
            return entity_access(connection, user_id)

    def bind_entity(self, organization_id, entity_id):
        with identity_operation(self.database) as connection:
            connection.execute(text('SELECT id FROM organizations WHERE id=:org FOR UPDATE'), {'org': organization_id})
            current = connection.scalar(text('SELECT organization_id FROM organization_entities WHERE entity_id=:entity'), {'entity': entity_id})
            if current is not None:
                if current != organization_id:
                    raise IdentityError('forbidden')
                return
            if connection.scalar(text('SELECT entity_id FROM organization_entities WHERE organization_id=:org'),
                                 {'org': organization_id}) is not None:
                raise IdentityError('forbidden')
            connection.execute(text('INSERT INTO organization_entities(entity_id,organization_id) VALUES (:entity,:org)'),
                {'entity': entity_id, 'org': organization_id})

    def set_membership(self, organization_id, user_id, role, *, active=True):
        if role not in ROLE_CAPABILITIES or not isinstance(active, bool):
            raise IdentityError('invariant')
        with identity_operation(self.database) as connection:
            connection.execute(text('''INSERT INTO memberships(organization_id,user_id,role,state) VALUES (:org,:user,:role,:state)
                ON CONFLICT (organization_id,user_id) DO UPDATE SET role=EXCLUDED.role,state=EXCLUDED.state'''),
                {'org': organization_id, 'user': user_id, 'role': role, 'state': 'active' if active else 'inactive'})

    def set_user_state(self, user_id, state):
        if state not in {'active', 'disabled'}:
            raise IdentityError('invariant')
        self._update('identity_users', user_id, state)

    def set_organization_state(self, organization_id, state):
        if state not in {'active', 'suspended'}:
            raise IdentityError('invariant')
        self._update('organizations', organization_id, state)

    def _update(self, table, identity, state):
        with identity_operation(self.database) as connection:
            if not connection.scalar(text(f'UPDATE {table} SET state=:state WHERE id=:id RETURNING id'), {'state': state, 'id': identity}):
                raise IdentityError('forbidden')

    def revoke_user_sessions(self, user_id):
        with identity_operation(self.database) as connection:
            if not connection.scalar(text('UPDATE identity_users SET session_epoch=session_epoch+1 WHERE id=:id RETURNING id'), {'id': user_id}):
                raise IdentityError('forbidden')
