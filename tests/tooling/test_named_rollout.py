"""Named-user promotion boundaries; all provider configuration is synthetic."""

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

from fastapi.testclient import TestClient
import pytest
import yaml

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.deployment import release, smoke
from tabula_rasa.http.app import create_app
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.identity import NamedIdentity
from tests.http.test_transport import FakeRuntime
from tests.tooling.test_commercial_deployment import ENV, IMAGE, metadata, provider, release_ancestry  # noqa: F401

NAMED = {key: value for key, value in ENV.items() if 'ALPHA_' not in key}
NAMED['TABULA_RASA_ACCESS_MODE'] = 'named_user'
ROOT = Path(__file__).resolve().parents[2]


def test_named_promotion_has_no_basic_credentials_or_automatic_alpha_rollback():
    read, check = provider(), Mock()
    result = release.promote(IMAGE, NAMED, read=read, metadata=metadata, verify=Mock(), smoke_check=check)
    assert result['access_mode'] == 'named_user'
    check.assert_called_once_with(NAMED['TABULA_RASA_PUBLIC_ORIGIN'], 'c' * 40, previous_release='d' * 40)
    read, check = provider(), Mock(side_effect=RuntimeError('private provider diagnostic'))
    with pytest.raises(release.ReleaseError, match='reviewed named-mode rollback') as failure:
        release.promote(IMAGE, NAMED, read=read, metadata=metadata, verify=Mock(), smoke_check=check)
    assert 'private provider' not in str(failure.value)
    assert sum(call.kwargs.get('body') is not None for call in read.call_args_list) == 1
    assert check.call_count == 1


@pytest.mark.parametrize('changes', [
    {'TABULA_RASA_ACCESS_MODE': ''}, {'TABULA_RASA_ACCESS_MODE': 'typo'},
    {'TABULA_RASA_ALPHA_USERNAME': 'old-operator'}, {'TABULA_RASA_ALPHA_PASSWORD': 'private'},
    {'TABULA_RASA_TRUSTED_ENTITY_ID': 'alpha-synthetic'}, {'TABULA_RASA_ALPHA_PASSWORD_HASH': 'old-hash'},
])
def test_invalid_named_promotion_stops_before_registry_or_render(changes):
    read, inspect = Mock(), Mock()
    with pytest.raises(ValueError):
        release.promote(IMAGE, {**NAMED, **changes}, read=read, metadata=inspect)
    read.assert_not_called()
    inspect.assert_not_called()


@pytest.mark.parametrize('environment', ['development', 'staging', 'production'])
def test_hosted_missing_mode_fails_before_runtime_allocation(monkeypatch, environment):
    monkeypatch.setenv('TABULA_RASA_ENVIRONMENT', environment)
    monkeypatch.setenv('TABULA_RASA_PUBLIC_ORIGIN', 'https://stage.example.test')
    monkeypatch.delenv('TABULA_RASA_ACCESS_MODE', raising=False)
    factory = Mock()
    with pytest.raises(ValueError, match='explicit access mode'):
        with TestClient(create_app(runtime_factory=factory)):
            pass
    factory.assert_not_called()


def test_local_fixture_keeps_its_default_and_hosted_alpha_can_be_explicit(monkeypatch):
    monkeypatch.setenv('TABULA_RASA_ENVIRONMENT', 'test')
    monkeypatch.setenv('TABULA_RASA_PUBLIC_ORIGIN', 'http://testserver')
    for key in ('ACCESS_MODE', 'TRUSTED_ENTITY_ID', 'ALPHA_USERNAME', 'ALPHA_PASSWORD_HASH'):
        monkeypatch.delenv('TABULA_RASA_' + key, raising=False)
    assert HttpSettings.from_environment().access_mode == 'restricted_alpha'
    from tests.http.test_alpha_gate import HASH
    monkeypatch.setenv('TABULA_RASA_ENVIRONMENT', 'staging')
    monkeypatch.setenv('TABULA_RASA_PUBLIC_ORIGIN', 'https://stage.example.test')
    monkeypatch.setenv('TABULA_RASA_ACCESS_MODE', 'restricted_alpha')
    monkeypatch.setenv('TABULA_RASA_TRUSTED_ENTITY_ID', 'alpha-synthetic')
    monkeypatch.setenv('TABULA_RASA_ALPHA_USERNAME', 'stage')
    monkeypatch.setenv('TABULA_RASA_ALPHA_PASSWORD_HASH', HASH)
    assert HttpSettings.from_environment().hosted_alpha


