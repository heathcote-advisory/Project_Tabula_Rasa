import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.http.app import create_app
from tabula_rasa.http.composition import PostgresRuntime
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.identity import NamedIdentity
from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository
from tests.postgres.test_identity import setup, verified
from tests.postgres._support import entity


def client_for(database):
    settings = HttpSettings('test', 'http://127.0.0.1', access_mode='named_user')
    sessions = PostgresSessionRepository(database, 'test', 'a' * 64)
    sessions.configure()
    provider = Mock()
    auth = NamedIdentity(settings, sessions, provider)
    factory = Mock(side_effect=AssertionError('No private service composition allowed'))
    runtime = PostgresRuntime(database, auth, service_factory=factory, identity=auth)
    app = create_app(settings=settings, runtime_factory=lambda _: runtime)
    return TestClient(app, base_url=settings.public_origin), auth, factory



def test_real_pool_exhaustion_labels_named_session_and_readiness(database, caplog, monkeypatch):
    setup(database)
    # A short fixture timeout makes the real checkout failure deterministic.
    pool_engine = create_engine(database.engine.url, pool_size=2, max_overflow=0,
                                pool_timeout=0.1, pool_pre_ping=True, hide_parameters=True)
    original_engine = database.engine
    database.engine = pool_engine
    caplog.set_level("INFO", logger="tabula_rasa.http")
    monkeypatch.setenv('TABULA_RASA_TELEMETRY_SCOPE_KEY', 'synthetic-identity-telemetry-key-32-chars')
    try:
        client, auth, factory = client_for(database)
        raw, session = auth.sessions.issue(verified())
        with client:
            client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
            client.cookies.set(auth.csrf_cookie, session.csrf_token, domain='127.0.0.1', path='/')
            assert client.get('/api/v1/session').status_code == 200
            assert client.get('/health/ready').status_code == 200
            with pool_engine.connect(), pool_engine.connect():
                denied = client.get('/api/v1/session')
                assert denied.status_code == 503 and denied.json()['error']['code'] == 'unavailable'
                readiness = client.get('/health/ready')
                assert readiness.status_code == 503 and readiness.json() == {'status': 'unavailable'}
                login = client.get('/api/v1/auth/login', follow_redirects=False)
                assert login.headers['location'].startswith('/signed-out?reason=unavailable')
                logout = client.post('/api/v1/auth/logout', headers={
                    'origin': auth.settings.public_origin, 'x-csrf-token': session.csrf_token,
                })
                assert logout.status_code == 503
                assert auth.session_cookie not in client.cookies and auth.csrf_cookie not in client.cookies
            assert client.get('/health/ready').status_code == 200
            client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
            assert client.get('/api/v1/session').status_code == 200
            auth.provider.authorization_url.assert_not_called()

            with monkeypatch.context() as unavailable:
                unavailable.setattr(pool_engine, 'connect', Mock(side_effect=OperationalError(None, None, OSError())))
                assert client.get('/api/v1/session').status_code == 503
            auth.provider.authorization_url.side_effect = IdentityError('unavailable')
            login = client.get('/api/v1/auth/login', follow_redirects=False)
            assert login.headers['location'].startswith('/signed-out?reason=unavailable')
        factory.assert_not_called()
    finally:
        pool_engine.dispose()
        database.engine = original_engine

    events = [json.loads(record.message) for record in caplog.records
              if record.name == 'tabula_rasa.http' and '"http_request"' in record.message]
    failures = [event for event in events if event['failure_code'] == 'unavailable']
    assert [(event['operation'], event['status']) for event in failures] == [
        ('get_session', 503), ('readiness', 503), ('begin_login', 303), ('logout', 503),
        ('get_session', 503), ('begin_login', 303),
    ]
    assert [(event['provider'], event['provider_status']) for event in failures] == [
        ('postgresql', 'exhausted'), ('postgresql', 'exhausted'),
        ('postgresql', 'exhausted'), ('postgresql', 'exhausted'),
        ('postgresql', 'unavailable'), ('identity', 'unavailable'),
    ]
    assert raw not in caplog.text and session.csrf_token not in caplog.text


