"""Route/schema security audit; every private surface must join the action guard."""

from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
import pytest

from tabula_rasa.http.app import create_app
from tabula_rasa.http.authorization import ROUTE_POLICIES, public_route, validate_routes
from tabula_rasa.http.config import HttpSettings
from tests.http.test_transport import FakeRuntime, SETTINGS


def test_complete_private_transport_inventory_matches_policy():
    app = create_app(schema_only=True)
    routes = [r for r in iter_route_contexts(app.routes)
              if isinstance(r.original_route, APIRoute) and r.path.startswith('/api/') and not public_route(r)]
    schema = app.openapi()
    operations = {value['operationId'] for methods in schema['paths'].values()
                  for method, value in methods.items() if method in {'get', 'post', 'put', 'patch', 'delete'}}
    assert {r.operation_id for r in routes} == set(ROUTE_POLICIES)
    assert operations == set(ROUTE_POLICIES) | {'liveness', 'readiness', 'version', 'public_policies'}
    validate_routes(app)
    # Schema-only generation must audit dependencies without a DB or secrets.
    assert not hasattr(app.state, 'runtime')


@pytest.mark.parametrize('path', ['/internal-admin', '/api', '/api/v2/recovery', '/health/private'])
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_unregistered_route_denies_even_outside_api_prefix(path, method, tmp_path):
    runtime = FakeRuntime()
    app = create_app(settings=SETTINGS, runtime_factory=lambda _: runtime, static_dir=tmp_path / 'unbuilt')
    invoked = Mock(return_value={})
    app.add_api_route(path, invoked, methods=[method], operation_id='unregistered_private')
    with pytest.raises(RuntimeError, match='authorization guard'):
        validate_routes(app)
    with TestClient(app) as client:
        assert client.request(method, path).status_code == 403
    invoked.assert_not_called()
    runtime.services.assert_not_called()


@pytest.mark.parametrize('path', ['/internal', '/api/docs', '/api/openapi.json'])
def test_private_subapplication_and_missing_central_dependency_fail_audit(path):
    app = create_app(schema_only=True)
    app.mount(path, FastAPI())
    with pytest.raises(RuntimeError, match='authorization guard'):
        validate_routes(app)
    app = FastAPI()
    app.add_api_route('/api/v1/entities/{entity_id}/documents/{document_id}/content',
                      lambda: {}, operation_id='download_document', methods=['GET'])
    with pytest.raises(RuntimeError, match='authorization guard'):
        validate_routes(app)


@pytest.mark.parametrize('path', ['/api/docs', '/api/openapi.json'])
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_arbitrary_http_handler_at_framework_docs_path_fails_audit(path, method):
    app = create_app(schema_only=True)
    app.add_route(path, lambda request: {}, methods=[method])
    with pytest.raises(RuntimeError, match='authorization guard'):
        validate_routes(app)


@pytest.mark.parametrize('path', ['/api/docs', '/api/openapi.json'])
def test_replaced_framework_docs_endpoint_fails_audit(path):
    app = create_app(schema_only=True)
    route = next(route for route in app.routes if route.path == path)
    route.endpoint = lambda request: {}
    with pytest.raises(RuntimeError, match='authorization guard'):
        validate_routes(app)


@pytest.mark.parametrize('mode', [None, 'restricted_alpha', 'typo'])
def test_production_environment_requires_explicit_named_mode(monkeypatch, mode):
    monkeypatch.setenv('TABULA_RASA_ENVIRONMENT', 'production')
    monkeypatch.setenv('TABULA_RASA_PUBLIC_ORIGIN', 'https://production.example.test')
    for key in ('TRUSTED_ENTITY_ID', 'ALPHA_USERNAME', 'ALPHA_PASSWORD_HASH', 'ACCESS_MODE'):
        monkeypatch.delenv('TABULA_RASA_' + key, raising=False)
    if mode is not None:
        monkeypatch.setenv('TABULA_RASA_ACCESS_MODE', mode)
    with pytest.raises(ValueError):
        HttpSettings.from_environment()
    monkeypatch.setenv('TABULA_RASA_ACCESS_MODE', 'named_user')
    assert HttpSettings.from_environment().access_mode == 'named_user'


@pytest.mark.parametrize('missing', ['OIDC_ISSUER', 'OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET', 'SESSION_GENERATION'])
def test_named_runtime_missing_provider_configuration_has_no_fallback(monkeypatch, missing):
    from tabula_rasa.http.composition import open_runtime
    database = Mock()
    monkeypatch.setattr('tabula_rasa.http.composition.Database', Mock(return_value=database))
    monkeypatch.setattr('tabula_rasa.http.composition.DatabaseSettings.from_environment', Mock())
    environment = {'OIDC_ISSUER': 'https://id.example.test', 'OIDC_CLIENT_ID': 'synthetic-client',
                   'OIDC_CLIENT_SECRET': 'private-secret-sentinel', 'SESSION_GENERATION': 'g' * 32}
    for key, value in environment.items():
        monkeypatch.setenv('TABULA_RASA_' + key, value)
    monkeypatch.delenv('TABULA_RASA_' + missing)
    monkeypatch.delenv('TABULA_RASA_ALPHA_DOCUMENTS_FILE', raising=False)
    with pytest.raises(RuntimeError, match='HTTP runtime startup failed') as failure:
        open_runtime(HttpSettings('production', 'https://production.example.test', access_mode='named_user'))
    assert failure.value.__context__ is None
    assert 'private-secret-sentinel' not in str(failure.value)
    database.dispose.assert_called_once()


@pytest.mark.parametrize('role', ['owner', 'admin', 'accountant', 'editor', 'read_only'])
def test_privileged_capabilities_keep_owner_approval_separate_from_operator_execution(role):
    from tabula_rasa.application.authorization import authorize_action
    from tabula_rasa.application.identity import IdentityError, capabilities
    tenant = capabilities(role)
    for action in ('backup_create', 'backup_list', 'backup_download', 'connections_manage', 'business_admin'):
        if role == 'owner':
            authorize_action(tenant, action)
        else:
            with pytest.raises(IdentityError):
                authorize_action(tenant, action)
        with pytest.raises(IdentityError):
            authorize_action(frozenset({'platform:operate', 'recovery:approved'}), action)
    for action in ('restore', 'migration', 'cutover', 'ledger_reset'):
        for granted in (tenant, tenant | {'platform:operate'}, tenant | {'recovery:approved'},
                        frozenset({'platform:operate', 'recovery:approved'})):
            with pytest.raises(IdentityError):
                authorize_action(granted, action)
        jointly_authorized = tenant | {'platform:operate', 'recovery:approved'}
        if role == 'owner':
            authorize_action(jointly_authorized, action)
        else:
            with pytest.raises(IdentityError):
                authorize_action(jointly_authorized, action)
        with pytest.raises(IdentityError):
            authorize_action(jointly_authorized, 'legacy_admin')
