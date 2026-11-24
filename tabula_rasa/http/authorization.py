"""Explicit registered HTTP actions; future private routes deny by default."""

from dataclasses import dataclass
from types import MappingProxyType

from fastapi import Request
from fastapi.routing import APIRoute, iter_route_contexts

from tabula_rasa.application.authorization import ACTION_CAPABILITIES

from .errors import TransportError


@dataclass(frozen=True)
class RoutePolicy:
    method: str
    action: str
    scope: str = 'entity'


# IDs are server route metadata, never request claims. Identity handlers share
# the session guard; logout retains its documented outage-clearing path.
ROUTE_POLICIES = MappingProxyType({
    'policy_status': RoutePolicy('GET', 'session', 'identity'),
    'accept_policies': RoutePolicy('POST', 'session', 'identity'),
    'customer_lifecycle_status': RoutePolicy('GET', 'session', 'identity'),
    'request_customer_lifecycle': RoutePolicy('POST', 'session', 'identity'),
    'change_customer_lifecycle': RoutePolicy('POST', 'session', 'identity'),
    'download_customer_export': RoutePolicy('POST', 'export'),
    'support_diagnostics': RoutePolicy('POST', 'diagnostics', 'support'),
    'support_audit': RoutePolicy('POST', 'audit', 'support'),
    'support_administer': RoutePolicy('POST', 'administration', 'support'),
    'list_accounting_periods': RoutePolicy('GET', 'read'),
    'close_accounting_period': RoutePolicy('POST', 'period_close'),
    'unlock_accounting_period': RoutePolicy('POST', 'period_unlock'),
    'reverse_accounting_entry': RoutePolicy('POST', 'entry_correct'),
    'connection_status': RoutePolicy('GET', 'connections_manage'),
    'maps_quota_status': RoutePolicy('GET', 'read'),
    'load_maps_quota': RoutePolicy('POST', 'read'),
    'change_connection': RoutePolicy('POST', 'connections_manage'),
    'setup_drive': RoutePolicy('POST', 'connections_manage'),
    'authorize_drive': RoutePolicy('POST', 'connections_manage'),
    'complete_drive_authorization': RoutePolicy('GET', 'session', 'identity'),
    **{name: RoutePolicy('GET', 'read') for name in (
        'list_vehicles', 'list_accounts', 'list_categories', 'list_payees', 'list_tags',
        'list_transactions', 'list_journals', 'list_mileage', 'get_mileage',
        'unreferenced_documents', 'download_document', 'transaction_documents',
        'query_ledger', 'report_summary', 'list_mixed_use', 'get_mixed_use',
        'list_recurring', 'get_recurring', 'asset_types', 'list_assets', 'get_asset',
        'download_transactions_csv', 'download_journal_postings_csv',
        'download_selected_ledger_csv', 'mixed_use_documents', 'mileage_maps',
    )},
    **{name: RoutePolicy('POST', 'write') for name in (
        'create_vehicle', 'create_account', 'deactivate_account', 'create_category', 'create_payee',
        'create_tag', 'merge_payees', 'merge_tags', 'create_transaction', 'create_journal',
        'create_mileage', 'link_document', 'create_mixed_use', 'create_recurring',
        'reconcile_recurring', 'transition_recurring', 'create_asset',
        'link_mixed_use_document', 'calculate_mileage_route',
        'bulk_update_transactions', 'bulk_delete_transactions', 'convert_expense_to_recurring',
    )},
    **{name: RoutePolicy('PATCH', 'write') for name in (
        'update_vehicle', 'update_account', 'update_category', 'update_payee', 'update_tag',
        'update_transaction', 'update_journal', 'update_mileage', 'update_mixed_use',
        'update_recurring', 'update_asset',
    )},
    **{name: RoutePolicy('DELETE', 'write') for name in (
        'remove_vehicle', 'delete_category', 'remove_payee', 'remove_tag', 'delete_transaction',
        'delete_journal', 'delete_mileage', 'unlink_document', 'delete_mixed_use', 'delete_recurring',
        'delete_asset',
        'unlink_mixed_use_document',
    )},
    'upload_document': RoutePolicy('PUT', 'write'),
    'post_depreciation': RoutePolicy('POST', 'asset_depreciate'),
    'override_depreciation': RoutePolicy('PUT', 'asset_depreciate'),
    'remove_depreciation_override': RoutePolicy('DELETE', 'asset_depreciate'),
    'prepare_selected_ledger_csv': RoutePolicy('POST', 'read'),
    'preview_allocation': RoutePolicy('POST', 'read'),
    'prepare_transaction_copy': RoutePolicy('POST', 'read'),
    'copy_mixed_use_draft': RoutePolicy('POST', 'read'),
    **{name: RoutePolicy('POST', 'import') for name in (
        'prepare_transaction_csv', 'preview_quickbooks_transactions', 'preview_quickbooks_mileage',
        'import_transaction_csv', 'import_quickbooks_transactions', 'import_quickbooks_mileage',
    )},
    'get_session': RoutePolicy('GET', 'read', 'identity'),
    'list_entities': RoutePolicy('GET', 'read', 'identity'),
    'list_businesses': RoutePolicy('GET', 'read', 'identity'),
    **{name: RoutePolicy('GET', 'session', 'identity') for name in (
        'beta_status', 'bootstrap_status', 'business_creation_status', 'list_members', 'list_invitations',
        'business_members', 'business_invitations',
    )},
    **{name: RoutePolicy('POST', 'session', 'identity') for name in (
        'beta_operator', 'create_business', 'bootstrap_workspace', 'bootstrap_business', 'create_invitation', 'respond_invitation', 'grant_ownership',
        'business_invite', 'business_grant_ownership', 'preview_invitation',
    )},
    'change_member': RoutePolicy('PATCH', 'session', 'identity'),
    'business_change_member': RoutePolicy('PATCH', 'session', 'identity'),
    'business_settings': RoutePolicy('GET', 'session', 'identity'),
    'business_change_currency': RoutePolicy('PATCH', 'session', 'identity'),
    'revoke_invitation': RoutePolicy('DELETE', 'session', 'identity'),
    'business_revoke_invitation': RoutePolicy('DELETE', 'session', 'identity'),
    'select_entity': RoutePolicy('POST', 'read', 'identity'),
    'session_activity': RoutePolicy('POST', 'session', 'identity'),
    'revoke_all_sessions': RoutePolicy('POST', 'session', 'identity'),
    'logout': RoutePolicy('POST', 'logout', 'identity'),
    'logout_context': RoutePolicy('GET', 'logout', 'identity'),
    'begin_login': RoutePolicy('GET', 'login', 'public'),
    'begin_registration': RoutePolicy('GET', 'login', 'public'),
    'complete_login': RoutePolicy('GET', 'callback', 'public'),
})