def test_logout_bootstrap_during_outage_preserves_origin_csrf_and_cookie_isolation():
    settings = HttpSettings('test', 'http://127.0.0.1', access_mode='named_user')
    auth = NamedIdentity(settings, Mock(), Mock())
    auth.sessions.validate.side_effect = IdentityError('unavailable')
    auth.sessions.revoke.side_effect = IdentityError('unavailable')
    runtime = Mock()
    runtime.identity = auth
    runtime.resolve_context = auth
    with TestClient(create_app(settings=settings, runtime_factory=lambda _: runtime), base_url=settings.public_origin) as client:
        for csrf in ('alice-logout', 'bob-logout'):
            client.cookies.set(auth.session_cookie, 'synthetic-session', domain='127.0.0.1', path='/')
            client.cookies.set(auth.csrf_cookie, csrf, domain='127.0.0.1', path='/')
            for headers in ({}, {'x-tabula-logout': '1', 'origin': 'https://evil.test'},
                            {'x-tabula-logout': '1', 'sec-fetch-site': 'cross-site'}):
                assert client.get('/api/v1/auth/logout-context', headers=headers).status_code == 403
            response = client.get('/api/v1/auth/logout-context', headers={'x-tabula-logout': '1', 'sec-fetch-site': 'same-origin'})
            assert response.status_code == 200 and response.json() == {'csrf_token': csrf}
            assert response.headers['cache-control'] == 'no-store'
            assert 'access-control-allow-origin' not in response.headers
            auth.sessions.validate.assert_not_called()
            auth.sessions.revoke.assert_not_called()
            headers = {'origin': settings.public_origin, 'x-csrf-token': csrf}
            assert client.post('/api/v1/auth/logout', headers={**headers, 'origin': 'https://evil.test'}).status_code == 403
            assert client.post('/api/v1/auth/logout', headers={**headers, 'x-csrf-token': 'wrong'}).status_code == 403
            assert client.post('/api/v1/auth/logout', headers=headers).status_code == 503
            assert auth.session_cookie not in client.cookies and auth.csrf_cookie not in client.cookies
            auth.sessions.revoke.reset_mock()
        assert client.get('/api/v1/auth/logout-context', headers={'x-tabula-logout': '1'}).status_code == 401
        runtime.services.assert_not_called()


@pytest.mark.parametrize('entry', ['login', 'register'])
@pytest.mark.parametrize('return_path', ['/', '/onboarding', '/invitations'])
def test_login_callback_cookie_contract_and_no_entity_setup(database, return_path, caplog, monkeypatch, entry):
    monkeypatch.setenv('TABULA_RASA_TELEMETRY_SCOPE_KEY', 'synthetic-identity-telemetry-key-32-chars')
    caplog.set_level('INFO', logger='tabula_rasa.http')
    client, auth, factory = client_for(database)
    auth.provider.authorization_url.return_value = 'https://id.example.test/authorize'
    auth.provider.verify.return_value = verified()
    with client:
        client.cookies.set(auth.session_cookie, 'x' * 43, domain='127.0.0.1', path='/')
        delayed_denial = client.get('/api/v1/session')
        assert delayed_denial.status_code == 401
        for bad_path in ('https://foreign.test', '//foreign.test', '/invitations?credential=private', '/invitations/../other'):
            assert client.get('/api/v1/auth/' + entry, params={'return_path': bad_path}).status_code == 403
        start = client.get('/api/v1/auth/' + entry, params={'return_path': return_path}, follow_redirects=False)
        assert start.status_code == 303
        transaction = auth.provider.authorization_url.call_args.args[0]
        assert client.get('/api/v1/session').status_code == 401
        assert auth.login_cookie in client.cookies  # Late private denial cannot erase login.
        result = client.get('/api/v1/auth/callback', params={'code': 'synthetic-code', 'state': transaction.state}, follow_redirects=False)
        assert result.status_code == 303 and result.headers['location'] == return_path
        cookies = result.headers.get_list('set-cookie')
        assert all('HttpOnly' in cookie and 'Path=/' in cookie and 'SameSite=lax' in cookie and 'Domain=' not in cookie for cookie in cookies)
        fresh_cookie = client.cookies.get(auth.session_cookie)
        fresh_csrf = client.cookies.get(auth.csrf_cookie)
        # Deliver an old private 401 after the callback, as a browser would.
        # Cookie application is by name, regardless of the request's credential.
        client.cookies.extract_cookies(delayed_denial)
        assert client.cookies.get(auth.session_cookie) == fresh_cookie
        assert client.cookies.get(auth.csrf_cookie) == fresh_csrf
        assert not delayed_denial.headers.get_list('set-cookie')
        session = client.get('/api/v1/session').json()
        assert session['state'] == 'needs_setup' and session['entity'] is None and not session['capabilities']
        assert client.get('/api/v1/entities/alpha/accounts').status_code == 403
        replay = client.get('/api/v1/auth/callback', params={'code': 'synthetic-code', 'state': transaction.state}, follow_redirects=False)
        assert '/signed-out?reason=login_failed&category=login_transaction&reference=' in replay.headers['location']
        headers = {'origin': auth.settings.public_origin, 'x-csrf-token': session['csrf_token']}
        assert client.post('/api/v1/auth/activity', headers=headers).status_code == 204
        assert client.post('/api/v1/auth/logout', headers=headers).status_code == 204
        assert client.get('/api/v1/session').status_code == 401
        security = [json.loads(record.message) for record in caplog.records
                    if record.name == 'tabula_rasa.http' and '"http_request"' in record.message]
        succeeded = next(event for event in security if event['security_event'] == 'login_succeeded')
        failed = next(event for event in security if event['security_event'] == 'login_failed')
        assert succeeded['actor_ref'] and succeeded['outcome'] == 'success'
        assert failed['outcome'] == 'failure' and failed['status'] == 303
        assert any(event['security_event'] == 'logout_succeeded' for event in security)
        assert all(value not in caplog.text for value in ('synthetic-code', fresh_cookie, fresh_csrf))
        factory.assert_not_called()


