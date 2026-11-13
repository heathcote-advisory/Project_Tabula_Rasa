"""Named-user promotion boundaries; all provider configuration is synthetic."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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
    check.assert_called_once_with(NAMED['TABULA_RASA_PUBLIC_ORIGIN'], 'c' * 40)
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
