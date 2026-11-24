"""Adversarial action and route inventory checks before any private binding."""

from dataclasses import replace
from types import MappingProxyType
from unittest.mock import Mock

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
import pytest

from tabula_rasa.application.authorization import ACTION_CAPABILITIES, authorize_action
from tabula_rasa.application.identity import AuthenticatedSession, EntityAccess, IdentityError, capabilities
from tabula_rasa.http.app import create_app
from tabula_rasa.http.authorization import ROUTE_POLICIES, RoutePolicy, validate_routes
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.context import DenyContext, TrustedDevelopmentContext
from tabula_rasa.http.identity import NamedIdentity
from tests.http.test_transport import FakeRuntime, SETTINGS


def entity_routes():
    return tuple(route for route in iter_route_contexts(create_app(schema_only=True).routes)
                 if isinstance(route.original_route, APIRoute) and '{entity_id}' in route.path)


ROUTES = entity_routes()


def test_synthetic_development_import_grant_remains_inside_the_existing_boundary():
    resolver = TrustedDevelopmentContext('alpha', 'test-csrf')
    authorize_action(resolver.context.capabilities, 'import')
    runtime = FakeRuntime()
    runtime.resolve_context = resolver
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda _: runtime)) as client:
        result = client.post('/api/v1/entities/alpha/imports/csv/prepare',
            headers={'Origin': SETTINGS.public_origin, 'X-CSRF-Token': 'test-csrf',
                     'Content-Type': 'application/octet-stream'}, content=b'Date,Type')
    assert result.status_code == 403  # TestClient is not a permitted loopback caller.
    runtime.services.assert_not_called()


def test_inventory_covers_included_routes_and_matches_openapi():
    app = create_app(schema_only=True)
    schema = app.openapi()
    operations = {operation['operationId'] for path, methods in schema['paths'].items()
                  if '{entity_id}' in path for operation in methods.values()}
    assert {route.operation_id for route in ROUTES} == operations
    assert len(operations) > 50


def path_for(route, entity='alpha'):
    path = route.path.replace('{entity_id}', entity)
    for key in ('record_id', 'document_id', 'sequence', 'transition'):
        path = path.replace('{' + key + '}', '1')
    return path


@pytest.mark.parametrize('route', ROUTES, ids=lambda r: r.operation_id)
@pytest.mark.parametrize('denial', ['anonymous', 'foreign', 'no_capabilities'])
def test_every_entity_route_denies_before_binding(route, denial):
    runtime = FakeRuntime()
    original = runtime.resolve_context
    if denial == 'anonymous':
        runtime.resolve_context = DenyContext()
    elif denial == 'no_capabilities':
        runtime.resolve_context = lambda request: replace(original(request), capabilities=frozenset())
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda _: runtime)) as client:
        result = client.request(next(iter(route.methods)), path_for(route, 'beta' if denial == 'foreign' else 'alpha'),
            headers={'Origin': SETTINGS.public_origin, 'X-CSRF-Token': 'test-csrf'}, json={})
    assert result.status_code == (401 if denial == 'anonymous' else 403), result.text
    runtime.services.assert_not_called()


@pytest.mark.parametrize('route', [r for r in ROUTES if ROUTE_POLICIES[r.operation_id].action in {
    'write', 'asset_depreciate', 'import', 'period_close', 'period_unlock', 'entry_correct'}],
                         ids=lambda r: r.operation_id)
def test_every_mutation_denies_read_only_before_binding(route):
    runtime = FakeRuntime()
    original = runtime.resolve_context
    runtime.resolve_context = lambda request: replace(original(request), capabilities=capabilities('read_only'))
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda _: runtime)) as client:
        result = client.request(next(iter(route.methods)), path_for(route),
            headers={'Origin': SETTINGS.public_origin, 'X-CSRF-Token': 'test-csrf'}, json={})
    assert result.status_code == 403, result.text
    runtime.services.assert_not_called()


@pytest.mark.parametrize('route', [r for r in ROUTES if '/imports/' in r.path], ids=lambda r: r.operation_id)
def test_import_preparation_and_commit_deny_editor_before_upload_or_binding(route):
    runtime = FakeRuntime()
    original = runtime.resolve_context
    runtime.resolve_context = lambda request: replace(original(request), capabilities=capabilities('editor'))
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda _: runtime)) as client:
        result = client.request('POST', path_for(route),
            headers={'Origin': SETTINGS.public_origin, 'X-CSRF-Token': 'test-csrf',
                     'Content-Type': 'application/octet-stream'}, content=b'not a valid import')
    assert result.status_code == 403
    runtime.services.assert_not_called()


