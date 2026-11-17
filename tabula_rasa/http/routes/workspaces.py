"""Transport-only workspace handlers; named identity without a selected ledger."""

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response
from tabula_rasa.application.workspaces import BOOTSTRAP_ID_PATTERN

from ..errors import ERROR_RESPONSES, TransportError
from ..models.common import ErrorEnvelope
from ..context import RequestContext
from tabula_rasa.application.identity import IdentityError, capabilities
from ..models.workspaces import (
    BusinessCreateRequest, BusinessCreateResult, BootstrapRequest, BootstrapResult, MemberChange, MemberList, InviteRequest,
    InviteIssued, InvitationList, InvitationResponse, InvitationOutcome, OwnershipGrant,
    InvitationPreview, InvitationPreviewRequest, BetaStatus, BetaOperatorCommand,
    BusinessSettings, BusinessCurrencyChange,
)

router = APIRouter(responses=ERROR_RESPONSES, tags=['Business access'])


def workflow(request):
    runtime = request.app.state.runtime
    if runtime.identity is None:
        raise TransportError('forbidden')
    return runtime.workspaces, runtime.identity.authenticated(request)


def business_workflow(request, business_id):
    service, session = workflow(request)
    identity = request.app.state.runtime.identity
    business = next((item for item in identity.sessions.identity.allowed_entities(session.user_id)
                     if item.entity_id == business_id), None)
    if business is None:
        raise IdentityError('forbidden')
    request.state.telemetry_context = RequestContext(session.user_id, business.organization_id,
        business.entity_id, capabilities(business.role), identity.scope(session), session.csrf_token)
    return service, session


@router.get('/api/v1/businesses/{business_id}/settings', response_model=BusinessSettings, operation_id='business_settings')
def business_settings(business_id: str, request: Request):
    service, session = business_workflow(request, business_id)
    return service.business_settings(session, business_id)


@router.patch('/api/v1/businesses/{business_id}/settings/currency', response_model=BusinessSettings, operation_id='business_change_currency')
def business_change_currency(business_id: str, body: BusinessCurrencyChange, request: Request):
    service, session = business_workflow(request, business_id)
    return service.change_currency(session, business_id, body.currency_code, body.version, body.confirm_label_change)


@router.post('/api/v1/businesses', response_model=BusinessCreateResult, operation_id='create_business', tags=['Businesses'])
def create_business(body: BusinessCreateRequest, request: Request):
    service, session = workflow(request)
    result = service.create_business(session, body.request_id, body.business_name, country=body.country, currency=body.currency)
    return BusinessCreateResult(state=result['state'], entity_id=result.get('entity_id'))


@router.get('/api/v1/businesses/creation/{request_id}', response_model=BusinessCreateResult,
            operation_id='business_creation_status', tags=['Businesses'])
def business_creation_status(request_id: Annotated[str, Path(pattern=BOOTSTRAP_ID_PATTERN)], request: Request):
    service, session = workflow(request)
    result = service.bootstrap_status(session, request_id)
    return BusinessCreateResult(state=result['state'], entity_id=result.get('entity_id'))


@router.get('/api/v1/businesses/{business_id}/members', response_model=MemberList, operation_id='business_members')
def business_members(business_id: str, request: Request):
    service, session = business_workflow(request, business_id)
    return MemberList(items=service.business_operation(session, business_id, 'members'))


@router.patch('/api/v1/businesses/{business_id}/members/{user_id}', status_code=204, operation_id='business_change_member')
def business_change_member(business_id: str, user_id: str, body: MemberChange, request: Request):
    service, session = business_workflow(request, business_id)
    service.business_operation(session, business_id, 'change_member', user_id, body.role, body.active, body.authorization_version)
    return Response(status_code=204)


@router.post('/api/v1/businesses/{business_id}/members/{user_id}/ownership', status_code=204, operation_id='business_grant_ownership')
def business_grant_ownership(business_id: str, user_id: str, body: OwnershipGrant, request: Request):
    service, session = business_workflow(request, business_id)
    service.business_operation(session, business_id, 'grant_ownership', user_id, body.authorization_version)
    return Response(status_code=204)


@router.get('/api/v1/businesses/{business_id}/invitations', response_model=InvitationList, operation_id='business_invitations')
def business_invitations(business_id: str, request: Request):
    service, session = business_workflow(request, business_id)
    return InvitationList(items=service.business_operation(session, business_id, 'invitations'))


@router.post('/api/v1/businesses/{business_id}/invitations', response_model=InviteIssued, operation_id='business_invite')
def business_invite(business_id: str, body: InviteRequest, request: Request):
    service, session = business_workflow(request, business_id)
    result = service.business_operation(session, business_id, 'invite', body.email, body.role)
    return InviteIssued(id=result.id, credential=result.credential)


