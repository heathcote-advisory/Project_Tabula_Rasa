from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tabula_rasa.http.errors import TransportError
from tabula_rasa.http.migrated import MigratedProfileServices
from tabula_rasa.http.composition import PostgresRuntime, open_migrated_runtime
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.context import RequestContext
from tabula_rasa.http.identity import NamedIdentity


def named_resolver(settings, database):
    from sqlalchemy.engine import make_url
    database.engine.url = make_url('postgresql+psycopg://test@127.0.0.1/test')
    return NamedIdentity(settings, SimpleNamespace(database=database, environment=settings.environment), Mock())


def test_migrated_binding_enters_existing_cutover_and_document_composition(monkeypatch):
    services = object()
    factory = Mock(return_value=SimpleNamespace(services=services))
    monkeypatch.setattr("tabula_rasa.composition.create_application", factory)
    database = object()
    secrets = {"bookkeeping_backends": {"profile-alpha": {
        "backend": "postgres", "entity_id": "entity-alpha", "mode": "rehearsal",
    }}}
    binding = MigratedProfileServices(database, entity_profiles={"entity-alpha": "profile-alpha"}, secrets=secrets)
    assert binding(SimpleNamespace(entity_id="entity-alpha")) is services
    factory.assert_called_once_with("profile-alpha", secrets, database=database)
    with pytest.raises(TransportError):
        binding(SimpleNamespace(entity_id="entity-beta"))
    assert factory.call_count == 1


def test_mismatched_server_binding_cannot_return_another_profiles_services(monkeypatch):
    factory = Mock()
    monkeypatch.setattr("tabula_rasa.composition.create_application", factory)
    binding = MigratedProfileServices(object(), entity_profiles={"alpha": "profile-beta"},
        secrets={"bookkeeping_backends": {"profile-beta": {"backend": "postgres", "entity_id": "beta"}}})
    with pytest.raises(TransportError):
        binding(SimpleNamespace(entity_id="alpha"))
    factory.assert_not_called()


def test_synthetic_context_cannot_bind_a_migrated_profile(monkeypatch):
    factory = Mock()
    monkeypatch.setattr("tabula_rasa.composition.create_application", factory)
    binding = MigratedProfileServices(None, entity_profiles={"alpha": "alpha"}, secrets={})
    with pytest.raises(TransportError, match="forbidden"):
        binding(SimpleNamespace(entity_id="alpha", trusted_development=True))
    factory.assert_not_called()


@pytest.mark.parametrize("mapping,backends", [
    ({}, {}),
    ({"alpha": "profile", "beta": "profile"}, {}),
    ({"alpha": ""}, {}),
    ({"alpha": "profile"}, None),
    ({"alpha": "profile"}, {"profile": None}),
    ({"alpha": "profile"}, {"profile": {"backend": "maintenance"}}),
])
def test_invalid_server_bindings_fail_before_provider_composition(monkeypatch, mapping, backends):
    factory = Mock()
    monkeypatch.setattr("tabula_rasa.composition.create_application", factory)
    binding = MigratedProfileServices(None, entity_profiles=mapping, secrets={"bookkeeping_backends": backends})
    with pytest.raises(TransportError):
        binding.validate()
    factory.assert_not_called()


@pytest.mark.parametrize("resolver,fixture", [(None, None), (lambda request: None, "alpha-synthetic")])
def test_migrated_factory_requires_authorization_and_refuses_fixture_option(monkeypatch, resolver, fixture):
    database = Mock()
    monkeypatch.setattr("tabula_rasa.http.composition.Database", database)
    settings = HttpSettings("test", "http://testserver", trusted_entity_id=fixture)
    with pytest.raises(RuntimeError, match="authorization resolver"):
        open_migrated_runtime(settings, resolve_context=resolver, entity_profiles={}, profile_secrets={})
    database.assert_not_called()


