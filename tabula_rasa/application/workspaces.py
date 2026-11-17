"""Named-user workspace workflows, separate from ledger and provider identity."""

from dataclasses import dataclass, field
import re

from .identity import IdentityError, capabilities

BOOTSTRAP_ID_PATTERN = r'^[A-Za-z0-9_-]{16,128}$'


def require_bootstrap_id(request_id):
    if not isinstance(request_id, str) or re.fullmatch(BOOTSTRAP_ID_PATTERN, request_id) is None:
        raise IdentityError('validation')


def grant_allowed(actor_role, target_role):
    return (actor_role == 'owner' and target_role in {'admin', 'editor', 'accountant', 'read_only'}
            or actor_role == 'admin' and target_role in {'editor', 'accountant', 'read_only'})


def normalized_email(value):
    if not isinstance(value, str) or not value.strip() or '@' not in value or len(value) > 320:
        raise IdentityError('validation')
    return value.strip().casefold()


@dataclass(frozen=True)
class InvitationCredential:
    id: str
    credential: str = field(repr=False)


class WorkspaceService:
    def __init__(self, repository, *, bootstrap_enabled=True):
        self.repository = repository
        self.bootstrap_enabled = bootstrap_enabled

    def bootstrap(self, session, request_id, workspace_name, business_name, organization_id=None, *, country="US", currency="USD"):
        if not self.bootstrap_enabled:
            raise IdentityError('forbidden')
        return self.repository.bootstrap(session.user_id, request_id, workspace_name, business_name, organization_id, country=country, currency=currency,
                                         verified=bool(session.verified_email))

    def create_business(self, session, request_id, business_name, *, country="US", currency="USD"):
        # The backing organization is private and never selected by the caller.
        return self.bootstrap(session, request_id, 'Private business', business_name, country=country, currency=currency)

    def bootstrap_status(self, session, request_id):
        return self.repository.bootstrap_status(session.user_id, request_id)

    def business_operation(self, session, business_id, operation, *args):
        """Resolve the private container server-side and reuse its policy owner."""
        return self.repository.business_operation(session, business_id, operation, *args)

    def preview_invitation(self, session, credential):
        return self.repository.preview_invitation(session, credential)

    def business_settings(self, session, business_id):
        return self.repository.business_settings(session.user_id, business_id)

    def change_currency(self, session, business_id, currency, version, confirmed):
        if currency not in {'USD', 'CAD'} or confirmed is not True or not isinstance(version, int) or version < 1:
            raise IdentityError('validation')
        return self.repository.change_currency(session.user_id, business_id, currency, version)

    def members(self, session, organization_id):
        return self.repository.members(session.user_id, organization_id)

    def invitations(self, session, organization_id):
        return self.repository.invitations(session.user_id, organization_id)

    def invite(self, session, organization_id, email, role):
        return self.repository.invite(session.user_id, organization_id, normalized_email(email), role)

    def change_member(self, session, organization_id, user_id, role, active, version):
        return self.repository.change_member(session.user_id, organization_id, user_id, role, active, version, session=session)

    def grant_ownership(self, session, organization_id, user_id, version):
        return self.repository.change_member(session.user_id, organization_id, user_id, 'owner', True, version, session=session, ownership=True)

    def revoke_invitation(self, session, organization_id, invitation_id):
        return self.repository.revoke_invitation(session.user_id, organization_id, invitation_id)

    def respond(self, session, credential, accept):
        return self.repository.respond(session, credential, accept)


def require_capability(role, capability):
    if capability not in capabilities(role):
        raise IdentityError('forbidden')
