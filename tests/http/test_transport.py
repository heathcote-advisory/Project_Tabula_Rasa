from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from tabula_rasa.errors import StorageConflictError, StorageError, StorageNotFoundError, StorageValidationError
from tabula_rasa.application.identity import AuthenticatedSession, EntityAccess
from tabula_rasa.http.app import create_app
from tabula_rasa.http.composition import PostgresRuntime
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.context import DenyContext, RequestContext, TrustedDevelopmentContext
from tabula_rasa.http.export_openapi import export_schema
from tabula_rasa.http.identity import NamedIdentity
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError

SETTINGS = HttpSettings("test", "http://testserver", "test-release")
RECORD = dict(
    id="account-1", name="Bank", type="Bank", classification="Asset",
    opening_balance="12.50", active=True, created_at="2026-10-07T12:00:00Z",
    parent_id="", baseline="opaque-token", private_locator="NEVER-EXPOSE",
)


class FakeRuntime:
    def __init__(self):
        self.resolve_context = lambda request: RequestContext(
            "actor", "organization", "alpha", frozenset({"bookkeeping:read", "bookkeeping:write"}),
            "session-1", "test-csrf",
        )
        self.accounts = Mock()
        self.workspaces = SimpleNamespace(bootstrap_enabled=False)
        self.accounts.list.return_value = [RECORD]
        self.accounts.create.return_value = RECORD
        self.services = Mock(return_value=SimpleNamespace(
            accounts=self.accounts, ledger=SimpleNamespace(get_info=lambda: SimpleNamespace(name="Alpha", currency_code="USD")),
        ))
        self.healthy = True
        self.closed = False

    def operation(self):
        return nullcontext()

    def ready(self):
        return self.healthy

    def close(self):
        self.closed = True


@pytest.fixture
def runtime():
    return FakeRuntime()


@pytest.fixture
def client(runtime):
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda settings: runtime)) as client:
        yield client


def test_read_and_create_delegate_without_transport_domain_rules(client, runtime):
    response = client.get("/api/v1/entities/alpha/accounts?active_only=false")
    assert response.status_code == 200
    assert response.json()["items"][0]["opening_balance"] == "12.50"
    assert "private_locator" not in response.text
    runtime.accounts.list.assert_called_once_with(active_only=False)
    response = client.post("/api/v1/entities/alpha/accounts",
        headers={"Origin": "http://testserver", "X-CSRF-Token": "test-csrf"},
        json={"name": "  Delegated name ", "type": "custom-domain-input", "opening_balance": "-1.25"})
    assert response.status_code == 201
    runtime.accounts.create.assert_called_once_with(
        "  Delegated name ", "custom-domain-input", "-1.25", classification=None, parent_id=None,
    )


def test_entity_disagreement_and_missing_identity_fail_before_composition(client, runtime):
    assert client.get("/api/v1/entities/beta/accounts").status_code == 403
    runtime.services.assert_not_called()
    runtime.resolve_context = DenyContext()
    response = client.get("/api/v1/entities/alpha/accounts")
    assert response.status_code == 401 and response.json()["error"]["code"] == "unauthorized"
    runtime.services.assert_not_called()


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "https://foreign.test", "X-CSRF-Token": "test-csrf"},
    {"Origin": "http://testserver"}, {"Origin": "http://testserver", "X-CSRF-Token": "wrong"},
])
def test_unsafe_origin_and_csrf_rejected(client, runtime, headers):
    assert client.post("/api/v1/entities/alpha/accounts", json={"name": "Bank", "type": "Bank"}, headers=headers).status_code == 403
    runtime.services.assert_not_called()