def test_named_probe_against_http_guard_denies_before_composition(tmp_path):
    configured = HttpSettings('staging', NAMED['TABULA_RASA_PUBLIC_ORIGIN'], 'c' * 40, access_mode='named_user')
    sessions = Mock()
    sessions.validate.side_effect = IdentityError('unauthorized')
    auth = NamedIdentity(configured, sessions, Mock())
    runtime = FakeRuntime()
    runtime.identity = runtime.resolve_context = auth
    (tmp_path / 'index.html').write_text('<html>Public sign-in shell</html>')
    (tmp_path / 'assets').mkdir()
    with TestClient(create_app(settings=configured, runtime_factory=lambda _: runtime, static_dir=tmp_path),
                    base_url=configured.public_origin) as client:
        # Adapt only the response interface; real HTTP handlers enforce denial.
        def get(path, **kwargs):
            kwargs.pop('timeout', None)
            response = client.get(path, **kwargs)
            return SimpleNamespace(status=response.status_code, headers=response.headers, json=response.json)
        smoke.named_probe(SimpleNamespace(request=SimpleNamespace(get=get)), configured.release_id)
    runtime.services.assert_not_called()
    assert sessions.validate.call_count == 18


@pytest.mark.parametrize('status,challenge,cache', [(200, '', 'no-store'), (403, '', 'no-store'),
                                                   (401, 'Basic realm=alpha', 'no-store'), (401, '', 'public')])
def test_named_probe_does_not_accept_alpha_or_unavailable_boundary(status, challenge, cache):
    request = Mock()
    request.get.side_effect = [SimpleNamespace(status=200), SimpleNamespace(json=lambda: {'release': 'c' * 40}),
        SimpleNamespace(status=status, headers={'www-authenticate': challenge, 'cache-control': cache})]
    with pytest.raises(ValueError, match='access restriction'):
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40)


def test_named_probe_waits_for_known_prior_release_before_checking_access():
    versions = iter(['d' * 40, 'd' * 40, 'c' * 40])
    request = Mock()

    def get(path, **_):
        if path == '/health/ready':
            return SimpleNamespace(status=200)
        if path == '/version':
            return SimpleNamespace(json=lambda: {'release': next(versions)})
        if path in ('/api/v1/unknown', '/assets/missing.js'):
            return SimpleNamespace(status=404)
        return SimpleNamespace(status=401, headers={'cache-control': 'no-store'})

    request.get.side_effect = get
    sleep = Mock()
    smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                      previous_release='d' * 40, sleep=sleep)
    assert sleep.call_args_list == [call(3), call(3)]
    assert sum(c.args[0] == '/api/v1/session' for c in request.get.call_args_list) == 3


@pytest.mark.parametrize('observed,previous', [
    ({'release': 'e' * 40}, 'd' * 40),
    ({'release': 'd' * 40}, None),
    ({'release': 'd' * 40, 'unexpected': True}, 'd' * 40),
])
def test_named_probe_rejects_unexpected_release_without_waiting(observed, previous):
    request = Mock()
    request.get.side_effect = [SimpleNamespace(status=200), SimpleNamespace(json=lambda: observed)]
    sleep = Mock()
    with pytest.raises(ValueError, match='Release identity mismatch'):
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                          previous_release=previous, sleep=sleep)
    sleep.assert_not_called()
    assert request.get.call_count == 2


def test_named_probe_stops_when_prior_release_never_hands_over():
    request = Mock()
    request.get.side_effect = lambda path, **_: (
        SimpleNamespace(status=200) if path == '/health/ready'
        else SimpleNamespace(json=lambda: {'release': 'd' * 40}))
    sleep = Mock()
    with pytest.raises(ValueError, match='handover did not converge'):
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                          previous_release='d' * 40, sleep=sleep)
    assert sleep.call_count == 20
    assert request.get.call_count == 22


def test_handover_does_not_retry_a_failed_access_boundary():
    request = Mock()
    request.get.side_effect = [SimpleNamespace(status=200),
        SimpleNamespace(json=lambda: {'release': 'd' * 40}),
        SimpleNamespace(json=lambda: {'release': 'c' * 40}),
        SimpleNamespace(status=403, headers={'cache-control': 'no-store'})]
    sleep = Mock()
    with pytest.raises(ValueError, match='access restriction'):
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                          previous_release='d' * 40, sleep=sleep)
    sleep.assert_called_once_with(3)
    assert request.get.call_count == 4