def test_future_route_without_handler_context_still_denies_and_inventory_rejects_it(tmp_path):
    runtime = FakeRuntime()
    app = create_app(settings=SETTINGS, runtime_factory=lambda _: runtime, static_dir=tmp_path / 'unbuilt')
    bound = Mock()

    @app.get('/api/v1/entities/{entity_id}/future-export', operation_id='future_export')
    def future_export(entity_id: str):
        bound()
        return {}

    with pytest.raises(RuntimeError, match='explicit action'):
        validate_routes(app)
    with TestClient(app) as client:
        assert client.get('/api/v1/entities/alpha/future-export').status_code == 403
    bound.assert_not_called()
    runtime.services.assert_not_called()


@pytest.mark.parametrize('defect', ['duplicate', 'method', 'scope', 'starlette_route'])
def test_inventory_rejects_unsafe_registration(defect):
    app = create_app(schema_only=True)
    def endpoint():
        return {}
    if defect == 'starlette_route':
        app.add_route('/api/v1/internal-admin', endpoint)
    elif defect == 'duplicate':
        app.add_api_route('/api/v1/entities/{entity_id}/another-read', endpoint,
                          methods=['GET'], operation_id='list_accounts')
    elif defect == 'method':
        app.add_api_route('/api/v1/entities/{entity_id}/changed-method', endpoint,
                          methods=['POST'], operation_id='list_accounts')
    else:
        app.add_api_route('/api/v1/entities/{entity_id}/wrong-scope', endpoint,
                          methods=['GET'], operation_id='get_session')
    with pytest.raises(RuntimeError, match='authorization guard'):
        validate_routes(app)


def test_include_prefix_cannot_hide_an_unregistered_private_route(tmp_path):
    runtime = FakeRuntime()
    app = create_app(settings=SETTINGS, runtime_factory=lambda _: runtime, static_dir=tmp_path / 'unbuilt')
    router = APIRouter()
    invoked = Mock(return_value={})
    router.add_api_route('/future-admin', invoked, operation_id='future_admin', methods=['GET'])
    app.include_router(router, prefix='/api/v1')
    with pytest.raises(RuntimeError):
        validate_routes(app)
    with TestClient(app) as client:
        assert client.get('/api/v1/future-admin').status_code == 403
    invoked.assert_not_called()