@pytest.mark.parametrize('configured,canonical', [
    ('HTTPS://NAMED.example.test', 'https://named.example.test'),
    ('https://named.example.test:443', 'https://named.example.test'),
    ('https://named.example.test:8443', 'https://named.example.test:8443'),
    ('HTTP://LOCALHOST:80', 'http://localhost'),
    ('http://[::1]:8000', 'http://[::1]:8000'),
])
def test_canonical_origin_accepts_browser_mutations_without_relaxing_scope(runtime, configured, canonical):
    settings = HttpSettings('test', configured, access_mode='named_user')
    assert settings.public_origin == canonical
    now = datetime.now(timezone.utc)
    sessions = Mock()
    sessions.validate.return_value = AuthenticatedSession('actor', 'session-1', 'test-csrf', now,
        now + timedelta(hours=12), EntityAccess('alpha', 'organization', 'Alpha', 'editor', 1))
    runtime.identity = runtime.resolve_context = NamedIdentity(settings, sessions, Mock())
    with TestClient(create_app(settings=settings, runtime_factory=lambda _: runtime), base_url=canonical) as client:
        payload = {'name': 'Bank', 'type': 'Bank'}
        headers = {'Origin': canonical, 'X-CSRF-Token': 'test-csrf'}
        assert client.post('/api/v1/entities/alpha/accounts', json=payload, headers=headers).status_code == 201
        runtime.services.reset_mock()
        for origin in ('https://foreign.test', 'null', ''):
            assert client.post('/api/v1/entities/alpha/accounts', json=payload,
                headers={**headers, 'Origin': origin}).status_code == 403
        assert client.post('/api/v1/entities/beta/accounts', json=payload, headers=headers).status_code == 403
        assert client.post('/api/v1/entities/alpha/accounts', json=payload,
            headers={**headers, 'X-CSRF-Token': 'wrong'}).status_code == 403
        runtime.services.assert_not_called()


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('selected', [None, EntityAccess('alpha', 'organization', 'Alpha', 'read_only', 1)])
def test_named_session_exposes_runtime_creation_availability_before_and_after_selection(runtime, enabled, selected):
    settings = HttpSettings('test', 'http://testserver', access_mode='named_user')
    now = datetime.now(timezone.utc)
    sessions = Mock()
    sessions.validate.return_value = AuthenticatedSession('actor', 'session-1', 'test-csrf', now,
        now + timedelta(hours=12), selected)
    sessions.select.return_value = AuthenticatedSession('actor', 'session-1', 'test-csrf', now,
        now + timedelta(hours=12), EntityAccess('beta', 'other-organization', 'Beta', 'owner', 1))
    runtime.identity = runtime.resolve_context = NamedIdentity(settings, sessions, Mock())
    runtime.workspaces = SimpleNamespace(bootstrap_enabled=enabled)
    with TestClient(create_app(settings=settings, runtime_factory=lambda _: runtime)) as client:
        result = client.get('/api/v1/session')
        assert result.status_code == 200
        assert result.json()['business_creation_available'] is enabled
        assert result.json()['state'] == ('ready' if selected else 'needs_setup')
        switched = client.post('/api/v1/session/entity', json={'entity_id': 'beta'},
            headers={'Origin': 'http://testserver', 'X-CSRF-Token': 'test-csrf'})
        assert switched.status_code == 200
        assert switched.json()['business_creation_available'] is enabled
        assert switched.json()['entity']['id'] == 'beta'


def test_preview_contract_declares_expired_envelope(tmp_path):
    output = tmp_path / 'openapi.json'
    export_schema(output)
    schema = json.loads(output.read_text())
    response = schema['paths']['/api/v1/invitations/preview']['post']['responses']['410']
    assert response['content']['application/json']['schema']['$ref'] == '#/components/schemas/ErrorEnvelope'


@pytest.mark.parametrize('origin', ['https://named.example.test:invalid', 'https://named.example.test:65536'])
def test_invalid_origin_port_rejected(origin):
    with pytest.raises(ValueError, match='Invalid HTTP configuration'):
        HttpSettings('test', origin, access_mode='named_user')


def test_capability_enforcement(client, runtime):
    runtime.resolve_context = lambda request: RequestContext("actor", "org", "alpha",
        frozenset({"bookkeeping:read"}), "scope", "test-csrf")
    assert client.get("/api/v1/entities/alpha/accounts").status_code == 200
    runtime.services.reset_mock()
    assert client.post("/api/v1/entities/alpha/accounts", json={"name": "Bank", "type": "Bank"},
        headers={"Origin": "http://testserver", "X-CSRF-Token": "test-csrf"}).status_code == 403
    runtime.services.assert_not_called()


