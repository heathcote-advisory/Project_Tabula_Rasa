"""Synthetic data only: both sinks must receive the same allowlisted contract."""

from dataclasses import replace
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from fastapi.testclient import TestClient
import pytest
import requests
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from tabula_rasa.application.documents import DocumentError
from tabula_rasa.http.app import create_app
from tabula_rasa.http.context import RequestContext
from tabula_rasa.http.composition import translate_database_errors
from tabula_rasa.errors import StorageError
from tabula_rasa.infrastructure.postgres.runtime import safe_database_error
from tabula_rasa.telemetry import Telemetry, TelemetrySettings, configure_console
from tests.http.test_transport import FakeRuntime, SETTINGS

KEY = "synthetic-observability-key-32-characters"
SOURCE = TelemetrySettings("https://s123.eu-nbg-2.betterstackdata.com", "synthetic-source-token", KEY)
PRIVATE = "private-password-cookie-ledger-receipt@example.test"


def events(caplog):
    return [json.loads(item.message) for item in caplog.records if item.name == "tabula_rasa.http"]


@pytest.fixture
def instrumented(monkeypatch, caplog):
    monkeypatch.setenv("TABULA_RASA_TELEMETRY_SCOPE_KEY", KEY)
    caplog.set_level("INFO", logger="tabula_rasa.http")
    runtime = FakeRuntime()
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda _: runtime)) as client:
        yield client, runtime


def test_scopes_and_correlation_are_keyed_and_never_raw(instrumented, caplog):
    client, runtime = instrumented
    context = RequestContext(PRIVATE, "organization-alpha", "alpha", frozenset({"bookkeeping:read"}), "private-cache", "private-csrf")
    runtime.resolve_context = lambda _: context
    first = client.get("/api/v1/entities/alpha/accounts?token=" + PRIVATE,
        headers={"X-Correlation-ID": PRIVATE, "Authorization": "Bearer " + PRIVATE, "Cookie": "private=" + PRIVATE})
    a = events(caplog)[-1]
    runtime.resolve_context = lambda _: replace(context, entity_id="beta", organization_id="organization-beta")
    assert client.get("/api/v1/entities/beta/accounts").status_code == 200
    b = events(caplog)[-1]
    assert first.status_code == 200
    assert a["request_id"] == first.headers["X-Request-ID"]
    assert a["route"] == b["route"] == "/api/v1/entities/{entity_id}/accounts"
    assert a["operation"] == "list_accounts" and a["stage"] == "test" and a["release"] == "test-release"
    assert a["actor_ref"] == b["actor_ref"] and a["entity_ref"] != b["entity_ref"]
    assert a["organization_ref"] != b["organization_ref"]
    assert len(a["correlation_id"]) == 24 and PRIVATE not in caplog.text
    assert "private-cache" not in caplog.text and "private-csrf" not in caplog.text
    assert Telemetry("staging", "same", TelemetrySettings(scope_key=KEY)).reference("actor", PRIVATE) != a["actor_ref"]
    assert Telemetry("test", "same", TelemetrySettings(scope_key="other-key" * 5)).reference("actor", PRIVATE) != a["actor_ref"]


def test_cross_entity_and_capability_denials_use_resolved_scope(instrumented, caplog):
    client, runtime = instrumented
    assert client.get("/api/v1/entities/" + PRIVATE + "/accounts").status_code == 403
    denied = events(caplog)[-1]
    assert denied["security_event"] == "authorization_denied" and denied["security_reason"] == "cross_entity"
    assert denied["entity_ref"] == Telemetry("test", "r", TelemetrySettings(scope_key=KEY)).reference("entity", "alpha")
    assert denied["action"] == "read"
    runtime.services.assert_not_called()
    runtime.resolve_context = lambda _: RequestContext("actor", "org", "beta", frozenset({"bookkeeping:read"}), "scope", "csrf")
    assert client.post("/api/v1/entities/beta/accounts", json={"name": PRIVATE, "type": "Bank"}).status_code == 403
    denied = events(caplog)[-1]
    assert denied["security_reason"] == "capability" and denied["action"] == "write"
    assert PRIVATE not in caplog.text
    runtime.services.assert_not_called()


@pytest.mark.parametrize("error,provider", [(RuntimeError(PRIVATE), "application"), (DocumentError(PRIVATE), "documents")])
def test_exception_and_document_failures_preserve_http_result_and_safe_group(instrumented, caplog, error, provider):
    client, runtime = instrumented
    runtime.accounts.list.side_effect = error
    response = client.get("/api/v1/entities/alpha/accounts")
    event = events(caplog)[-1]
    assert response.status_code in {500, 503}
    assert event["level"] == "error" and event["outcome"] == "failure"
    assert event["provider"] == provider and event["provider_status"] == "unavailable"
    assert event["error_group"] == "list_accounts:" + response.json()["error"]["code"]
    assert PRIVATE not in response.text + caplog.text
    runtime.accounts.list.side_effect = None
    assert client.get("/api/v1/entities/alpha/accounts").status_code == 200
    assert events(caplog)[-1]["outcome"] == "success"