def test_named_identity_guard_validates_once_per_request_and_future_handlers_cannot_omit_it(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    settings = HttpSettings('test', 'http://testserver', access_mode='named_user')
    sessions = Mock()
    sessions.validate.return_value = AuthenticatedSession('actor', 'cache', 'test-csrf', now,
        now + timedelta(hours=12), EntityAccess('alpha', 'org', 'Alpha', 'read_only', 1))
    auth = NamedIdentity(settings, sessions, Mock())
    runtime = FakeRuntime()
    runtime.identity = runtime.resolve_context = auth
    monkeypatch.setattr('tabula_rasa.http.authorization.ROUTE_POLICIES', MappingProxyType({
        **ROUTE_POLICIES, 'test_identity_action': RoutePolicy('POST', 'session', 'identity'),
    }))
    app = create_app(settings=settings, runtime_factory=lambda _: runtime, static_dir=tmp_path / 'unbuilt')
    invoked = Mock(return_value={})

    @app.post('/api/v1/session/future-action', operation_id='test_identity_action')
    def future_identity_action():
        return invoked()

    validate_routes(app)
    with TestClient(app) as client:
        assert client.get('/api/v1/session').status_code == 200
        assert sessions.validate.call_count == 1
        assert client.get('/api/v1/entities/alpha/accounts').status_code == 200
        assert sessions.validate.call_count == 2
        assert client.post('/api/v1/session/future-action').status_code == 403
        invoked.assert_not_called()
        assert client.post('/api/v1/session/future-action',
            headers={'Origin': settings.public_origin, 'X-CSRF-Token': 'test-csrf'}).status_code == 200
        invoked.assert_called_once()
        sessions.validate.side_effect = IdentityError('unauthorized')
        assert client.post('/api/v1/session/future-action',
            headers={'Origin': settings.public_origin, 'X-CSRF-Token': 'test-csrf'}).status_code == 401
        invoked.assert_called_once()


@pytest.mark.parametrize('role,export_status,import_status', [
    ('owner', 200, 200), ('admin', 200, 200), ('accountant', 200, 200),
    ('editor', 403, 403), ('read_only', 403, 403),
])
def test_bulk_adapter_requires_distinct_handoff_capability_even_without_context_dependency(monkeypatch, role, export_status, import_status, tmp_path):
    # Test adapter only: no new public bulk/recovery endpoint is shipped.
    runtime = FakeRuntime()
    original = runtime.resolve_context
    runtime.resolve_context = lambda request: replace(original(request), capabilities=capabilities(role))
    monkeypatch.setattr('tabula_rasa.http.authorization.ROUTE_POLICIES', MappingProxyType({
        **ROUTE_POLICIES, 'test_export': RoutePolicy('GET', 'export'), 'test_import': RoutePolicy('POST', 'import'),
    }))
    app = create_app(settings=SETTINGS, runtime_factory=lambda _: runtime, static_dir=tmp_path / 'unbuilt')
    bound = Mock(return_value={})

    @app.get('/api/v1/entities/{entity_id}/export', operation_id='test_export')
    def export(entity_id: str, request: Request):
        return bound(request.state.authorized_context)

    @app.post('/api/v1/entities/{entity_id}/import', operation_id='test_import')
    def import_(entity_id: str, request: Request):
        return bound(request.state.authorized_context)

    validate_routes(app)
    with TestClient(app) as client:
        assert client.get('/api/v1/entities/alpha/export').status_code == export_status
        assert client.post('/api/v1/entities/alpha/import',
            headers={'Origin': SETTINGS.public_origin, 'X-CSRF-Token': 'test-csrf'}).status_code == import_status
        assert client.get('/api/v1/entities/beta/export').status_code == 403
        assert client.post('/api/v1/entities/alpha/import').status_code == 403
    assert bound.call_count == (2 if export_status == 200 else 0)


@pytest.mark.parametrize('role', ['owner', 'admin', 'editor', 'accountant', 'read_only', 'unknown'])
def test_privileged_policy_never_confers_operator_or_local_administration(role):
    granted = capabilities(role)
    for action in ('restore', 'migration', 'cutover', 'ledger_reset', 'archive_recover', 'audit_inspect', 'legacy_admin'):
        with pytest.raises(IdentityError, match='forbidden'):
            authorize_action(granted, action)
    for action in ('backup_create', 'backup_list', 'backup_download', 'entity_manage',
                   'organization_manage', 'connections_manage', 'ownership_manage', 'business_admin'):
        if role == 'owner':
            authorize_action(granted, action)
        else:
            with pytest.raises(IdentityError, match='forbidden'):
                authorize_action(granted, action)


def test_recovery_requires_both_independent_execution_and_approval_authority():
    owner = capabilities('owner')
    for extra in (frozenset({'platform:operate'}), frozenset({'recovery:approved'})):
        with pytest.raises(IdentityError):
            authorize_action(owner | extra, 'restore')


@pytest.mark.parametrize('role', ['owner', 'admin', 'editor', 'accountant', 'read_only'])
def test_action_matrix_preserves_read_write_and_membership_distinctions(role):
    granted = capabilities(role)
    authorize_action(granted, 'read')
    for action, allowed in [('write', role != 'read_only'), ('members_manage', role in {'owner', 'admin'})]:
        if allowed:
            authorize_action(granted, action)
        else:
            with pytest.raises(IdentityError):
                authorize_action(granted, action)
    with pytest.raises(IdentityError):
        authorize_action(granted, 'unknown')
    with pytest.raises(IdentityError):
        authorize_action(None, 'read')
    assert set(policy.action for policy in ROUTE_POLICIES.values() if policy.scope == 'entity') <= ACTION_CAPABILITIES.keys()


@pytest.mark.parametrize('mode,environment,origin', [
    ('named_user', 'test', 'http://testserver'), ('named_user', 'production', 'https://example.test'),
])
def test_plausible_context_and_client_role_claims_cannot_replace_named_membership(mode, environment, origin):
    runtime = FakeRuntime()  # trusted_development=False still cannot bypass membership.
    settings = HttpSettings(environment, origin, access_mode=mode)
    with TestClient(create_app(settings=settings, runtime_factory=lambda _: runtime), base_url=origin) as client:
        client.cookies.set('tabula_entity', 'alpha')
        for path in ('/api/v1/session', '/api/v1/entities', '/api/v1/entities/alpha/accounts'):
            result = client.get(path, headers={'X-Role': 'owner', 'X-Organization-ID': 'organization',
                                             'Authorization': 'Bearer fabricated'})
            assert result.status_code == 403
    runtime.services.assert_not_called()


def test_denial_signal_excludes_selectors_and_raw_claims(caplog):
    import json
    runtime = FakeRuntime()
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda _: runtime)) as client, \
            caplog.at_level('INFO', logger='tabula_rasa.http'):
        result = client.get('/api/v1/entities/private-foreign-business/documents/private-receipt/content',
                            headers={'X-Role': 'PRIVATE-role-claim'})
    assert result.status_code == 403
    event = json.loads(caplog.records[-1].message)
    assert event['security_event'] == 'authorization_denied'
    assert event['failure_code'] == 'forbidden'
    assert 'private-foreign-business' not in caplog.text and 'PRIVATE-role-claim' not in caplog.text