@router.delete('/api/v1/businesses/{business_id}/invitations/{invitation_id}', status_code=204, operation_id='business_revoke_invitation')
def business_revoke_invitation(business_id: str, invitation_id: str, request: Request):
    service, session = business_workflow(request, business_id)
    service.business_operation(session, business_id, 'revoke_invitation', invitation_id)
    return Response(status_code=204)


@router.post('/api/v1/invitations/preview', response_model=InvitationPreview, operation_id='preview_invitation',
             responses={410: {'model': ErrorEnvelope, 'description': 'Invitation expired'}})
def preview_invitation(body: InvitationPreviewRequest, request: Request):
    service, session = workflow(request)
    return service.preview_invitation(session, body.credential.get_secret_value())


@router.post('/api/v1/workspaces/bootstrap', response_model=BootstrapResult, operation_id='bootstrap_workspace', deprecated=True)
def bootstrap_workspace(body: BootstrapRequest, request: Request):
    service, session = workflow(request)
    return service.bootstrap(session, body.request_id, body.workspace_name, body.business_name, country=body.country, currency=body.currency)


@router.get('/api/v1/workspaces/bootstrap/{request_id}', response_model=BootstrapResult, operation_id='bootstrap_status', deprecated=True)
def bootstrap_status(request_id: Annotated[str, Path(pattern=BOOTSTRAP_ID_PATTERN)], request: Request):
    service, session = workflow(request)
    return service.bootstrap_status(session, request_id)


@router.post('/api/v1/workspaces/{organization_id}/businesses', response_model=BootstrapResult, operation_id='bootstrap_business', deprecated=True)
def bootstrap_business(organization_id: str, body: BootstrapRequest, request: Request):
    service, session = workflow(request)
    return service.bootstrap(session, body.request_id, body.workspace_name, body.business_name, organization_id, country=body.country, currency=body.currency)


@router.get('/api/v1/workspaces/{organization_id}/members', response_model=MemberList, operation_id='list_members', deprecated=True)
def list_members(organization_id: str, request: Request):
    service, session = workflow(request)
    return MemberList(items=service.members(session, organization_id))


@router.patch('/api/v1/workspaces/{organization_id}/members/{user_id}', status_code=204, operation_id='change_member', deprecated=True)
def change_member(organization_id: str, user_id: str, body: MemberChange, request: Request):
    service, session = workflow(request)
    service.change_member(session, organization_id, user_id, body.role, body.active, body.authorization_version)
    return Response(status_code=204)


@router.get('/api/v1/workspaces/{organization_id}/invitations', response_model=InvitationList, operation_id='list_invitations', deprecated=True)
def list_invitations(organization_id: str, request: Request):
    service, session = workflow(request)
    return InvitationList(items=service.invitations(session, organization_id))


@router.post('/api/v1/workspaces/{organization_id}/members/{user_id}/ownership', status_code=204, operation_id='grant_ownership', deprecated=True)
def grant_ownership(organization_id: str, user_id: str, body: OwnershipGrant, request: Request):
    service, session = workflow(request)
    service.grant_ownership(session, organization_id, user_id, body.authorization_version)
    return Response(status_code=204)


@router.post('/api/v1/workspaces/{organization_id}/invitations', response_model=InviteIssued, operation_id='create_invitation', deprecated=True)
def create_invitation(organization_id: str, body: InviteRequest, request: Request):
    service, session = workflow(request)
    result = service.invite(session, organization_id, body.email, body.role)
    return InviteIssued(id=result.id, credential=result.credential)


@router.delete('/api/v1/workspaces/{organization_id}/invitations/{invitation_id}', status_code=204, operation_id='revoke_invitation', deprecated=True)
def revoke_invitation(organization_id: str, invitation_id: str, request: Request):
    service, session = workflow(request)
    service.revoke_invitation(session, organization_id, invitation_id)
    return Response(status_code=204)


@router.post('/api/v1/invitations/respond', response_model=InvitationOutcome, operation_id='respond_invitation',
             responses={410: {'model': ErrorEnvelope, 'description': 'Invitation expired'}})
def respond_invitation(body: InvitationResponse, request: Request):
    service, session = workflow(request)
    return service.respond(session, body.credential.get_secret_value(), body.accept)


@router.get('/api/v1/beta/status', response_model=BetaStatus, operation_id='beta_status')
def beta_status(request: Request):
    _, session = workflow(request)
    return request.app.state.runtime.beta.status(session.user_id, verified=bool(session.verified_email))


@router.post('/api/v1/beta/operator', operation_id='beta_operator')
def beta_operator(body: BetaOperatorCommand, request: Request):
    workflow(request)
    runtime = request.app.state.runtime
    return runtime.beta.operate(runtime.identity.sessions,
        request.cookies.get(runtime.identity.session_cookie, ''), body.model_dump())
