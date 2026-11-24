"""Server-resolved request context; path/body/header IDs never grant access."""

from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Protocol

from fastapi import Request

from tabula_rasa.application.authorization import authorize_action
from tabula_rasa.application.identity import IdentityError

from .errors import TransportError


@dataclass(frozen=True)
class RequestContext:
    actor_id: str
    organization_id: str
    entity_id: str
    capabilities: frozenset[str]
    cache_scope: str
    csrf_token: str = field(repr=False)
    trusted_development: bool = False


class ContextResolver(Protocol):
    def __call__(self, request: Request) -> RequestContext: ...


class DenyContext:
    def __call__(self, request):
        raise TransportError("unauthorized")


class TrustedDevelopmentContext:
    """Fixed synthetic scope; require loopback locally or a gated hosted request."""

    def __init__(self, entity_id, csrf_token, *, entity_ids=(), hosted=False):
        self.hosted = hosted
        self.context = RequestContext(
            "synthetic-operator", "synthetic", entity_id,
            frozenset({"bookkeeping:read", "bookkeeping:write", "bookkeeping:import", "asset:depreciate"}),
            "development:" + entity_id, csrf_token, True,
        )
        from dataclasses import replace
        self.contexts = tuple(replace(self.context, entity_id=value, cache_scope='development:' + value)
                              for value in dict.fromkeys((entity_id, *entity_ids)))

    def __call__(self, request):
        if self.hosted:
            if not getattr(request.state, "alpha_authenticated", False):
                raise TransportError("unauthorized")
        elif request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise TransportError("forbidden")
        selected = request.cookies.get('tabula_entity', self.context.entity_id)
        return next((item for item in self.contexts if item.entity_id == selected), None) or self._forbidden()

    @staticmethod
    def _forbidden():
        raise TransportError('forbidden')

    def available_contexts(self, request):
        self(request)  # Enforce the same local/hosted boundary for enumeration.
        return self.contexts


def require_named_resolver(request):
    runtime = request.app.state.runtime
    settings = request.app.state.settings
    # Commercial composition cannot replace membership resolution with a lambda
    # returning a plausible non-synthetic context.
    if settings.environment == 'production' or settings.access_mode == 'named_user':
        from .identity import NamedIdentity
        if (not isinstance(runtime.resolve_context, NamedIdentity)
                or getattr(runtime, 'identity', None) is not runtime.resolve_context
                or runtime.identity.settings != settings):
            request.state.security_event = 'authorization_denied'
            raise TransportError('forbidden')


def resolve_context(request: Request) -> RequestContext:
    require_named_resolver(request)
    runtime = request.app.state.runtime
    settings = request.app.state.settings
    context = getattr(request.state, 'authorized_context', None)
    if context is None:
        context = runtime.resolve_context(request)
    if not isinstance(context, RequestContext):
        raise TransportError("unauthorized")
    if context.trusted_development and (settings.environment == "production" or settings.access_mode == "named_user"):
        raise TransportError("forbidden")
    request.state.telemetry_context = context
    requested = request.path_params.get("entity_id")
    if requested is not None and requested != context.entity_id:
        request.state.security_event = 'authorization_denied'
        request.state.security_reason = 'cross_entity'
        raise TransportError("forbidden")
    unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
    from .authorization import route_policy
    # Middleware preflight and API documentation have no matched APIRoute yet;
    # they may inspect context, but the handler guard must still check its action.
    action = route_policy(request).action if request.scope.get('route') is not None else 'read'
    try:
        authorize_action(context.capabilities, action)
    except IdentityError:
        request.state.security_reason = 'capability'
        request.state.security_event = 'authorization_denied'
        raise TransportError('forbidden') from None
    if unsafe:
        require_csrf(request, context.csrf_token)
    request.state.authorized_context = context
    return context


def require_csrf(request, expected):
    csrf = request.headers.get("x-csrf-token", "")
    if (request.headers.get("origin", "") != request.app.state.settings.public_origin
            or not csrf or not expected or not compare_digest(csrf.encode(), expected.encode())):
        request.state.security_event = 'authorization_denied'
        request.state.security_reason = 'csrf'
        raise TransportError("forbidden")
