"""Public policy bytes and named, CSRF-protected explicit acknowledgement."""

from fastapi import APIRouter, Request
from pydantic import Field

from tabula_rasa.application.identity import IdentityError
from ..models.common import DTO
from ..errors import ERROR_RESPONSES

router = APIRouter(tags=['Customer policies'], responses=ERROR_RESPONSES)


class PublicPolicies(DTO):
    version: str
    effective_date: str
    terms: str
    privacy: str
    support_email: str | None
    beta: bool
    digest: str


class PolicyStatus(DTO):
    digest: str
    required: bool
    acknowledged: bool


class PolicyAcceptance(DTO):
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    accepted: bool


def workflow(request):
    runtime = request.app.state.runtime
    if runtime.identity is None or runtime.policy_acknowledgements is None:
        raise IdentityError('forbidden')
    return runtime.policy_acknowledgements, runtime.identity.authenticated(request)


def require_acknowledgement(request, session):
    # Existing customers keep read/export/closure access during policy updates.
    if not request.app.state.runtime.policy_acknowledgements.acknowledged(
            session, request.app.state.customer_policies):
        raise IdentityError('required')


@router.get('/api/v1/public/policies', response_model=PublicPolicies, operation_id='public_policies')
def public_policies(request: Request):
    policies = request.app.state.customer_policies
    return PublicPolicies(**vars(policies), digest=policies.digest)


@router.get('/api/v1/policies/acknowledgement', response_model=PolicyStatus, operation_id='policy_status')
def policy_status(request: Request):
    policies = request.app.state.customer_policies
    service, session = workflow(request)
    return PolicyStatus(digest=policies.digest, required=policies.beta,
                        acknowledged=service.acknowledged(session, policies) if policies.beta else False)


@router.post('/api/v1/policies/acknowledgement', response_model=PolicyStatus, operation_id='accept_policies')
def accept_policies(body: PolicyAcceptance, request: Request):
    if not body.accepted:
        raise IdentityError('required')
    policies = request.app.state.customer_policies
    service, session = workflow(request)
    service.accept(session, policies, body.digest)
    return PolicyStatus(digest=policies.digest, required=True, acknowledged=True)
