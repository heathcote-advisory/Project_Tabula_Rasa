"""Owner-safe status and explicit activation/disconnection; never token DTOs."""
from dataclasses import asdict
from fastapi import APIRouter, Request
from ..errors import ERROR_RESPONSES, TransportError
from ..models.connections import ConnectionAuthorization, ConnectionChange, ConnectionInfo, ConnectionStart, DriveSetupCommand, DriveSetupResult
from .services import Context

router = APIRouter(prefix='/api/v1/entities/{entity_id}/connections', tags=['Connections'], responses=ERROR_RESPONSES)
callback_router = APIRouter(tags=['Connections'], responses=ERROR_RESPONSES)


def manager(request):
    runtime = request.app.state.runtime
    if runtime.identity is None or runtime.connections is None:
        raise TransportError('unavailable')
    return runtime.connections


@router.get('/google-drive', response_model=ConnectionInfo, operation_id='connection_status')
def connection_status(entity_id: str, request: Request, context: Context):
    connections = manager(request)
    result = (connections.oauth.status(context, request.app.state.runtime.identity.authenticated(request)) if connections.oauth else
              asdict(connections.repository.status(context)))
    return ConnectionInfo(**result)


@router.post('/google-drive/authorize', response_model=ConnectionAuthorization, operation_id='authorize_drive')
def authorize_drive(entity_id: str, body: ConnectionStart, request: Request, context: Context):
    connections = manager(request)
    if connections.oauth is None:
        raise TransportError('unavailable')
    session = request.app.state.runtime.identity.authenticated(request)
    return ConnectionAuthorization(authorization_url=connections.oauth.begin(context, session, body.revision))


@router.post('/google-drive/setup', response_model=DriveSetupResult, operation_id='setup_drive')
def setup_drive(entity_id: str, body: DriveSetupCommand, request: Request, context: Context):
    connections = manager(request)
    if connections.oauth is None:
        raise TransportError('unavailable')
    auth = request.app.state.runtime.identity
    return DriveSetupResult(**connections.oauth.setup(context, auth.authenticated(request), body,
        reauthenticate=lambda: auth.sessions.validate(request.cookies.get(auth.session_cookie, ''))))


@callback_router.get('/api/v1/connections/google-drive/callback', operation_id='complete_drive_authorization')
def complete_drive_authorization(request: Request):
    from starlette.responses import RedirectResponse
    from tabula_rasa.application.authorization import authorize_action
    from tabula_rasa.application.identity import IdentityError
    auth = request.app.state.runtime.identity
    context = auth(request)
    authorize_action(context.capabilities, 'connections_manage')
    connections = manager(request)
    if connections.oauth is None:
        raise TransportError('unavailable')
    session = auth.authenticated(request)
    try:
        success = connections.oauth.complete(context, session, request.query_params.get('state', ''),
            request.query_params.get('code', ''), denied='error' in request.query_params,
            reauthenticate=lambda: auth.sessions.validate(request.cookies.get(auth.session_cookie, '')))
    except IdentityError:
        raise
    except Exception:
        # The persisted attempt remains failed; never reflect provider messages/codes.
        # Invalid state/authorization still receives a denial through the service boundary.
        response = RedirectResponse('/data?drive=failed', status_code=303)
    else:
        response = RedirectResponse('/data' if success else '/data?drive=failed', status_code=303)
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@router.post('/google-drive', response_model=ConnectionInfo, operation_id='change_connection')
def change_connection(entity_id: str, body: ConnectionChange, request: Request, context: Context):
    result = manager(request).change(context, request.app.state.runtime.identity.authenticated(request),
        revision=body.revision, connect=body.connect)
    return ConnectionInfo(**asdict(result))