@pytest.mark.parametrize("error,status,code", [
    (StorageValidationError("PRIVATE", code="reference"), 422, "reference"),
    (StorageValidationError("PRIVATE", code="baseline_required"), 422, "baseline_required"),
    (StorageValidationError("PRIVATE", code="baseline_invalid"), 422, "baseline_invalid"),
    (StorageValidationError("PRIVATE", code="arbitrary"), 422, "validation"),
    (StorageConflictError("PRIVATE", code="stale"), 409, "stale"),
    (StorageConflictError("PRIVATE"), 409, "conflict"),
    (StorageNotFoundError("PRIVATE"), 404, "not_found"),
    (StorageError("PRIVATE"), 503, "unavailable"),
    (RuntimeError("PRIVATE SQL password"), 500, "unexpected"),
])
def test_safe_stable_error_envelopes(client, runtime, error, status, code):
    runtime.accounts.list.side_effect = error
    response = client.get("/api/v1/entities/alpha/accounts")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "PRIVATE" not in response.text


def test_validation_does_not_echo_draft_or_extra_field_names(client):
    response = client.post("/api/v1/entities/alpha/accounts",
        headers={"Origin": "http://testserver", "X-CSRF-Token": "test-csrf"},
        json={"name": "secret ledger draft", "type": "Bank", "opening_balance": 2.5, "private-extra-key": "password"})
    assert response.status_code == 422
    assert response.json()["error"]["fields"][0]["field"] == "opening_balance"
    assert all(value not in response.text for value in ["secret ledger", "private-extra-key", "password"])


def test_request_id_logging_allowlist_and_host_policy(client, runtime, caplog):
    with caplog.at_level("INFO", logger="tabula_rasa.http"):
        response = client.get("/api/v1/entities/alpha/accounts?private=password",
            headers={"X-Request-ID": "untrusted-request", "X-Correlation-ID": "safe-correlation", "Authorization": "private-password"})
    assert response.headers["X-Request-ID"] != "untrusted-request"
    assert response.headers["Cache-Control"] == "no-store"
    event = json.loads(caplog.records[-1].message)
    assert event["route"] == "/api/v1/entities/{entity_id}/accounts"
    assert event["correlation_id"] is None  # No pseudonym key in ordinary local tests.
    assert "password" not in caplog.text
    runtime.services.reset_mock()
    assert client.get("/api/v1/session", headers={"Host": "spoofed.test"}).status_code == 403
    runtime.services.assert_not_called()


def test_session_and_health_lifecycle(runtime):
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda settings: runtime)) as client:
        session = client.get("/api/v1/session").json()
        assert session["entity"] == {"id": "alpha", "name": "Alpha", "currency_code": "USD"}
        assert session["csrf_token"] == "test-csrf"
        assert session["business_creation_available"] is False
        assert client.get("/health/live").json() == {"status": "live"}
        runtime.healthy = False
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200
        assert client.get("/version").json() == {"release": "test-release"}
        assert client.get("/api/missing").status_code == 404
    assert runtime.closed


def test_schema_export_is_deterministic_offline(monkeypatch, tmp_path):
    monkeypatch.setattr("tabula_rasa.http.app.HttpSettings.from_environment",
                        lambda: pytest.fail("Schema must not read settings"))
    monkeypatch.setattr("tabula_rasa.http.app.open_runtime",
                        lambda settings: pytest.fail("Schema must not construct runtime"))
    output = tmp_path / "schema.json"
    export_schema(output)
    first = output.read_bytes()
    export_schema(output)
    assert output.read_bytes() == first
    schema = json.loads(first)
    operation = schema["paths"]["/api/v1/entities/{entity_id}/accounts"]["get"]
    assert any(item["name"] == "entity_id" and item["in"] == "path" for item in operation["parameters"])
    assert "ErrorEnvelope" in schema["components"]["schemas"]
    expired = schema['paths']['/api/v1/invitations/respond']['post']['responses']['410']
    assert expired['content']['application/json']['schema'] == {'$ref': '#/components/schemas/ErrorEnvelope'}