@pytest.mark.parametrize('version_status,readiness_status', [(502, 504), (503, 503), (504, 502)])
def test_maintenance_handover_waits_for_prior_and_readiness_before_access(version_status, readiness_status):
    unavailable = SimpleNamespace(status=version_status, json=Mock(side_effect=AssertionError('Not a JSON response')))
    available = [
        SimpleNamespace(status=200, json=Mock(return_value={'release': revision}))
        for revision in ['d' * 40, 'c' * 40, 'c' * 40]
    ]
    versions = iter([unavailable, *available])
    readiness = iter([readiness_status, 200])
    request = Mock()

    def get(path, **_):
        if path == '/version':
            return next(versions)
        if path == '/health/ready':
            return SimpleNamespace(status=next(readiness))
        if path in ('/api/v1/unknown', '/assets/missing.js'):
            return SimpleNamespace(status=404)
        return SimpleNamespace(status=401, headers={'cache-control': 'no-store'})

    request.get.side_effect = get
    sleep = Mock()
    smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                      previous_release='d' * 40, maintenance=True, sleep=sleep)
    unavailable.json.assert_not_called()
    assert sleep.call_args_list == [call(3), call(3), call(3)]
    assert [c.args[0] for c in request.get.call_args_list[:7]] == [
        '/version', '/version', '/version', '/health/ready', '/version', '/health/ready', '/api/v1/session',
    ]


@pytest.mark.parametrize('defect', [
    'no_prior', 'unknown_release', 'never_ready', 'unexpected_status', 'access',
    'version_unavailable', 'version_status', 'version_json',
])
def test_maintenance_handover_still_fails_closed(defect):
    request = Mock()

    def get(path, **_):
        if path == '/version':
            if defect == 'version_unavailable':
                return SimpleNamespace(status=503)
            if defect == 'version_status':
                return SimpleNamespace(status=403)
            if defect == 'version_json':
                return SimpleNamespace(status=200, json=Mock(side_effect=ValueError('private response detail')))
            return SimpleNamespace(status=200, json=lambda: {'release': ('e' if defect == 'unknown_release' else 'c') * 40})
        if path == '/health/ready':
            return SimpleNamespace(status=503 if defect == 'never_ready' else 403 if defect == 'unexpected_status' else 200)
        return SimpleNamespace(status=403, headers={'cache-control': 'no-store'})

    request.get.side_effect = get
    sleep = Mock()
    expected = {'no_prior': 'verified prior', 'unknown_release': 'identity mismatch',
                'never_ready': 'handover did not converge', 'unexpected_status': 'Readiness failed',
                'access': 'access restriction', 'version_unavailable': 'handover did not converge',
                'version_status': 'Version endpoint returned an unexpected status',
                'version_json': 'Version response was not valid JSON'}[defect]
    with pytest.raises(smoke.NamedSmokeError, match=expected) as failure:
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                          previous_release=None if defect == 'no_prior' else 'd' * 40,
                          maintenance=True, sleep=sleep)
    assert 'private response detail' not in str(failure.value)
    if defect == 'version_status':
        assert str(failure.value) == 'Version endpoint returned an unexpected status (HTTP 403).'
    if defect == 'unexpected_status':
        assert str(failure.value) == 'Readiness failed (HTTP 403).'
    assert sleep.call_count == (200 if defect in {'never_ready', 'version_unavailable'} else 0)
    if defect == 'never_ready':
        assert request.get.call_count == 402
    if defect == 'version_unavailable':
        assert request.get.call_count == 201
    if defect != 'access':
        assert not any(c.args[0].startswith('/api/') for c in request.get.call_args_list)


@pytest.mark.parametrize('path', ['/version', '/health/ready'])
@pytest.mark.parametrize('status', [502, 504])
def test_maintenance_gateway_failure_exhausts_budget_without_access_checks(path, status):
    request, sleep = Mock(), Mock()
    request.get.side_effect = lambda observed, **_: (
        SimpleNamespace(status=status) if observed == path
        else SimpleNamespace(status=200, json=lambda: {'release': 'c' * 40}))
    with pytest.raises(smoke.NamedSmokeError, match='handover did not converge'):
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                          previous_release='d' * 40, maintenance=True, sleep=sleep)
    assert sleep.call_count == 200
    assert request.get.call_count == (201 if path == '/version' else 402)
    assert not any(c.args[0].startswith('/api/') for c in request.get.call_args_list)


