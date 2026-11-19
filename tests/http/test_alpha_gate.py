from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from tabula_rasa.http.alpha import password_hash
from tabula_rasa.http.app import create_app
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.context import TrustedDevelopmentContext
from tests.http.test_transport import FakeRuntime


HASH = password_hash("synthetic-alpha-password")


def settings(environment="staging", origin="https://alpha.example.test", username="operator", hashed=HASH):
    return HttpSettings(environment, origin, "test-release", "alpha-synthetic", username, hashed)


def test_gate_covers_shell_assets_schema_and_api_before_composition(tmp_path, caplog):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>Alpha shell</html>")
    (tmp_path / "assets" / "app.js").write_text("export const value=1;")
    runtime = FakeRuntime()
    runtime.resolve_context = TrustedDevelopmentContext("alpha-synthetic", "csrf", hosted=True)
    app = create_app(settings=settings(), runtime_factory=lambda _: runtime, static_dir=tmp_path)
    with TestClient(app, base_url="https://alpha.example.test", client=("203.0.113.2", 5000)) as client:
        for path in ("/accounts", "/assets/app.js", "/api/v1/session", "/api/docs", "/api/openapi.json", "/documents/private"):
            response = client.get(path, headers={"Accept": "text/html", "Authorization": "Basic invalid"})
            assert response.status_code == 401
            assert response.headers["www-authenticate"].startswith("Basic")
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/").status_code == 401
        runtime.services.assert_not_called()
        for path in ("/health/live", "/health/ready", "/version"):
            assert client.get(path, headers={"Host": "internal-probe"}).status_code == 200
        client.auth = ("operator", "synthetic-alpha-password")
        assert client.get("/accounts", headers={"Accept": "text/html"}).status_code == 200
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200 and asset.headers["cache-control"] == "no-store"
        cached = client.get("/assets/app.js", headers={"If-None-Match": asset.headers["etag"]})
        assert cached.status_code == 304 and cached.headers["cache-control"] == "no-store"
        assert client.get("/api/openapi.json").status_code == 200
        assert client.get("/api/v1/session").json()["entity"]["id"] == "alpha-synthetic"
        runtime.services.reset_mock()
        assert client.get("/api/v1/entities/another/accounts").status_code == 403
        assert client.get("/api/v1/session", headers={"Host": "spoofed.test", "X-Forwarded-Host": "alpha.example.test"}).status_code == 403
        runtime.services.assert_not_called()
        body = {"name": "Synthetic bank", "type": "Bank"}
        assert client.post("/api/v1/entities/alpha-synthetic/accounts", json=body).status_code == 403
        assert client.post("/api/v1/entities/alpha-synthetic/accounts", json=body,
                           headers={"Origin": "https://foreign.test", "X-CSRF-Token": "csrf"}).status_code == 403
        assert client.post("/api/v1/entities/alpha-synthetic/accounts", json=body,
                           headers={"Origin": "https://alpha.example.test", "X-CSRF-Token": "csrf"}).status_code == 201
        client.auth = ("operator", "wrong-password")
        assert client.get("/accounts").status_code == 401
    assert "synthetic-alpha-password" not in caplog.text and "wrong-password" not in caplog.text
    assert HASH not in repr(settings())


def test_two_environments_do_not_accept_each_others_operator_credentials():
    for environment, username, password in (("development", "dev", "dev-password"), ("staging", "stage", "stage-password")):
        runtime = FakeRuntime()
        runtime.resolve_context = TrustedDevelopmentContext("alpha-synthetic", "csrf", hosted=True)
        configured = settings(environment, username=username, hashed=password_hash(password))
        with TestClient(create_app(settings=configured, runtime_factory=lambda _: runtime), base_url=configured.public_origin) as client:
            assert client.get("/api/v1/session", auth=(username, password)).status_code == 200
            wrong = ("stage", "stage-password") if environment == "development" else ("dev", "dev-password")
            assert client.get("/api/v1/session", auth=wrong).status_code == 401