@pytest.mark.parametrize("code,status,mapped", [
    ("duplicate", 409, "duplicate"), ("concurrent_write", 409, "concurrent_write"),
    ("reference", 422, "reference"), ("invariant", 422, "invariant"),
    ("required", 422, "required"), ("unavailable", 503, "unavailable"),
    ("read_only", 503, "unavailable"), ("operation_aborted", 503, "unavailable"),
])
def test_database_errors_translated_only_by_composition(runtime, code, status, mapped):
    composed = PostgresRuntime(None, runtime.resolve_context, service_factory=runtime.services)
    # This transport-only factory has neither a database nor persisted effects.
    # Native HTTP tests separately prove actor/event coupling with a real pool.
    composed.audit_operation = lambda *args: nullcontext()
    composed.close = runtime.close
    runtime.accounts.list.side_effect = DatabaseError(code)
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda settings: composed)) as client:
        response = client.get("/api/v1/entities/alpha/accounts")
        assert response.status_code == status and response.json()["error"]["code"] == mapped
    # Test factory does not own a pool in this case.


def test_trusted_context_requires_loopback_and_never_uses_request_entity():
    resolver = TrustedDevelopmentContext("alpha", "csrf")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.1"))
    with pytest.raises(Exception) as failure:
        resolver(request)
    assert failure.value.code == "forbidden"
    assert resolver(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), cookies={})).entity_id == "alpha"


@pytest.mark.parametrize("environment,origin", [
    ("production", "https://example.test"), ("staging", "https://example.test"),
    ("development", "http://0.0.0.0:5173"),
])
def test_trusted_fixture_cannot_enable_hosted_or_public_scope(environment, origin):
    with pytest.raises(ValueError):
        HttpSettings(environment, origin, trusted_entity_id="alpha")


def test_spa_fallback_does_not_mask_api_asset_or_non_html_errors(runtime, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>Shell</html>")
    (tmp_path / "assets" / "app-hash.js").write_text("export const value = 1;")
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda settings: runtime, static_dir=tmp_path)) as client:
        for accept in (None, "*/*", "text/html"):
            headers = {} if accept is None else {"Accept": accept}
            request = client.build_request("GET", "/", headers=headers)
            if accept is None:
                del request.headers["accept"]
            response = client.send(request)
            assert response.status_code == 200
            assert response.text == "<html>Shell</html>"
            assert response.headers["content-type"].startswith("text/html")
            assert response.headers["cache-control"] == "no-cache"
        assert client.head("/").status_code == 200
        assert client.get("/accounts").status_code == 404
        assert client.get("/accounts", headers={"Accept": "text/html"}).status_code == 200
        assert client.head("/accounts", headers={"Accept": "text/html"}).status_code == 200
        for path in ["/api/v1/unknown", "/assets/missing.js", "/health/unknown", "/missing.js"]:
            response = client.get(path, headers={"Accept": "text/html"})
            assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
        assert client.get("/accounts", headers={"Accept": "application/json"}).status_code == 404
        assert client.post("/accounts").status_code == 405

def test_schema_access_guard_and_asset_cache_policy(client, runtime, tmp_path):
    assert client.get("/api/openapi.json").status_code == 200
    runtime.resolve_context = DenyContext()
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/docs").status_code == 401
    for path in ("/health/live", "/health/ready", "/version"):
        assert client.get(path, headers={"Host": "internal-probe"}).status_code == 200
    runtime.services.reset_mock()
    assert client.get("/api/v1/session", headers={"Host": "TESTSERVER"}).status_code == 401
    runtime.services.assert_not_called()


def test_hashed_asset_cache_and_html_revalidation(runtime, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>Shell</html>")
    (tmp_path / "assets" / "app-hash.js").write_text("export const value = 1;")
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda settings: runtime, static_dir=tmp_path)) as client:
        assert client.get("/assets/app-hash.js").headers["Cache-Control"] == "public, max-age=31536000, immutable"
        for path in ('/assets', '/assets/new', '/assets/synthetic-id'):
            response = client.get(path, headers={'Accept': 'text/html'})
            assert response.status_code == 200
            assert response.headers['Cache-Control'] == 'no-cache'
        assert client.get('/assets/missing.js', headers={'Accept': 'text/html'}).status_code == 404
        assert client.get('/assets/synthetic-id').status_code == 404
        assert client.get("/accounts", headers={"Accept": "text/html"}).headers["Cache-Control"] == "no-cache"


def test_host_case_equivalence_preserves_private_route_guard(client, runtime):
    assert client.get("/api/v1/session", headers={"Host": "TESTSERVER"}).status_code == 200
    runtime.services.reset_mock()
    assert client.get("/api/v1/session", headers={"Host": "TESTSERVER:8000"}).status_code == 403
    runtime.services.assert_not_called()