@pytest.mark.parametrize('path', ['/version', '/health/ready'])
@pytest.mark.parametrize('status', [401, 403, 404, 500])
def test_maintenance_other_failures_stop_immediately_with_safe_status(path, status):
    request, sleep = Mock(), Mock()
    request.get.side_effect = lambda observed, **_: (
        SimpleNamespace(status=status, json=Mock(side_effect=ValueError('private response detail')))
        if observed == path else SimpleNamespace(status=200, json=lambda: {'release': 'c' * 40}))
    with pytest.raises(smoke.NamedSmokeError, match=f'HTTP {status}') as failure:
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                          previous_release='d' * 40, maintenance=True, sleep=sleep)
    assert 'private response detail' not in str(failure.value)
    sleep.assert_not_called()
    assert not any(c.args[0].startswith('/api/') for c in request.get.call_args_list)


@pytest.mark.parametrize('status', [502, 503, 504])
def test_ordinary_handover_keeps_readiness_strict(status):
    request, sleep = Mock(), Mock()
    request.get.return_value.status = status
    with pytest.raises(ValueError, match='Readiness failed'):
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40,
                          previous_release='d' * 40, sleep=sleep)
    assert request.get.call_count == 1
    sleep.assert_not_called()


def healthy_response(path):
    if path == '/version':
        return SimpleNamespace(status=200, json=lambda: {'release': 'c' * 40})
    if path == '/health/ready':
        return SimpleNamespace(status=200)
    if path in ('/api/v1/unknown', '/assets/missing.js'):
        return SimpleNamespace(status=404)
    return SimpleNamespace(status=401, headers={'cache-control': 'no-store'})


@pytest.mark.parametrize('rolling,fault,status,retries', [
    (True, 'page', 502, True),
    (True, 'session', 503, True),
    (True, 'session', 504, True),
    (True, 'page', 403, False),
    (False, 'page', 502, False),
    (False, 'session', 504, False),
])
def test_rolling_browser_smoke_retries_only_observed_shell_gateways(
    monkeypatch, rolling, fault, status, retries,
):
    from playwright import sync_api

    origin = 'https://example.test'
    browser_api = MagicMock()
    browser = browser_api.__enter__.return_value.chromium.launch.return_value
    context = browser.new_context.return_value.__enter__.return_value
    context.request.get.side_effect = lambda path, **_: healthy_response(path)
    page = context.new_page.return_value
    attempted = False

    def navigate(path, **_):
        nonlocal attempted
        assert path == '/accounts'
        if attempted:
            return SimpleNamespace(status=200)
        attempted = True
        if fault == 'session':
            response_listener = next(call.args[1] for call in page.on.call_args_list if call.args[0] == 'response')
            response_listener(SimpleNamespace(
                url=origin + '/api/v1/session', status=status, request=SimpleNamespace(method='GET'),
            ))
            return SimpleNamespace(status=200)
        return SimpleNamespace(status=status)

    page.goto.side_effect = navigate
    sleep, emit = Mock(), Mock()
    monkeypatch.setattr(sync_api, 'sync_playwright', lambda: browser_api)
    monkeypatch.setattr(sync_api, 'expect', Mock())
    monkeypatch.setattr(smoke, 'named_probe', partial(smoke.named_probe, sleep=sleep))
    options = {'previous_release': 'd' * 40} if rolling else {}
    if retries:
        smoke.named_smoke(origin, 'c' * 40, emit=emit, **options)
        sleep.assert_called_once_with(3)
        assert page.goto.call_count == 2
        assert sum(c.args[0] == '/version' for c in context.request.get.call_args_list) == 2
        assert any(c.args[0]['status'] == 'rolling_public_shell_probe' and c.args[0]['http_status'] == status
                   for c in emit.call_args_list)
    else:
        with pytest.raises(smoke.NamedSmokeError, match=f'HTTP {status}'):
            smoke.named_smoke(origin, 'c' * 40, emit=emit, **options)
        sleep.assert_not_called()
        assert page.goto.call_count == 1


@pytest.mark.parametrize('path,status', [('/api/openapi.json', 502), ('/api/v1/session', 503),
                                       ('/assets/missing.js', 504)])
def test_maintenance_restarts_the_complete_http_boundary_after_gateway(path, status):
    request, sleep, emit, shell = Mock(), Mock(), Mock(), Mock(return_value=True)
    pending = True

    def get(observed, **_):
        nonlocal pending
        if observed == path and pending:
            pending = False
            return SimpleNamespace(status=status)
        return healthy_response(observed)

    request.get.side_effect = get
    smoke.named_probe(SimpleNamespace(request=request), 'c' * 40, previous_release='d' * 40,
                      maintenance=True, sleep=sleep, verify_shell=shell, emit=emit)
    sleep.assert_called_once_with(3)
    shell.assert_called_once()
    assert sum(c.args[0] == '/version' for c in request.get.call_args_list) == 2
    assert any(c.args[0]['path'] == path and c.args[0]['http_status'] == status for c in emit.call_args_list)
    assert all(set(c.args[0]) == {'status', 'path', 'http_status', 'attempt', 'elapsed_seconds'}
               for c in emit.call_args_list)


