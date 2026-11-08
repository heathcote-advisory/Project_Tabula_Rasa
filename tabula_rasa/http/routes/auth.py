from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse, Response

from tabula_rasa.application.identity import (
    IdentityError, LOGIN_FAILURE_REASONS, LOGIN_TRANSACTION_SECONDS, REGISTRATION_TRANSACTION_SECONDS,
)
from ..context import require_csrf
from ..errors import ERROR_RESPONSES, TransportError, error_response, mark_identity_storage_failure
from ..models.common import LogoutContext

router = APIRouter(responses=ERROR_RESPONSES, tags=["Authentication"])


def login_failure(request, error, reason):
    mark_identity_storage_failure(request, error)
    # Only server-owned classifications and a fresh request reference reach the
    # browser. Never reflect provider errors, descriptions or callback values.
    reason = error.login_reason or reason
    if reason not in LOGIN_FAILURE_REASONS:
        reason = 'token_validation'
    outcome = 'unavailable' if error.code == 'unavailable' else 'login_failed'
    request.state.failure_code = error.code
    request.state.security_reason = reason
    request.state.security_event = 'login_failed'
    return RedirectResponse('/signed-out?reason=' + outcome + '&category=' + reason
                            + '&reference=' + request.state.request_id, status_code=303)


def identity(request):
    result = getattr(request.app.state.runtime, "identity", None)
    if result is None:
        raise TransportError("forbidden")
    return result


@router.get("/api/v1/auth/login", operation_id="begin_login")
def login(request: Request, return_path: str = "/"):
    return begin_authentication(request, return_path)


@router.get("/api/v1/auth/register", operation_id="begin_registration")
def register(request: Request, return_path: str = "/invitations"):
    return begin_authentication(request, return_path, registration=True)


def begin_authentication(request, return_path, *, registration=False):
    auth = identity(request)
    request.state.login_request_id = request.state.request_id
    try:
        lifetime = REGISTRATION_TRANSACTION_SECONDS if registration else LOGIN_TRANSACTION_SECONDS
        raw, transaction = auth.sessions.begin_login(return_path, request.cookies.get(auth.login_cookie), lifetime=lifetime)
        response = RedirectResponse(auth.provider.authorization_url(transaction, registration=registration), status_code=303)
        auth.set_cookie(response, auth.login_cookie, raw, lifetime)
        auth.set_cookie(response, auth.login_reference_cookie, request.state.request_id, lifetime)
        request.state.security_event = "login_started"
    except IdentityError as error:
        if error.code == 'forbidden':
            raise  # Preserve return-path validation; never broaden redirects.
        response = login_failure(request, error, 'login_start')
        auth.clear(response, login_only=True)
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@router.get("/api/v1/auth/callback", operation_id="complete_login")
def callback(request: Request):
    auth = identity(request)
    request.state.login_request_id = request.cookies.get(auth.login_reference_cookie)
    reason = 'login_transaction'
    # Consume even provider-denied/invalid callbacks. Exchange failures cannot be replayed.
    try:
        transaction = auth.sessions.consume_login(request.cookies.get(auth.login_cookie, ""),
                                                 request.query_params.get("state", ""))
        if "error" in request.query_params:
            raise IdentityError("unauthorized", login_reason='provider_authorization')
        reason = 'token_validation'
        verified = auth.provider.verify(request.query_params.get("code", ""), transaction)
        reason = 'session_issue'
        raw, session = auth.sessions.issue(verified, request.cookies.get(auth.session_cookie))
        response = RedirectResponse(transaction.return_path, status_code=303)
        auth.set_cookie(response, auth.session_cookie, raw, 43200)
        auth.set_cookie(response, auth.csrf_cookie, session.csrf_token, 43200)
        request.state.named_session = session
        request.state.security_event = "login_succeeded"
    except IdentityError as error:
        response = login_failure(request, error, reason)
    auth.clear(response, login_only=True)
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@router.get("/api/v1/auth/logout-context", response_model=LogoutContext, operation_id="logout_context")
def logout_context(request: Request):
    auth = identity(request)
    # A same-origin read of the logout-only cookie, not an authenticated session.
    # The custom header requires preflight cross-origin; this API grants no CORS.
    if (request.headers.get("x-tabula-logout") != "1"
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
            or request.headers.get("origin", auth.settings.public_origin) != auth.settings.public_origin):
        raise TransportError("forbidden")
    token = request.cookies.get(auth.csrf_cookie, "")
    if not token:
        raise TransportError("unauthorized")
    return LogoutContext(csrf_token=token)


@router.post("/api/v1/auth/logout", status_code=204, operation_id="logout")
def logout(request: Request):
    auth = identity(request)
    # Host-only HttpOnly synchronizer copy permits local clearing during DB outage;
    # it grants no entity authority and does not claim server revocation succeeded.
    require_csrf(request, request.cookies.get(auth.csrf_cookie, ""))
    try:
        auth.sessions.revoke(request.cookies.get(auth.session_cookie, ""))
        response = Response(status_code=204)
        request.state.security_event = "logout_succeeded"
    except IdentityError as error:
        mark_identity_storage_failure(request, error)
        response = Response(status_code=204) if error.code == "unauthorized" else error_response(request, error.code)
        request.state.security_event = "logout_local" if error.code == "unauthorized" else "logout_failed"
    auth.clear(response)
    return response


@router.post("/api/v1/auth/activity", status_code=204, operation_id="session_activity")
def activity(request: Request):
    auth = identity(request)
    session = auth.authenticated(request)
    require_csrf(request, session.csrf_token)
    auth.authenticated(request, foreground=True)
    return Response(status_code=204)


@router.post("/api/v1/auth/revoke-all", status_code=204, operation_id="revoke_all_sessions")
def revoke_all(request: Request):
    auth = identity(request)
    session = auth.authenticated(request)
    require_csrf(request, session.csrf_token)
    auth.sessions.identity.revoke_user_sessions(session.user_id)
    response = Response(status_code=204)
    auth.clear(response)
    request.state.security_event = "user_sessions_revoked"
    return response