def test_readiness_failure_and_recovery_use_existing_probe(instrumented, caplog):
    client, runtime = instrumented
    runtime.healthy = False
    assert client.get("/health/ready").status_code == 503
    failed = events(caplog)[-1]
    assert failed["operation"] == "readiness" and failed["provider"] == "postgresql"
    assert failed["failure_code"] == "unavailable"
    runtime.healthy = True
    assert client.get("/health/ready").status_code == 200
    assert events(caplog)[-1]["outcome"] == "success"


def test_pool_and_server_capacity_failures_are_bounded(instrumented, caplog):
    for error in (PoolTimeoutError(PRIVATE), SimpleNamespace(orig=SimpleNamespace(sqlstate="53300"))):
        failure = safe_database_error(error)
        assert failure.code == "database_exhausted" and PRIVATE not in str(failure)
        with pytest.raises(StorageError) as translated:
            with translate_database_errors():
                raise failure
        assert translated.value.code == "database_exhausted"
    client, runtime = instrumented
    runtime.accounts.list.side_effect = translated.value
    assert client.get("/api/v1/entities/alpha/accounts").status_code == 503
    assert events(caplog)[-1]["provider_status"] == "exhausted"
    assert PRIVATE not in caplog.text


def test_failed_startup_emits_safe_release_signal(monkeypatch, caplog):
    caplog.set_level("INFO", logger="tabula_rasa.http")
    def fail(_):
        raise RuntimeError("HTTP runtime startup failed.")
    with pytest.raises(RuntimeError), TestClient(create_app(settings=SETTINGS, runtime_factory=fail)):
        pass
    assert events(caplog)[-1]["operation"] == "startup"
    assert events(caplog)[-1]["outcome"] == "failure"


@pytest.mark.parametrize("settings", [
    {"endpoint": "https://evil.example.test", "token": "synthetic-source-token"},
    {"endpoint": SOURCE.endpoint}, {"token": SOURCE.token},
    {"endpoint": SOURCE.endpoint + "/private", "token": SOURCE.token},
    {"endpoint": "https://user:password@s123.betterstackdata.com", "token": SOURCE.token},
    {"endpoint": SOURCE.endpoint, "token": "private\nheader"}, {"scope_key": "short"},
])
def test_bad_exporter_binding_fails_without_echo(settings):
    with pytest.raises(ValueError, match="Invalid telemetry configuration") as failure:
        TelemetrySettings(**settings)
    assert "evil" not in str(failure.value) and "password" not in str(failure.value)


def test_system_hook_rejects_unbounded_labels_and_drops_raw_objects(caplog):
    caplog.set_level("INFO", logger="tabula_rasa.http")
    telemetry = Telemetry("test", "r", TelemetrySettings(scope_key=KEY))
    event = telemetry.record("job_run", "job_execute", job_id=PRIVATE, failure_code=PRIVATE,
        security_event=PRIVATE, provider_status=PRIVATE, route=PRIVATE, action=PRIVATE, request_id=PRIVATE,
        duration_ms=float("nan"), context=object())
    assert event["job_ref"] and event["operation"] == "job_execute"
    assert event["duration_ms"] is None and event["route"] == "unmatched"
    assert PRIVATE not in caplog.text
    assert telemetry.record(PRIVATE, PRIVATE) is None


@pytest.mark.parametrize("status,raises,expected", [(202, False, None), (402, False, "exhausted"),
    (403, False, "unavailable"), (302, False, "unavailable"), (500, False, "unavailable"), (0, True, "unavailable")])
def test_exporter_sends_only_sanitized_payload_and_safe_failure(monkeypatch, caplog, status, raises, expected):
    caplog.set_level("INFO", logger="tabula_rasa.http")
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    response = Mock(status_code=status)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    delivered = Event()
    def post(*args, **kwargs):
        delivered.set()
        if raises:
            raise requests.ConnectionError(PRIVATE + SOURCE.token)
        return response
    session.post.side_effect = post
    monkeypatch.setattr("tabula_rasa.telemetry.requests.Session", lambda: session)
    telemetry = Telemetry("test", "release", SOURCE)
    telemetry.record("http_request", "list_accounts", status=503, failure_code="unavailable", actor_id=PRIVATE)
    assert delivered.wait(3)
    telemetry.close()
    payload = session.post.call_args.kwargs["json"][0]
    assert payload["outcome"] == "failure" and PRIVATE not in json.dumps(payload)
    assert session.post.call_args.kwargs["allow_redirects"] is False
    assert session.post.call_args.kwargs["stream"] is True
    assert session.trust_env is False
    failures = [event for event in events(caplog) if event["event"] == "telemetry_delivery"]
    assert bool(failures) == bool(expected)
    if expected:
        assert failures[0]["provider_status"] == expected
    assert PRIVATE not in caplog.text and SOURCE.token not in caplog.text


