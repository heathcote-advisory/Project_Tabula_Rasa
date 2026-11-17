"""Native workspace transactions joining the existing identity/ledger owners."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
from uuid import uuid4

from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.application.ledger_setup import LedgerSetupService
from tabula_rasa.application.workspaces import (
    InvitationCredential, grant_allowed, normalized_email, require_capability, require_bootstrap_id,
)
from .control_plane import ENTITY_ACCESS_SQL, PostgresIdentityRepository, identity_operation
from .reference_data import PostgresLedgerSetupRepository
from .sessions import credential_hash
from . import beta
from .audit import AuditContext, audit_context


class PostgresWorkspaceRepository:
    def __init__(self, database, *, clock=None):
        self.database = database
        self.identity = PostgresIdentityRepository(database)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _users(connection, *users):
        # Match the existing membership triggers: users before organization.
        rows = connection.execute(text('''SELECT id FROM identity_users
            WHERE id=ANY(:users) AND state='active' ORDER BY id FOR UPDATE'''),
            {'users': sorted(set(users))}).scalars().all()
        if len(rows) != len(set(users)):
            raise IdentityError('forbidden')

    @staticmethod
    def _business_access(connection, user, org, *, lock=False):
        if lock and not connection.scalar(text("SELECT id FROM organizations WHERE id=:org AND state='active' FOR UPDATE"), {'org': org}):
            raise IdentityError('forbidden')
        # Compatibility organization routes resolve through the unique business
        # and its current membership; a supplied container ID grants no authority.
        row = connection.execute(text(ENTITY_ACCESS_SQL + ' AND e.organization_id=:org'),
            {'org': org, 'user': user}).mappings().first()
        if row is None:
            raise IdentityError('forbidden')
        return row

    @classmethod
    def _role(cls, connection, user, org, *, lock=False):
        return cls._business_access(connection, user, org, lock=lock)['role']

    def _manage(self, connection, actor, org, *users):
        self._users(connection, actor, *users)
        role = self._role(connection, actor, org, lock=True)
        require_capability(role, 'members:manage')
        return role

    def bootstrap(self, actor, request_id, workspace_name, business_name, organization_id=None, *, country="US", currency="USD", verified=False):
        require_bootstrap_id(request_id)
        names = [workspace_name, business_name]
        if any(not isinstance(name, str) or not name.strip() or len(name) > 160 for name in names):
            raise IdentityError('validation')
        workspace_name, business_name = [name.strip() for name in names]
        values = [workspace_name, business_name, organization_id]
        if (country, currency) != ('US', 'USD'):
            values += [country, currency]
        payload = sha256(json.dumps(values).encode()).hexdigest()
        entity_id = uuid4().hex
        with audit_context(self.database, AuditContext('user', actor, 'workspace.bootstrap', entity_id,
                           operation_id='bootstrap:' + request_id)), identity_operation(self.database) as connection:
            beta.lock_program(connection)
            self._users(connection, actor)
            previous = connection.execute(text('''SELECT * FROM workspace_bootstraps
                WHERE user_id=:user AND request_id=:request'''), {'user': actor, 'request': request_id}).mappings().first()
            if previous:
                if previous['payload_hash'] != payload:
                    raise IdentityError('conflict')
                return self._bootstrap_result(connection, actor, previous, legacy=organization_id is not None)
            # Retain authorized replay of historical receipts, but never append a
            # new business to an existing permission container.
            if organization_id is not None:
                raise IdentityError('forbidden')
            pending = beta.prepare_business(connection, actor, request_id, business_name, country, currency,
                                            verified=verified)
            if pending is not None:
                return pending
            org = self.identity.create_organization(actor, workspace_name)
            ledger = PostgresLedgerSetupRepository.create_entity(self.database, entity_id, business_name, currency=currency)
            self.identity.bind_entity(org, entity_id)
            LedgerSetupService(ledger).ensure_defaults()
            connection.execute(text('''INSERT INTO workspace_bootstraps
                (user_id,request_id,payload_hash,organization_id,entity_id) VALUES (:user,:request,:payload,:org,:entity)'''),
                {'user': actor, 'request': request_id, 'payload': payload, 'org': org, 'entity': entity_id})
            beta.activate_business(connection, actor, request_id, org)
            return {'state': 'ready', 'organization_id': org, 'entity_id': entity_id}

    def _bootstrap_result(self, connection, actor, row, *, legacy=False):
        # The original payload stays immutable, while an approved operator
        # reconciliation can change the receipt's current private container.
        access = self._business_access(connection, actor, row['organization_id'])
        if access['entity_id'] != row['entity_id']:
            raise IdentityError('forbidden')
        if legacy:
            require_capability(access['role'], 'entity:manage')
        return {'state': 'ready', 'organization_id': row['organization_id'], 'entity_id': row['entity_id']}

    def bootstrap_status(self, actor, request_id):
        require_bootstrap_id(request_id)
        with identity_operation(self.database, read_only=True) as connection:
            row = connection.execute(text('''SELECT * FROM workspace_bootstraps
                WHERE user_id=:user AND request_id=:request'''), {'user': actor, 'request': request_id}).mappings().first()
            if row is None:
                admission = connection.execute(text("""SELECT CASE WHEN state='approved' AND expires_at<=clock_timestamp()
                    THEN 'expired' ELSE state END FROM beta_admissions WHERE user_id=:user AND request_id=:request"""),
                    {'user': actor, 'request': request_id}).scalar()
                return {'state': admission or 'not_started', 'organization_id': None, 'entity_id': None}
            return self._bootstrap_result(connection, actor, row)

    def business_operation(self, session, business_id, operation, *args):
        # Thin business adapter: the same transaction and policy/locks own both
        # public business routes and retained organization compatibility routes.
        if operation not in {'members', 'invitations', 'invite', 'change_member', 'grant_ownership', 'revoke_invitation'}:
            raise IdentityError('forbidden')
        read_only = operation in {'members', 'invitations'}
        with identity_operation(self.database, read_only=read_only) as connection:
            business = connection.execute(text(ENTITY_ACCESS_SQL + ' AND e.entity_id=:business'),
                {'user': session.user_id, 'business': business_id}).mappings().first()
            if business is None:
                raise IdentityError('forbidden')
            org = business['organization_id']
            if operation == 'grant_ownership':
                target, version = args
                return self.change_member(session.user_id, org, target, 'owner', True, version, session=session, ownership=True)
            if operation == 'change_member':
                return self.change_member(session.user_id, org, *args, session=session)
            return getattr(self, operation)(session.user_id, org, *args)

    def members(self, actor, org):
        with identity_operation(self.database, read_only=True) as connection:
            require_capability(self._role(connection, actor, org), 'members:manage')
            return [dict(row) for row in connection.execute(text('''SELECT m.user_id,u.display_name,m.role,m.state,
                m.authorization_version FROM memberships m JOIN identity_users u ON u.id=m.user_id
                WHERE m.organization_id=:org ORDER BY m.user_id'''), {'org': org}).mappings()]

    def _settings_access(self, connection, actor, business_id, *, lock=False):
        business = connection.execute(text(ENTITY_ACCESS_SQL + ' AND e.entity_id=:business'),
            {'user': actor, 'business': business_id}).mappings().first()
        if business is None:
            raise IdentityError('forbidden')
        if lock:
            self._manage(connection, actor, business['organization_id'])
            business = self._business_access(connection, actor, business['organization_id'])
        require_capability(business['role'], 'members:manage')
        if business['closure_expires_at'] is not None:
            raise IdentityError('forbidden')

    def business_settings(self, actor, business_id):
        with identity_operation(self.database, read_only=True) as connection:
            self._settings_access(connection, actor, business_id)
            return dict(connection.execute(text('SELECT currency_code,version FROM business_entities WHERE id=:id'),
                {'id': business_id}).mappings().one())

    def change_currency(self, actor, business_id, currency, version):
        if currency not in {'USD', 'CAD'} or not isinstance(version, int) or version < 1:
            raise IdentityError('validation')
        with audit_context(self.database, AuditContext('user', actor, 'business.currency', business_id)), identity_operation(self.database) as connection:
            # Match closure's drain order before taking user/organization locks.
            connection.execute(text('SELECT pg_advisory_xact_lock_shared(346,hashtext(:scope))'), {'scope': business_id})
            self._settings_access(connection, actor, business_id, lock=True)
            row = connection.execute(text('SELECT currency_code,version FROM business_entities WHERE id=:id FOR UPDATE'),
                {'id': business_id}).mappings().one()
            if row['version'] != version:
                raise IdentityError('stale')
            if row['currency_code'] == currency:
                return dict(row)
            return dict(connection.execute(text('''UPDATE business_entities SET currency_code=:currency
                WHERE id=:id RETURNING currency_code,version'''), {'id': business_id, 'currency': currency}).mappings().one())

    def change_member(self, actor, org, target, role, active, version, *, session=None, ownership=False):
        with identity_operation(self.database) as connection:
            beta.lock_program(connection)
            acting_role = self._manage(connection, actor, org, target)
            current = connection.execute(text('''SELECT role,state,authorization_version FROM memberships
                WHERE organization_id=:org AND user_id=:user'''), {'org': org, 'user': target}).mappings().first()
            if current is None:
                raise IdentityError('forbidden')
            if current['authorization_version'] != version:
                raise IdentityError('stale')
            # Admins can demote themselves, but cannot alter an Owner or peer
            # Admin. DB deferred constraints protect the last active Owner.
            if acting_role != 'owner' and (current['role'] == 'owner'
                    or current['role'] == 'admin' and actor != target
                    or role != current['role'] and not grant_allowed(acting_role, role)):
                raise IdentityError('forbidden')
            if role == 'owner' and acting_role != 'owner':
                raise IdentityError('forbidden')
            if role == 'owner' and (current['role'] != 'owner' or current['state'] != 'active' and active) and not ownership:
                raise IdentityError('forbidden')
            if ownership or current['role'] == 'owner' and (role != 'owner' or not active):
                self._require_fresh(session)
            if active and current['state'] != 'active':
                grant = beta.invitation_grant(connection, actor, org, user=target)
                if grant:
                    connection.execute(text("UPDATE beta_named_users SET state='active',ever_active=true WHERE admission_id=:id AND user_id=:user"),
                                       {'id': grant['admission_id'], 'user': target})
                    beta.event(connection, actor, 'member_readmitted', grant['admission_id'], user_id=target)
            if not active:
                admission = connection.scalar(text('SELECT id FROM beta_admissions WHERE organization_id=:org'), {'org':org})
                if admission:
                    connection.execute(text("UPDATE beta_named_users SET state='withdrawn' WHERE admission_id=:id AND user_id=:user"),
                                       {'id':admission,'user':target})
                    beta.event(connection,actor,'member_withdrawn',admission,user_id=target)
            self.identity.set_membership(org, target, role, active=active)

    def invite(self, actor, org, email, role):
        raw, invitation_id = secrets.token_urlsafe(32), uuid4().hex
        with identity_operation(self.database) as connection:
            beta.lock_program(connection)
            acting_role = self._manage(connection, actor, org)
            if not grant_allowed(acting_role, role):
                raise IdentityError('forbidden')
            grant = beta.invitation_grant(connection, actor, org, email=normalized_email(email))
            expiry = self.clock() + timedelta(hours=48)
            if grant:
                expiry = min(expiry, grant['expires_at'])
            connection.execute(text('''INSERT INTO workspace_invitations
                (id,credential_hash,organization_id,email_key,role,inviter_id,expires_at)
                VALUES (:id,:hash,:org,:email,:role,:actor,:expiry)'''),
                {'id': invitation_id, 'hash': credential_hash(raw), 'org': org, 'email': normalized_email(email),
                 'role': role, 'actor': actor, 'expiry': expiry})
            if grant:
                connection.execute(text("""UPDATE beta_named_users SET state='invited',invitation_id=:invitation,
                    expires_at=:expiry,membership_version=(SELECT authorization_version FROM memberships
                    WHERE organization_id=:org AND user_id=:user) WHERE admission_id=:id AND user_id=:user"""),
                    {'invitation':invitation_id,'expiry':expiry,'org':org,
                     'id':grant['admission_id'],'user':grant['user_id']})
                beta.event(connection,actor,'invited',grant['admission_id'],user_id=grant['user_id'])
        return InvitationCredential(invitation_id, raw)

    def invitations(self, actor, org):
        with identity_operation(self.database, read_only=True) as connection:
            require_capability(self._role(connection, actor, org), 'members:manage')
            return [dict(row) for row in connection.execute(text('''SELECT id,email_key AS recipient_email,role,expires_at,
                CASE WHEN state='pending' AND expires_at<=:now THEN 'expired' ELSE state END AS state
                FROM workspace_invitations WHERE organization_id=:org ORDER BY created_at DESC'''),
                {'org': org, 'now': self.clock()}).mappings()]

    def revoke_invitation(self, actor, org, invitation_id):
        with identity_operation(self.database) as connection:
            beta.lock_program(connection)
            acting_role = self._manage(connection, actor, org)
            row = connection.execute(text('''SELECT role FROM workspace_invitations
                WHERE id=:id AND organization_id=:org FOR UPDATE'''), {'id': invitation_id, 'org': org}).mappings().first()
            if row is None or not grant_allowed(acting_role, row['role']):
                raise IdentityError('forbidden')
            connection.execute(text("UPDATE workspace_invitations SET state='revoked' WHERE id=:id AND state='pending'"), {'id': invitation_id})
            beta.release_invitation(connection, actor, invitation_id)

    def respond(self, session, credential, accept):
        self._require_fresh(session)
        hashed = credential_hash(credential)
        with identity_operation(self.database) as connection:
            beta.lock_program(connection)
            # Discover only internally, then use the standard user/org lock order.
            row = self._recipient_invitation(connection, session, hashed)
            self._users(connection, session.user_id, row['inviter_id'])
            acting_role = self._role(connection, row['inviter_id'], row['organization_id'], lock=True)
            row = self._recipient_invitation(connection, session, hashed, lock=True)
            self._require_fresh(session)
            self._require_pending(row)
            if not grant_allowed(acting_role, row['role']):
                raise IdentityError('forbidden')
            if accept:
                # Internal invite authority is the explicitly retained operator,
                # whereas beta offers bind the exact approved recipient identity.
                grant = beta.invitation_grant(connection, row['inviter_id'], row['organization_id'],
                    user=session.user_id, invitation=row['id'], email=normalized_email(session.verified_email))
                if grant:
                    version = connection.scalar(text('SELECT authorization_version FROM memberships WHERE organization_id=:org AND user_id=:user'),
                        {'org':row['organization_id'],'user':session.user_id})
                    if version != grant['membership_version']:
                        raise IdentityError('beta_pending')
                    connection.execute(text("UPDATE beta_named_users SET state='active',ever_active=true WHERE admission_id=:id AND user_id=:user"),
                                       {'id':grant['admission_id'],'user':session.user_id})
                    beta.event(connection,session.user_id,'invitation_accepted',grant['admission_id'])
                existing = connection.scalar(text('''SELECT state FROM memberships
                    WHERE organization_id=:org AND user_id=:user'''), {'org': row['organization_id'], 'user': session.user_id})
                if existing != 'active':
                    self.identity.set_membership(row['organization_id'], session.user_id, row['role'])
            if not accept:
                beta.release_invitation(connection, session.user_id, row['id'])
            connection.execute(text('UPDATE workspace_invitations SET state=:state,responded_by=:user WHERE id=:id'),
                {'id': row['id'], 'user': session.user_id, 'state': 'accepted' if accept else 'declined'})
            return {'state': 'accepted' if accept else 'declined'}

    def preview_invitation(self, session, credential):
        """Only the verified recipient may discover the one offered business."""
        self._require_fresh(session)
        with identity_operation(self.database, read_only=True) as connection:
            row = self._recipient_invitation(connection, session, credential_hash(credential))
            self._require_pending(row)
            business = self._business_access(connection, row['inviter_id'], row['organization_id'])
            if not grant_allowed(business['role'], row['role']):
                raise IdentityError('forbidden')
            existing_role = connection.scalar(text("""SELECT role FROM memberships
                WHERE organization_id=:org AND user_id=:user AND state='active'"""),
                {'org': row['organization_id'], 'user': session.user_id})
            return {'business_id': business['entity_id'], 'business_name': business['name'],
                    'role': existing_role or row['role'], 'expires_at': row['expires_at']}

    @staticmethod
    def _recipient_invitation(connection, session, hashed, *, lock=False):
        query = 'SELECT * FROM workspace_invitations WHERE credential_hash=:hash'
        row = connection.execute(text(query + (' FOR UPDATE' if lock else '')), {'hash': hashed}).mappings().first()
        if row is None or row['email_key'] != normalized_email(session.verified_email):
            raise IdentityError('invitation_invalid')
        return row

    def _require_pending(self, row):
        if row['state'] != 'pending':
            raise IdentityError('invitation_used')
        if row['expires_at'] <= self.clock():
            raise IdentityError('invitation_expired')

    def _require_fresh(self, session):
        # Match the verified OIDC auth_time tolerance; retain the five-minute
        # upper bound rather than treating session validity as recent login.
        if (session is None or not session.verified_email
                or not -timedelta(seconds=60) <= self.clock() - session.authenticated_at <= timedelta(minutes=5)):
            raise IdentityError('reauthentication_required')