@pytest.mark.parametrize('origin', ['https://named.example.test', 'HTTPS://named.example.test'])
def test_hosted_cookie_attributes_and_injected_fixture_rejection(origin):
    from tabula_rasa.http.context import TrustedDevelopmentContext
    from starlette.responses import Response
    settings = HttpSettings('staging', origin, access_mode='named_user')
    auth = NamedIdentity(settings, Mock(), Mock())
    response = Response()
    auth.set_cookie(response, auth.session_cookie, 'synthetic', 43200)
    cookie = response.headers['set-cookie']
    assert cookie.startswith('__Host-tabula_session=') and 'Secure' in cookie and 'HttpOnly' in cookie
    assert 'Path=/' in cookie and 'SameSite=lax' in cookie and 'Domain=' not in cookie
    for values in ({'trusted_entity_id': 'alpha'}, {'alpha_username': 'operator'}, {'alpha_password_hash': 'secret'}):
        with pytest.raises(ValueError):
            HttpSettings('staging', 'https://named.example.test', access_mode='named_user', **values)
    with pytest.raises(ValueError):
        HttpSettings('development', 'http://public.example.test', access_mode='named_user')
    synthetic = TrustedDevelopmentContext('alpha', 'csrf')
    runtime = Mock()
    runtime.identity = None
    runtime.resolve_context = lambda request: synthetic.context
    with TestClient(create_app(settings=settings, runtime_factory=lambda _: runtime), base_url=settings.public_origin) as client:
        assert client.get('/api/v1/session').status_code == 403
        assert client.get('/api/v1/entities/alpha/accounts').status_code == 403
        runtime.services.assert_not_called()


def test_read_only_identity_management_scope_denial_and_outage_logout(database):
    repo, alice, bob, a, _ = setup(database)
    repo.set_membership(a, bob.id, 'owner')
    repo.set_membership(a, alice.id, 'read_only')
    with database.transaction() as connection:
        entity(connection, 'alpha-extra')
    extra = repo.create_organization(bob.id, 'Independent')
    repo.bind_entity(extra, 'alpha-extra')
    repo.set_membership(extra, alice.id, 'read_only')
    client, auth, factory = client_for(database)
    raw, session = auth.sessions.issue(verified())
    auth.sessions.select(raw, 'alpha')
    with client:
        client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
        client.cookies.set(auth.csrf_cookie, session.csrf_token, domain='127.0.0.1', path='/')
        info = client.get('/api/v1/session').json()
        assert info['entity']['id'] == 'alpha' and info['capabilities'] == ['bookkeeping:read']
        headers = {'origin': auth.settings.public_origin, 'x-csrf-token': info['csrf_token']}
        assert client.post('/api/v1/session/entity', json={'entity_id': 'beta'}, headers=headers).status_code == 403
        selected = client.post('/api/v1/session/entity', json={'entity_id': 'alpha-extra'}, headers=headers)
        assert selected.status_code == 200 and selected.json()['entity']['id'] == 'alpha-extra'
        assert selected.json()['capabilities'] == ['bookkeeping:read']
        assert client.post('/api/v1/auth/logout', headers={**headers, 'origin': 'https://evil.test'}).status_code == 403
        auth.sessions.revoke = Mock(side_effect=IdentityError('unavailable'))
        assert client.post('/api/v1/auth/logout', headers=headers).status_code == 503
        assert auth.session_cookie not in client.cookies
        assert auth.sessions.validate(raw).user_id == alice.id  # No false server-revocation success.
        factory.assert_not_called()