@pytest.mark.parametrize("failure", ["binding", "schema", "allocation"])
def test_migrated_startup_disposes_pool_and_does_not_retain_private_errors(monkeypatch, failure):
    database = Mock()
    database.health.return_value = {"connected": True, "revision": "unsupported"}
    allocate = Mock(return_value=database)
    if failure == "allocation":
        allocate.side_effect = ValueError("private synthetic connection")
    monkeypatch.setattr("tabula_rasa.http.composition.Database", allocate)
    monkeypatch.setattr(MigratedProfileServices, "validate", Mock(
        side_effect=ValueError("private synthetic binding") if failure == "binding" else None))
    settings = HttpSettings("production", "https://owner.example.test", access_mode='named_user')
    resolver = named_resolver(settings, database)
    with pytest.raises(RuntimeError, match="Migrated HTTP runtime startup failed") as caught:
        open_migrated_runtime(settings,
            resolve_context=resolver, entity_profiles={"alpha": "alpha"},
            profile_secrets={}, database_settings=object())
    assert caught.value.__context__ is None
    assert "private" not in str(caught.value)
    assert database.dispose.call_count == (0 if failure == "allocation" else 1)


def test_migrated_named_identity_requires_explicit_access_mode_before_allocation(monkeypatch):
    allocate = Mock()
    monkeypatch.setattr("tabula_rasa.http.composition.Database", allocate)
    settings = HttpSettings("test", "http://127.0.0.1")
    resolver = NamedIdentity(settings, Mock(), Mock())
    with pytest.raises(RuntimeError, match="explicit named-user access mode"):
        open_migrated_runtime(settings, resolve_context=resolver, entity_profiles={}, profile_secrets={})
    allocate.assert_not_called()


def test_shared_production_factory_validates_all_bindings_and_preserves_request_scopes(monkeypatch):
    from tabula_rasa.infrastructure.postgres.migrations import head_revision

    database = Mock()
    database.health.return_value = {"connected": True, "revision": head_revision()}
    monkeypatch.setattr("tabula_rasa.http.composition.Database", Mock(return_value=database))
    services = {profile: object() for profile in ("alpha", "beta")}
    binding = Mock(side_effect=lambda context: services[context.entity_id])
    factory = Mock(return_value=binding)
    monkeypatch.setattr("tabula_rasa.http.migrated.MigratedProfileServices", factory)
    secrets = {"bookkeeping_backends": {profile: {
        "backend": "postgres", "entity_id": profile, "mode": "rehearsal",
    } for profile in services}}
    settings = HttpSettings("production", "https://owner.example.test", access_mode='named_user')
    resolver = named_resolver(settings, database)
    runtime = open_migrated_runtime(settings,
        resolve_context=resolver, entity_profiles={profile: profile for profile in services},
        profile_secrets=secrets, database_settings=object())
    binding.validate.assert_called_once()
    assert factory.call_args.kwargs['connections'] is runtime.connections
    assert runtime.resolve_context is resolver and runtime.ready()
    assert runtime.identity is resolver
    from tabula_rasa.application.identity import IdentityError
    bootstrap = Mock()
    monkeypatch.setattr(runtime.workspaces.repository, 'bootstrap', bootstrap)
    for profile in services:
        context = RequestContext("actor", "org", profile, frozenset({"bookkeeping:read"}), "session", "csrf")
        assert runtime.services(context) is services[profile]
        binding.assert_called_with(context)
        for organization_id in (None, 'organization-' + profile):
            with pytest.raises(IdentityError, match='forbidden'):
                runtime.workspaces.bootstrap(SimpleNamespace(user_id=profile), 'request-' + profile,
                                             'Workspace', 'Business', organization_id)
    for profile in services:
        with pytest.raises(IdentityError, match='forbidden'):
            runtime.workspaces.create_business(SimpleNamespace(user_id=profile), 'request-' + profile, 'Business')
    bootstrap.assert_not_called()
    members = Mock(return_value=[{'user_id': 'alpha'}])
    monkeypatch.setattr(runtime.workspaces.repository, 'members', members)
    assert runtime.workspaces.members(SimpleNamespace(user_id='alpha'), 'org') == [{'user_id': 'alpha'}]
    runtime.close()
    database.dispose.assert_called_once()


def test_native_workspace_bootstrap_remains_available_for_independent_users(monkeypatch):
    runtime = PostgresRuntime(Mock(), Mock(), service_factory=Mock(), identity=Mock())
    bootstrap = Mock(side_effect=lambda actor, *args, **kwargs: {'state': 'ready', 'entity_id': actor})
    monkeypatch.setattr(runtime.workspaces.repository, 'bootstrap', bootstrap)
    for actor in ('alpha', 'beta'):
        assert runtime.workspaces.bootstrap(SimpleNamespace(user_id=actor, verified_email=None), 'request-' + actor,
                                             'Workspace', 'Business') == {'state': 'ready', 'entity_id': actor}
        bootstrap.assert_called_with(actor, 'request-' + actor, 'Workspace', 'Business', None, country='US', currency='USD', verified=False)