def test_maintenance_shell_gateway_uses_the_same_budget_and_rechecks_http():
    request, sleep = Mock(), Mock()
    request.get.side_effect = lambda path, **_: healthy_response(path)
    shell = Mock(side_effect=[False, True])
    smoke.named_probe(SimpleNamespace(request=request), 'c' * 40, previous_release='d' * 40,
                      maintenance=True, sleep=sleep, verify_shell=shell)
    sleep.assert_called_once_with(3)
    assert shell.call_count == 2
    assert sum(c.args[0] == '/version' for c in request.get.call_args_list) == 2


def test_maintenance_waits_past_five_minutes_but_obeys_one_ten_minute_deadline():
    clock = [0.0]
    request, emit = Mock(), Mock()

    def get(path, **_):
        if clock[0] < 480:
            return SimpleNamespace(status=502)
        return healthy_response(path)

    def sleep(seconds):
        clock[0] += seconds

    request.get.side_effect = get
    smoke.named_probe(SimpleNamespace(request=request), 'c' * 40, previous_release='d' * 40,
                      maintenance=True, sleep=sleep, monotonic=lambda: clock[0], emit=emit)
    assert clock[0] == 480
    assert emit.call_args_list[-1].args[0]['elapsed_seconds'] == 480

    clock[0] = 0

    def slow_gateway(path, **kwargs):
        clock[0] += kwargs['timeout'] / 1000
        return SimpleNamespace(status=502)

    request.get.side_effect = slow_gateway
    with pytest.raises(smoke.NamedSmokeError, match='10 minutes'):
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40, previous_release='d' * 40,
                          maintenance=True, sleep=sleep, monotonic=lambda: clock[0])
    assert clock[0] == 600


def test_shell_semantic_failure_does_not_retry():
    request, sleep = Mock(), Mock()
    request.get.side_effect = lambda path, **_: healthy_response(path)
    shell = Mock(side_effect=smoke.NamedSmokeError('Public sign-in shell check failed.'))
    with pytest.raises(smoke.NamedSmokeError, match='shell check'):
        smoke.named_probe(SimpleNamespace(request=request), 'c' * 40, previous_release='d' * 40,
                          maintenance=True, sleep=sleep, verify_shell=shell)
    sleep.assert_not_called()


def test_workflow_selects_mode_before_migration_and_keeps_alpha_secrets_out_of_named_step():
    def load(name):
        return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)
    job = load('commercial-promote.yml')['jobs']['deploy']
    assert 'TABULA_RASA_ACCESS_MODE' not in job['env']
    steps = job['steps']
    verify = next(i for i, step in enumerate(steps) if 'release verify' in step.get('run', ''))
    migrate = next(i for i, step in enumerate(steps) if 'database migrate' in step.get('run', ''))
    assert verify < migrate
    assert steps[verify]['env']['TABULA_RASA_ACCESS_MODE'] == '${{ vars.TABULA_RASA_ACCESS_MODE }}'
    named = next(step for step in steps if 'named-user boundary' in step.get('name', ''))
    alpha = next(step for step in steps if 'recover prior' in step.get('name', ''))
    assert named['if'] == "!inputs.automatic && vars.TABULA_RASA_ACCESS_MODE == 'named_user'"
    assert alpha['if'] == "!inputs.automatic && vars.TABULA_RASA_ACCESS_MODE == 'restricted_alpha'"
    automatic = next(step for step in steps if step.get('id') == 'automatic')
    assert automatic['if'] == 'inputs.automatic'
    assert not any('ALPHA' in key for key in automatic['env'])
    for step in (named, alpha):
        assert step['env']['TABULA_RASA_ACCESS_MODE'] == '${{ vars.TABULA_RASA_ACCESS_MODE }}'
    assert not any('ALPHA' in key for key in named['env'] | job['env'])


def test_verify_rejects_missing_mode_before_image_work(monkeypatch):
    monkeypatch.delenv('TABULA_RASA_ACCESS_MODE', raising=False)
    inspect = Mock()
    monkeypatch.setattr(release, 'image_metadata', inspect)
    monkeypatch.setattr('sys.argv', ['release', 'verify', '--image', IMAGE])
    with pytest.raises(SystemExit, match='configuration'):
        release.main()
    inspect.assert_not_called()