# Only these exact application routes serve public, non-private content.
# A new path outside /api must not silently bypass the central dependency.
PUBLIC_ROUTES = MappingProxyType({
    'public_policies': ('/api/v1/public/policies', frozenset({'GET'})),
    'liveness': ('/health/live', frozenset({'GET'})),
    'readiness': ('/health/ready', frozenset({'GET'})),
    'version': ('/version', frozenset({'GET'})),
    'asset_register': ('/assets', frozenset({'GET', 'HEAD'})),
    'browser_shell': ('/{path:path}', frozenset({'GET', 'HEAD'})),
})


def public_route(route):
    return PUBLIC_ROUTES.get(getattr(route, 'operation_id', None)) == (
        getattr(route, 'path', None), frozenset(getattr(route, 'methods', ())),
    )


def route_policy(request):
    route = request.scope.get('route')
    policy = ROUTE_POLICIES.get(getattr(route, 'operation_id', None))
    entity_route = 'entity_id' in request.path_params
    if (policy is None or request.method != policy.method
            or (policy.scope == 'entity') != entity_route):
        request.state.security_event = 'authorization_denied'
        request.state.security_reason = 'policy'
        raise TransportError('forbidden')
    return policy


def authorize_request(request: Request):
    """App-wide dependency executes before every private handler's binding."""
    route = request.scope.get('route')
    if getattr(route, 'operation_id', None) == 'browser_shell' and request.url.path.split('/')[1] == 'api':
        # The public SPA catch-all must never serve or bind a reserved API URL.
        raise TransportError('not_found')
    if public_route(route):
        return
    from .context import require_named_resolver
    require_named_resolver(request)
    policy = route_policy(request)
    if policy.scope == 'support':
        from .context import require_csrf
        from .identity import NamedIdentity
        identity = getattr(request.app.state.runtime, 'identity', None)
        if not isinstance(identity, NamedIdentity) or identity is not request.app.state.runtime.resolve_context:
            request.state.security_event = 'privileged_denied'
            request.state.security_reason = 'capability'
            raise TransportError('forbidden')
        session = identity.authenticated(request)
        request.state.security_event = 'privileged_denied'
        request.state.security_reason = 'capability'
        require_csrf(request, session.csrf_token)
    elif policy.scope == 'entity':
        from .context import resolve_context
        resolve_context(request)
    elif policy.scope == 'identity' and policy.action != 'logout':
        from .context import require_csrf, resolve_context
        identity = getattr(request.app.state.runtime, 'identity', None)
        if identity is None:
            resolve_context(request)  # Existing restricted-alpha session/selector.
        else:
            session = identity.authenticated(request)
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                require_csrf(request, session.csrf_token)
            if (request.app.state.customer_policies.beta and request.method == 'POST'
                    and policy.action == 'session' and getattr(route, 'operation_id', '') in {
                        'create_business', 'bootstrap_workspace', 'bootstrap_business'}):
                from .routes.customer_policies import require_acknowledgement
                require_acknowledgement(request, session)


def validate_routes(app):
    """Catch missing actions, guards, duplicate IDs and mismatched scopes at build."""
    seen = set()
    for route in iter_route_contexts(app.routes):
        from starlette.routing import Mount, Route
        from .static import HashedAssets
        original = route.original_route
        if isinstance(original, Mount) and route.path == '/assets' and isinstance(original.app, HashedAssets):
            continue  # Hashed build assets only; arbitrary mounted apps fail closed.
        framework_endpoint = getattr(app.state, 'framework_documentation_endpoints', {}).get(route.path)
        if (type(original) is Route and framework_endpoint is not None
                and original.endpoint is framework_endpoint and original.methods == {'GET', 'HEAD'}):
            continue  # Existing middleware protects framework documentation.
        if not isinstance(route.original_route, APIRoute):
            raise RuntimeError('Private API routes must use the shared FastAPI authorization guard.')
        if public_route(route):
            if route.operation_id in seen:
                raise RuntimeError('Duplicate public authorization guard exemption.')
            seen.add(route.operation_id)
            continue
        policy = ROUTE_POLICIES.get(route.operation_id)
        allowed_actions = {'entity': ACTION_CAPABILITIES, 'identity': {'read', 'session', 'logout'},
                           'public': {'login', 'callback'},
                           'support': {'diagnostics', 'audit', 'administration'}}
        if (policy is None or policy.action not in allowed_actions.get(policy.scope, ())
                or route.operation_id in seen or route.methods != {policy.method}
                or (policy.scope == 'entity') != ('{entity_id}' in route.path)
                or not any(item.call is authorize_request for item in route.dependant.dependencies)):
            raise RuntimeError('Private API route requires an explicit action and authorization guard.')
        seen.add(route.operation_id)
