"""Provider/storage-neutral identity records and the single v1 role policy."""

from dataclasses import dataclass, field
from datetime import datetime


LOGIN_TRANSACTION_SECONDS = 300
REGISTRATION_TRANSACTION_SECONDS = 900


LOGIN_FAILURE_REASONS = frozenset({
    'login_start', 'login_transaction', 'provider_authorization', 'token_exchange',
    'token_validation', 'login_freshness', 'userinfo_validation', 'session_issue',
})


class IdentityError(Exception):
    def __init__(self, code, *, login_reason=None):
        self.code = code
        self.login_reason = login_reason if login_reason in LOGIN_FAILURE_REASONS else None
        super().__init__(code)


class IdentityStorageUnavailable(IdentityError):
    def __init__(self, *, exhausted):
        self.exhausted = exhausted
        super().__init__("unavailable")


READ = frozenset({'bookkeeping:read'})
WRITE = READ | {'bookkeeping:write'}
HANDOFF = WRITE | {'bookkeeping:import', 'bookkeeping:export'}
ADMIN = HANDOFF | {'members:manage', 'period:close', 'bookkeeping:correct'}
ROLE_CAPABILITIES = {
    'owner': ADMIN | {'entity:manage', 'organization:manage', 'connections:manage',
                      'ownership:manage', 'backup:create', 'backup:list', 'backup:download',
                      'archive:recover', 'restore:execute', 'migration:execute', 'cutover:execute',
                      'ledger:reset', 'recovery:authorize', 'business:admin', 'period:unlock'},
    'admin': ADMIN, 'editor': WRITE, 'accountant': HANDOFF | {'bookkeeping:correct'}, 'read_only': READ,
}


def capabilities(role):
    return frozenset(ROLE_CAPABILITIES.get(role, ()))


@dataclass(frozen=True)
class User:
    id: str
    state: str
    session_epoch: int


@dataclass(frozen=True)
class EntityAccess:
    entity_id: str
    organization_id: str
    name: str
    role: str
    authorization_version: int
    organization_name: str = ''
    currency_code: str = 'USD'
    closure_expires_at: datetime | None = None
    lifecycle_version: int = 0


def access_capabilities(access):
    granted = capabilities(access.role)
    if access.closure_expires_at is not None:
        # Freeze bookkeeping while preserving membership/ownership recovery and
        # security administration under their existing independent rules.
        return granted - {'bookkeeping:write', 'bookkeeping:import', 'bookkeeping:correct',
                          'period:close', 'period:unlock'}
    return granted


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str = field(repr=False)
    subject: str = field(repr=False)
    email: str = field(repr=False)
    display_name: str = field(repr=False)
    authenticated_at: datetime


@dataclass(frozen=True)
class AuthenticatedSession:
    user_id: str
    cache_id: str
    csrf_token: str = field(repr=False)
    authenticated_at: datetime
    absolute_expires_at: datetime
    selected: EntityAccess | None
    verified_email: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class LoginTransaction:
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    verifier: str = field(repr=False)
    return_path: str


class IdentityService:
    """Internal control-plane facade; callers enforce action and membership policy."""

    def __init__(self, repository):
        self.repository = repository

    def resolve_user(self, identity):
        return self.repository.resolve_user(identity)

    def create_organization(self, owner_id, name):
        return self.repository.create_organization(owner_id, name)

    def active_memberships(self, user_id):
        return self.repository.active_memberships(user_id)

    def allowed_entities(self, user_id):
        return self.repository.allowed_entities(user_id)

    def bind_entity(self, organization_id, entity_id):
        return self.repository.bind_entity(organization_id, entity_id)

    def set_membership(self, organization_id, user_id, role, *, active=True):
        return self.repository.set_membership(organization_id, user_id, role, active=active)

    def set_user_state(self, user_id, state):
        return self.repository.set_user_state(user_id, state)

    def set_organization_state(self, organization_id, state):
        return self.repository.set_organization_state(organization_id, state)

    def revoke_user_sessions(self, user_id):
        return self.repository.revoke_user_sessions(user_id)
