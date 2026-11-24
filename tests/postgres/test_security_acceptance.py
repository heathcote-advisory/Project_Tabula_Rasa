"""Real sessions and native PostgreSQL across the entire private API.

Only synthetic identities/records; no live provider, grant or operator action.
Viewer/bookkeeper are the canonical read_only/editor roles, not new roles.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import logging
from uuid import uuid4
from unittest.mock import Mock

from fastapi.routing import APIRoute, iter_route_contexts
import pytest

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.http.app import create_app
from tabula_rasa.http.authorization import ROUTE_POLICIES
from tests.http.test_authorization import ROUTES, path_for
from tests.postgres.test_authorization_http import named_client
from tests.postgres.test_identity import verified


PRIVATE = tuple(r for r in iter_route_contexts(create_app(schema_only=True).routes)
                if isinstance(r.original_route, APIRoute) and r.operation_id in ROUTE_POLICIES
                and ROUTE_POLICIES[r.operation_id].scope != 'public'
                and ROUTE_POLICIES[r.operation_id].action != 'logout')


def concrete_path(route, organization='foreign'):
    return (path_for(route).replace('{organization_id}', organization)
            .replace('{business_id}', 'foreign')
            .replace('{user_id}', 'unknown').replace('{invitation_id}', 'unknown')
            .replace('{request_id}', 'synthetic-request-123'))


def safe_denial(response, status, secrets=()):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-request-id']
    assert set(response.json()) == {'error', 'request_id'}
    for value in (*secrets, 'SELECT ', 'Traceback', 'postgresql://'):
        assert value not in response.text


@pytest.mark.parametrize('loss', ['anonymous', 'tampered', 'expired', 'revoked', 'disabled', 'outage'])
def test_every_private_family_denies_lost_identity_before_binding(database, loss, caplog, monkeypatch):
    monkeypatch.setenv('TABULA_RASA_TELEMETRY_SCOPE_KEY', 'synthetic-security-acceptance-key-32-chars')
    with named_client(database) as (client, auth, repo, identities, factory, _, headers):
        alice, bob, org, _ = identities
        raw = client.cookies.get(auth.session_cookie)
        expected = 401
        if loss == 'anonymous':
            client.cookies.clear()
        elif loss == 'tampered':
            client.cookies.set(auth.session_cookie, 'forged-private-cookie')
        elif loss == 'expired':
            auth.sessions.clock = lambda: verified().authenticated_at + timedelta(hours=13)
        elif loss == 'revoked':
            repo.revoke_user_sessions(alice.id)
        elif loss == 'disabled':
            repo.set_membership(org, bob.id, 'owner')
            repo.set_user_state(alice.id, 'disabled')
        else:
            auth.sessions.validate = Mock(side_effect=IdentityError('unavailable'))
            expected = 503
        with caplog.at_level(logging.INFO, logger='tabula_rasa.http'):
            for route in PRIVATE:
                result = client.request(next(iter(route.methods)), concrete_path(route), headers={
                    **headers, 'X-Correlation-ID': 'security-320', 'Authorization': 'Bearer private-token',
                }, json={'private_record': 'private-ledger-content'})
                safe_denial(result, expected, (raw, 'private-ledger-content', 'private-token'))
                event = json.loads(next(record.message for record in reversed(caplog.records)
                                        if record.name == 'tabula_rasa.http'))
                assert event['request_id'] == result.headers['x-request-id']
                assert len(event['correlation_id']) == 24 and event['correlation_id'] != 'security-320'
                assert 'security-320' not in event.values()
                assert event['security_event'] == 'identity_denied'
            for path in ('/api/docs', '/api/openapi.json'):
                safe_denial(client.get(path), expected, (raw,))
        assert all(value not in caplog.text for value in (raw, 'private-ledger-content', 'private-token',
                                                         'shared@example.test', 'forged-private-cookie'))
        factory.assert_not_called()


def test_provider_outage_cannot_create_session_or_replay_callback(database, caplog):
    with named_client(database) as (client, auth, _, _, factory, _, _):
        client.cookies.clear()
        auth.provider.authorization_url.return_value = 'https://id.example.test/authorize'
        auth.provider.verify.side_effect = IdentityError('unavailable')
        with caplog.at_level(logging.INFO, logger='tabula_rasa.http'):
            assert client.get('/api/v1/auth/login', follow_redirects=False).status_code == 303
            transaction = auth.provider.authorization_url.call_args.args[0]
            callback = dict(state=transaction.state, code='private-code-sentinel')
            result = client.get('/api/v1/auth/callback', params=callback, follow_redirects=False)
            assert result.status_code == 303
            assert result.headers['location'].startswith('/signed-out?reason=unavailable&category=token_validation&reference=')
            assert auth.session_cookie not in client.cookies
            assert client.get('/api/v1/auth/callback', params=callback,
                              follow_redirects=False).headers['location'].startswith('/signed-out?reason=login_failed&category=login_transaction&reference=')
            assert auth.provider.verify.call_count == 1
            safe_denial(client.get('/api/v1/entities/alpha/accounts'), 401)
        assert 'private-code-sentinel' not in caplog.text and transaction.state not in caplog.text
        factory.assert_not_called()


def test_all_unsafe_operations_keep_origin_and_csrf_before_effects(database):
    with named_client(database) as (client, _, _, _, factory, stores, headers):
        for route in PRIVATE:
            if route.methods == {'GET'}:
                continue
            for invalid in ({}, {**headers, 'Origin': 'null'}, {**headers, 'Origin': 'https://foreign.test'},
                            {**headers, 'X-CSRF-Token': 'wrong'}, {'X-CSRF-Token': headers['X-CSRF-Token']}):
                safe_denial(client.request(next(iter(route.methods)), concrete_path(route),
                                           headers=invalid, json={}), 403)
        factory.assert_not_called()
        assert not any(store.upload_calls for store in stores.values())


@pytest.mark.parametrize('role', ['owner', 'admin', 'accountant', 'editor', 'read_only'])
def test_current_role_matrix_across_every_entity_action(database, role, caplog, monkeypatch):
    monkeypatch.setenv('TABULA_RASA_TELEMETRY_SCOPE_KEY', 'synthetic-security-acceptance-key-32-chars')
    permitted = {
        'owner': {'read', 'write', 'asset_depreciate', 'import', 'export', 'connections_manage',
                  'period_close', 'period_unlock', 'entry_correct'},
        'admin': {'read', 'write', 'asset_depreciate', 'import', 'export', 'period_close', 'entry_correct'},
        'accountant': {'read', 'write', 'asset_depreciate', 'import', 'export', 'entry_correct'},
        'editor': {'read', 'write', 'asset_depreciate'}, 'read_only': {'read'},
    }
    with named_client(database) as (client, _, repo, identities, factory, stores, headers):
        alice, bob, org, _ = identities
        repo.set_membership(org, bob.id, 'owner')
        repo.set_membership(org, alice.id, role)
        assert client.get('/api/v1/entities/alpha/accounts').status_code == 200
        created = client.post('/api/v1/entities/alpha/accounts', headers=headers,
                              json={'name': 'Synthetic account', 'type': 'Bank'})
        assert created.status_code == (403 if role == 'read_only' else 201)
        factory.reset_mock()
        caplog.set_level(logging.INFO, logger='tabula_rasa.http')
        for route in ROUTES:
            action = ROUTE_POLICIES[route.operation_id].action
            if action not in permitted[role]:
                safe_denial(client.request(next(iter(route.methods)), path_for(route), headers={
                    **headers, 'X-Role': 'owner', 'X-Entity-ID': 'alpha',
                }, json={}), 403)
            denied = client.request(next(iter(route.methods)), path_for(route, 'beta'),
                                    headers={**headers, 'X-Correlation-ID': 'security-320-role'}, json={})
            safe_denial(denied, 403)
            event = json.loads(next(record.message for record in reversed(caplog.records)
                                    if record.name == 'tabula_rasa.http'))
            assert event['security_event'] == 'authorization_denied'
            assert event['failure_code'] == 'forbidden'
            assert event['request_id'] == denied.headers['x-request-id']
            assert len(event['correlation_id']) == 24 and event['correlation_id'] != 'security-320-role'
            assert 'security-320-role' not in event.values()
        assert 'shared@example.test' not in caplog.text and headers['X-CSRF-Token'] not in caplog.text
        factory.assert_not_called()
        assert not any(store.upload_calls for store in stores.values())


def test_overlapping_memberships_sessions_and_committed_revocation(database):
    with named_client(database) as (client, auth, repo, identities, factory, _, headers):
        alice, bob, a, b = identities
        repo.set_membership(b, alice.id, 'read_only')
        raw = client.cookies.get(auth.session_cookie)
        other_raw, _ = auth.sessions.issue(verified())
        auth.sessions.select(raw, 'alpha')
        auth.sessions.select(other_raw, 'alpha')
        repo.set_membership(a, bob.id, 'owner')
        initial = client.get('/api/v1/session').json()
        selected = client.post('/api/v1/session/entity', headers=headers, json={'entity_id': 'beta'})
        assert selected.status_code == 200 and selected.json()['entity']['role'] == 'read_only'
        assert selected.json()['entity']['id'] != initial['entity']['id']
        assert auth.sessions.validate(other_raw).selected.entity_id == 'alpha'
        assert client.post('/api/v1/entities/beta/accounts', headers=headers,
                           json={'name': 'Forbidden', 'type': 'Bank'}).status_code == 403
        # Two independent committed changes, then requests on both old sessions.
        with ThreadPoolExecutor(max_workers=2) as workers:
            removal = workers.submit(repo.set_membership, b, alice.id, 'read_only', active=False)
            demotion = workers.submit(repo.set_membership, a, alice.id, 'editor')
            removal.result(timeout=10)
            demotion.result(timeout=10)
        for credential in (raw, other_raw):
            current = auth.sessions.validate(credential)
            assert current.selected.entity_id == 'alpha' and current.selected.role == 'editor'
        repo.revoke_user_sessions(alice.id)
        factory.reset_mock()
        for credential in (raw, other_raw):
            client.cookies.set(auth.session_cookie, credential)
            safe_denial(client.get('/api/v1/entities/alpha/accounts'), 401)
        factory.assert_not_called()
        bob_raw, _ = auth.sessions.issue(verified('bob'))
        auth.sessions.select(bob_raw, 'beta')
        client.cookies.set(auth.session_cookie, bob_raw)
        assert client.get('/api/v1/entities/beta/accounts').status_code == 200


def test_http_invite_replay_escalation_last_owner_and_bootstrap_forgery(database, caplog):
    with named_client(database) as (client, auth, repo, identities, factory, _, headers):
        alice, bob, a, b = identities
        prefix = '/api/v1/workspaces/' + a
        members = client.get(prefix + '/members').json()['items']
        owner = next(row for row in members if row['user_id'] == alice.id)
        change = dict(role='admin', active=True, authorization_version=owner['authorization_version'])
        assert client.patch(prefix + '/members/' + alice.id, headers=headers, json=change).status_code == 422
        issued = client.post(prefix + '/invitations', headers=headers,
                             json={'email': 'shared@example.test', 'role': 'editor'})
        assert issued.status_code == 200
        credential = issued.json()['credential']
        raw, session = auth.sessions.issue(verified('bob'))
        client.cookies.set(auth.session_cookie, raw)
        headers = {**headers, 'X-CSRF-Token': session.csrf_token}
        with caplog.at_level(logging.INFO, logger='tabula_rasa.http'):
            accepted = client.post('/api/v1/invitations/respond', headers=headers,
                                   json={'credential': credential, 'accept': True})
            assert accepted.status_code == 200
            replay = client.post('/api/v1/invitations/respond', headers=headers,
                                 json={'credential': credential, 'accept': True})
            safe_denial(replay, 409, (credential,))
            safe_denial(client.post(prefix + '/invitations', headers=headers,
                                   json={'email': 'attacker@example.test', 'role': 'admin'}), 403)
            safe_denial(client.post(prefix + '/businesses', headers=headers, json={
                'request_id': uuid4().hex, 'workspace_name': 'Forged', 'business_name': 'Forged',
            }), 403)
        assert credential not in caplog.text and 'shared@example.test' not in caplog.text
        assert len(repo.active_memberships(bob.id)) == 2
        assert next(row['role'] for row in repo.active_memberships(bob.id) if row['organization_id'] == a) == 'editor'
        assert repo.active_memberships(alice.id)[0]['role'] == 'owner'
        assert client.get('/api/v1/session').json()['entity']['organization_id'] == b
        factory.assert_not_called()