def test_queue_is_bounded_and_reports_loss_without_recursion(caplog):
    caplog.set_level("INFO", logger="tabula_rasa.http")
    telemetry = Telemetry("test", "r")
    telemetry.thread = Mock()  # Deterministic stalled exporter.
    for _ in range(140):
        telemetry.record("job_run", "job_execute", provider_status="unavailable")
    assert telemetry.queue.qsize() == 128
    assert len([event for event in events(caplog) if event["event"] == "telemetry_delivery"]) == 1


def test_broken_sink_cannot_change_failure_to_success(instrumented, monkeypatch):
    client, runtime = instrumented
    runtime.accounts.list.side_effect = RuntimeError(PRIVATE)
    monkeypatch.setattr("tabula_rasa.telemetry.logger.log", Mock(side_effect=OSError(PRIVATE)))
    response = client.get("/api/v1/entities/alpha/accounts")
    assert response.status_code == 500 and response.json()["error"]["code"] == "unexpected"
    assert PRIVATE not in response.text


def test_server_console_emits_info_json_without_tracebacks(capsys):
    import logging
    logger = logging.getLogger("tabula_rasa.http")
    old = logger.handlers[:], logger.level, logger.propagate
    try:
        configure_console()
        Telemetry("test", "release").record("runtime", "startup", provider_status="ok")
        event = json.loads(capsys.readouterr().err)
        assert event["event"] == "runtime" and event["release"] == "release"
    finally:
        logger.handlers[:], logger.level, logger.propagate = old


def test_exporter_initialization_failure_is_sanitized(monkeypatch, caplog):
    caplog.set_level("INFO", logger="tabula_rasa.http")
    monkeypatch.setattr("tabula_rasa.telemetry.requests.Session", Mock(side_effect=RuntimeError(PRIVATE)))
    telemetry = Telemetry("test", "release", SOURCE)
    telemetry.thread.join(timeout=3)
    telemetry.close()
    assert events(caplog)[-1]["provider_status"] == "unavailable"
    assert PRIVATE not in caplog.text


def test_shutdown_drains_already_queued_confirmed_outcomes(monkeypatch):
    started, release = Event(), Event()
    session = MagicMock()
    session.__enter__.return_value = session
    session.post.return_value.__enter__.return_value.status_code = 202

    def open_session():
        started.set()
        assert release.wait(3)
        return session

    monkeypatch.setattr('tabula_rasa.telemetry.requests.Session', open_session)
    telemetry = Telemetry('test', 'release', SOURCE)
    assert started.wait(3)
    telemetry.record('job_run', 'job_execute', provider_status='ok')
    telemetry.stopped.set()
    release.set()
    telemetry.close()
    assert not telemetry.thread.is_alive()
    assert telemetry.queue.empty()
    assert session.post.call_args.kwargs['json'][0]['outcome'] == 'success'


def test_missing_dependency_and_unknown_error_never_claim_success():
    telemetry = Telemetry("test", "r")
    missing = telemetry.record("job_run", "job_execute", provider_status="not_configured")
    unknown = telemetry.record("job_run", "job_execute", failure_code=PRIVATE)
    assert missing["outcome"] == unknown["outcome"] == "failure"
    assert unknown["failure_code"] == "unexpected"
    assert PRIVATE not in json.dumps(unknown)


def test_system_producer_does_not_import_http_framework():
    from pathlib import Path
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c",
        "import sys; from tabula_rasa.telemetry import Telemetry; "
        "Telemetry('test','r').record('job_run','job_execute',provider_status='ok'); "
        "assert 'tabula_rasa.http.app' not in sys.modules and 'fastapi' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_login_diagnostics_are_allowlisted_and_cookie_reference_is_pseudonymized():
    telemetry = Telemetry('test', 'release', TelemetrySettings(scope_key=KEY))
    try:
        event = telemetry.record('http_request', 'complete_login', security_event='login_failed',
            security_reason='login_freshness', login_request_id='a' * 32)
        assert event['security_reason'] == 'login_freshness'
        assert len(event['login_ref']) == 24 and event['login_ref'] != 'a' * 32
        for injected in (PRIVATE, 'a' * 1025, 'synthetic@example.test'):
            event = telemetry.record('http_request', 'complete_login', security_reason=injected,
                                     login_request_id=injected)
            assert event['security_reason'] is None and event['login_ref'] is None
            assert injected not in json.dumps(event)
    finally:
        telemetry.close()