@pytest.mark.parametrize('mismatch', ['database', 'environment'])
def test_named_control_plane_must_match_ledger_before_private_binding(monkeypatch, mismatch):
    from sqlalchemy.engine import make_url
    database, identity_database = Mock(), Mock()
    database.engine.url = make_url('postgresql+psycopg://test@127.0.0.1/test')
    settings = HttpSettings('production', 'https://owner.example.test', access_mode='named_user')
    resolver = named_resolver(settings, identity_database)
    if mismatch == 'database':
        identity_database.engine.url = database.engine.url.set(database='different')
    else:
        resolver.sessions.environment = 'staging'
    monkeypatch.setattr('tabula_rasa.http.composition.Database', Mock(return_value=database))
    binding = Mock()
    monkeypatch.setattr('tabula_rasa.http.migrated.MigratedProfileServices', binding)
    with pytest.raises(RuntimeError, match='startup failed'):
        open_migrated_runtime(settings, resolve_context=resolver,
            entity_profiles={'alpha': 'alpha'}, profile_secrets={}, database_settings=object())
    binding.assert_not_called()
    database.dispose.assert_called_once()


def test_production_cannot_bind_secrets_with_an_arbitrary_resolver(monkeypatch):
    database, binding = Mock(), Mock()
    monkeypatch.setattr('tabula_rasa.http.composition.Database', database)
    monkeypatch.setattr('tabula_rasa.http.migrated.MigratedProfileServices', binding)
    with pytest.raises(RuntimeError, match='authorization resolver'):
        open_migrated_runtime(HttpSettings('production', 'https://owner.example.test', access_mode='named_user'),
            resolve_context=lambda _: RequestContext('actor', 'org', 'alpha', frozenset({'bookkeeping:read'}), 'scope', 'csrf'),
            entity_profiles={'alpha': 'alpha'}, profile_secrets={})
    database.assert_not_called()
    binding.assert_not_called()


@pytest.mark.parametrize('drive', [None, {'receipt_folder': None}])
def test_named_binding_preserves_empty_document_scope_for_optional_legacy_config(monkeypatch, drive):
    from contextlib import nullcontext
    from tabula_rasa.migration.model import Binding
    connection = Mock()
    connection.execute.return_value.mappings.return_value.first.return_value = {
        'profile_key': 'alpha', 'organization_id': 'org'}
    monkeypatch.setattr('tabula_rasa.infrastructure.postgres.control_plane.identity_operation',
                        lambda *args, **kwargs: nullcontext(connection))
    controller = Mock()
    monkeypatch.setattr('tabula_rasa.infrastructure.postgres.cutover.PostgresCutoverController',
                        Mock(return_value=controller))
    config = {'business_profiles': {'alpha': {'name': 'Alpha', 'spreadsheet_id': 'workbook-alpha',
              'google_drive': drive}},
              'bookkeeping_backends': {'alpha': {'backend': 'postgres', 'entity_id': 'entity-alpha', 'mode': 'rehearsal'}}}
    binding = MigratedProfileServices(Mock(), entity_profiles={'entity-alpha': 'alpha'}, secrets=config,
                                      connections=Mock())
    binding.validate()
    controller.require_postgres.assert_called_once_with(Binding('alpha', 'workbook-alpha', 'entity-alpha'),
                                                        mode='rehearsal', document_scope='')
    binding.connections.validate_migrated_binding.assert_called_once_with(
        organization_id='org', entity_id='entity-alpha', profile_key='alpha', scope_key='')


def test_named_startup_checks_each_profile_grant_and_stops_on_failure(monkeypatch):
    from tabula_rasa.application.identity import IdentityError
    manager = Mock()
    binding = MigratedProfileServices(Mock(), entity_profiles={'alpha': 'alpha', 'beta': 'beta'},
                                      secrets={}, connections=manager)
    monkeypatch.setattr(binding, '_named_binding', lambda entity: ('org-' + entity, entity, 'folder-' + entity))
    binding.validate()
    assert manager.validate_migrated_binding.call_count == 2
    assert manager.validate_migrated_binding.call_args.kwargs == dict(
        organization_id='org-beta', entity_id='beta', profile_key='beta', scope_key='folder-beta')
    manager.validate_migrated_binding.side_effect = IdentityError('unavailable')
    with pytest.raises(IdentityError, match='unavailable'):
        binding.validate()
