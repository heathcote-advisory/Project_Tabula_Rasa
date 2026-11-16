from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr

from .common import DTO
from tabula_rasa.application.workspaces import BOOTSTRAP_ID_PATTERN

Role = Literal['owner', 'admin', 'editor', 'accountant', 'read_only']


class BusinessSettings(DTO):
    currency_code: Literal['USD', 'CAD']
    version: int = Field(ge=1)


class BusinessCurrencyChange(BusinessSettings):
    confirm_label_change: Literal[True]


class BusinessCreateRequest(DTO):
    request_id: str = Field(min_length=16, max_length=128, pattern=BOOTSTRAP_ID_PATTERN)
    business_name: str = Field(min_length=1, max_length=160)
    country: Literal['US', 'CA'] = 'US'
    currency: Literal['USD', 'CAD'] = 'USD'


class BusinessCreateResult(DTO):
    state: Literal['not_started', 'pending', 'approved', 'expired', 'denied', 'withdrawn', 'suspended', 'closed', 'active', 'ready']
    entity_id: str | None = None


class BootstrapRequest(DTO):
    request_id: str = Field(min_length=16, max_length=128, pattern=BOOTSTRAP_ID_PATTERN)
    workspace_name: str = Field(min_length=1, max_length=160)
    business_name: str = Field(min_length=1, max_length=160)
    country: Literal['US', 'CA'] = 'US'
    currency: Literal['USD', 'CAD'] = 'USD'


class BootstrapResult(DTO):
    state: Literal['not_started', 'pending', 'approved', 'expired', 'denied', 'withdrawn', 'suspended', 'closed', 'active', 'ready']
    organization_id: str | None = None
    entity_id: str | None = None


class Member(DTO):
    user_id: str
    display_name: str
    role: Role
    state: Literal['active', 'inactive']
    authorization_version: int


class MemberList(DTO):
    items: list[Member]


class MemberChange(DTO):
    role: Role
    active: bool
    authorization_version: int = Field(ge=1)


class OwnershipGrant(DTO):
    authorization_version: int = Field(ge=1)


class InviteRequest(DTO):
    email: str = Field(min_length=3, max_length=320)
    role: Literal['admin', 'editor', 'accountant', 'read_only']


class InviteIssued(DTO):
    id: str
    credential: str = Field(repr=False)


class Invitation(DTO):
    id: str
    recipient_email: str = Field(repr=False)
    role: Role
    state: Literal['pending', 'accepted', 'declined', 'expired', 'revoked']
    expires_at: datetime


class InvitationList(DTO):
    items: list[Invitation]


class InvitationResponse(DTO):
    credential: SecretStr = Field(min_length=32, max_length=256)
    accept: bool


class InvitationPreviewRequest(DTO):
    credential: SecretStr = Field(min_length=32, max_length=256)


class InvitationPreview(DTO):
    business_id: str
    business_name: str
    role: Role
    expires_at: datetime


class InvitationOutcome(DTO):
    state: Literal['accepted', 'declined']


class BetaRequestStatus(DTO):
    request_id: str
    business_name: str
    country: Literal['US', 'CA']
    state: str


class BetaStatus(DTO):
    paused: bool
    invitations_paused: bool
    internal: bool
    requests: list[BetaRequestStatus]


class BetaOperatorCommand(DTO):
    operation: Literal['inspect', 'configure', 'approve', 'approve_user', 'deny', 'withdraw', 'suspend', 'resume', 'close', 'withdraw_user']
    revision: int | None = Field(default=None, ge=1)
    reason: str = Field(default='', max_length=200)
    approval_reference: str = Field(default='', max_length=200)
    admission_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    stage: Literal['initial', 'expanded'] = 'initial'
    business_cap: int = Field(default=3, ge=1, le=10)
    user_cap: int | None = Field(default=None, ge=1, le=20)
    creation_paused: bool = True
    invitations_paused: bool = True
    suspended: bool = False