@pytest.mark.parametrize('code,status', [('unauthorized', 401), ('unavailable', 503)])
def test_middleware_identity_denial_uses_cookie_and_event_handler(code, status, caplog):
    import json
    import logging

    settings = HttpSettings('test', 'http://127.0.0.1', access_mode='named_user')
    auth = NamedIdentity(settings, Mock(), Mock())
    runtime = Mock()
    runtime.identity = auth
    auth.sessions.validate.side_effect = IdentityError(code)
    runtime.resolve_context = auth
    app = create_app(settings=settings, runtime_factory=lambda _: runtime)
    with TestClient(app, base_url=settings.public_origin) as client:
        client.cookies.set(auth.session_cookie, 'synthetic-session', domain='127.0.0.1', path='/')
        client.cookies.set(auth.csrf_cookie, 'synthetic-csrf', domain='127.0.0.1', path='/')
        client.cookies.set(auth.login_cookie, 'synthetic-login', domain='127.0.0.1', path='/')
        with caplog.at_level(logging.INFO, logger='tabula_rasa.http'):
            response = client.get('/api/docs')
        assert response.status_code == status
        assert auth.session_cookie in client.cookies
        assert auth.csrf_cookie in client.cookies
        assert not response.headers.get_list('set-cookie')
        assert auth.login_cookie in client.cookies
        event = next(json.loads(record.message) for record in caplog.records
                     if record.name == 'tabula_rasa.http' and '"http_request"' in record.message)
        assert event['security_event'] == 'identity_denied'
        assert 'synthetic-session' not in caplog.text
        runtime.services.assert_not_called()


def test_late_denial_cannot_erase_newer_browser_login():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    settings = HttpSettings('test', 'http://127.0.0.1', access_mode='named_user')
    auth = NamedIdentity(settings, Mock(), Mock())
    started, finish = Event(), Event()
    def old_validation(credential, *, foreground=False):
        assert credential == 'old-session' and not foreground
        started.set()
        assert finish.wait(5)
        raise IdentityError('unauthorized')
    auth.sessions.validate.side_effect = old_validation
    runtime = Mock(identity=auth, resolve_context=auth)
    with TestClient(create_app(settings=settings, runtime_factory=lambda _: runtime), base_url=settings.public_origin) as client:
        client.cookies.set(auth.session_cookie, 'old-session', domain='127.0.0.1', path='/')
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(client.get, '/api/docs')
            try:
                assert started.wait(5)
                # Model shared cookies just issued by another tab's successful callback.
                client.cookies.set(auth.session_cookie, 'new-session', domain='127.0.0.1', path='/')
                client.cookies.set(auth.csrf_cookie, 'new-csrf', domain='127.0.0.1', path='/')
            finally:
                finish.set()
            response = pending.result(timeout=5)
        assert response.status_code == 401 and 'set-cookie' not in response.headers
        assert client.cookies.get(auth.session_cookie) == 'new-session'
        assert client.cookies.get(auth.csrf_cookie) == 'new-csrf'
        runtime.services.assert_not_called()


def test_provider_outage_redirect_and_session_revocation_have_truthful_telemetry(database, caplog, monkeypatch):
    import json
    monkeypatch.setenv('TABULA_RASA_TELEMETRY_SCOPE_KEY', 'synthetic-identity-telemetry-key-32-chars')
    caplog.set_level('INFO', logger='tabula_rasa.http')
    setup(database)
    client, auth, _ = client_for(database)
    auth.provider.authorization_url.return_value = 'https://id.example.test/authorize'
    auth.provider.verify.side_effect = IdentityError('unavailable')
    with client:
        assert client.get('/api/v1/auth/login', follow_redirects=False).status_code == 303
        transaction = auth.provider.authorization_url.call_args.args[0]
        failed = client.get('/api/v1/auth/callback',
            params={'state': transaction.state, 'code': 'private-provider-code'}, follow_redirects=False)
        assert failed.status_code == 303
        event = json.loads(caplog.records[-1].message)
        assert event['security_event'] == 'login_failed' and event['failure_code'] == 'unavailable'
        assert event['outcome'] == 'failure' and event['level'] == 'error'
        assert event['provider'] == 'identity' and event['provider_status'] == 'unavailable'
        raw, session = auth.sessions.issue(verified())
        client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
        response = client.post('/api/v1/auth/revoke-all',
            headers={'origin': auth.settings.public_origin, 'x-csrf-token': session.csrf_token})
        assert response.status_code == 204
        event = json.loads(caplog.records[-1].message)
        assert event['security_event'] == 'user_sessions_revoked' and event['actor_ref']
        assert event['outcome'] == 'success'
        assert client.get('/api/v1/session').status_code == 401
        assert all(value not in caplog.text for value in ('private-provider-code', raw, session.csrf_token))