def test_busy_password_workers_leave_probes_responsive(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock
    from tabula_rasa.http import alpha

    entered = Event()
    release = Event()
    lock = Lock()
    active = peak = 0
    original = alpha.scrypt

    def blocked_hash(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
        try:
            assert release.wait(10), "Password workers were not released"
            return original(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(alpha, "scrypt", blocked_hash)
    runtime = FakeRuntime()
    runtime.resolve_context = TrustedDevelopmentContext("alpha-synthetic", "csrf", hosted=True)
    configured = settings()
    with TestClient(create_app(settings=configured, runtime_factory=lambda _: runtime),
                    base_url=configured.public_origin) as client, ThreadPoolExecutor(max_workers=4) as workers:
        attempts = [workers.submit(client.get, "/api/v1/session", auth=("operator", password))
                    for password in ("synthetic-alpha-password", "wrong-password", "synthetic-alpha-password")]
        try:
            assert entered.wait(3), "Password verification blocked the request loop"
            for path in ("/health/live", "/health/ready", "/version"):
                assert workers.submit(client.get, path).result(timeout=3).status_code == 200
            assert not any(attempt.done() for attempt in attempts)
        finally:
            release.set()
        assert [attempt.result(timeout=3).status_code for attempt in attempts] == [200, 401, 200]
        assert peak == 2


def test_empty_password_cannot_be_generated_or_authenticate_with_an_existing_hash():
    from hashlib import scrypt
    with pytest.raises(ValueError, match="must not be empty"):
        password_hash("")
    # Even an imported hash created before the generator guard cannot grant access.
    salt = bytes(16)
    empty_hash = f"scrypt${salt.hex()}${scrypt(b'', salt=salt, n=16384, r=8, p=1, dklen=32).hex()}"
    runtime = FakeRuntime()
    runtime.resolve_context = TrustedDevelopmentContext("alpha-synthetic", "csrf", hosted=True)
    configured = settings(hashed=empty_hash)
    with TestClient(create_app(settings=configured, runtime_factory=lambda _: runtime), base_url=configured.public_origin) as client:
        response = client.get("/api/v1/session", auth=("operator", ""))
        assert response.status_code == 401
        runtime.services.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"environment": "production"}, {"origin": "http://alpha.example.test"},
    {"username": ""}, {"hashed": ""}, {"hashed": "scrypt$bad$bad"},
])
def test_hosted_alpha_fails_closed_for_bad_configuration(kwargs):
    with pytest.raises(ValueError):
        settings(**kwargs)


def test_resolver_cannot_be_bypassed_with_forwarded_loopback():
    resolver = TrustedDevelopmentContext("alpha-synthetic", "csrf", hosted=True)
    with pytest.raises(Exception) as error:
        resolver(SimpleNamespace(state=SimpleNamespace(), client=SimpleNamespace(host="127.0.0.1")))
    assert error.value.code == "unauthorized"


def test_entrypoint_does_not_trust_forwarded_headers(monkeypatch):
    from tabula_rasa.http import serve
    calls = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setenv("PORT", "10000")
    serve.main()
    assert calls[0]["proxy_headers"] is False
    assert calls[0]["access_log"] is False
    assert calls[0]["workers"] == 1


@pytest.mark.parametrize("hosted,expected", [(False, ("alpha-synthetic", "beta-synthetic")),
                                           (True, ("alpha-synthetic",))])
def test_default_runtime_preserves_local_selection_and_fixed_hosted_scope(monkeypatch, hosted, expected):
    from unittest.mock import Mock
    from tabula_rasa.http import composition

    monkeypatch.delenv("TABULA_RASA_ALPHA_DOCUMENTS_FILE", raising=False)

    database = Mock()
    monkeypatch.setattr(composition, "open_alpha_database", lambda _: database)
    monkeypatch.setattr(composition, "Database", lambda _: database)
    monkeypatch.setattr(composition.DatabaseSettings, "from_environment", lambda **_: None)
    composed = []
    monkeypatch.setattr(composition, "create_postgres_services",
                        lambda db, entity: composed.append(entity))
    configured = settings() if hosted else HttpSettings("test", "http://testserver", trusted_entity_id="alpha-synthetic")
    runtime = composition.open_runtime(configured)
    assert tuple(composed) == expected
    resolver = runtime.resolve_context
    request = SimpleNamespace(state=SimpleNamespace(alpha_authenticated=True),
                              client=SimpleNamespace(host="127.0.0.1"), cookies={})
    assert tuple(item.entity_id for item in resolver.available_contexts(request)) == expected
    request.cookies = {"tabula_entity": "beta-synthetic"}
    if hosted:
        with pytest.raises(Exception) as failure:
            resolver(request)
        assert failure.value.code == "forbidden"
    else:
        assert resolver(request).entity_id == "beta-synthetic"
    runtime.close()
    database.dispose.assert_called_once()
