"""Explicit acknowledgement, replay, updates and isolation on real PostgreSQL."""

from dataclasses import replace

import pytest
from sqlalchemy import text

from tabula_rasa.application.customer_policies import beta_candidate
from tabula_rasa.application.identity import IdentityError
from tabula_rasa.infrastructure.postgres.customer_policies import PostgresPolicyAcknowledgements
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError
from tests.postgres.test_identity import setup, verified
from tests.postgres.test_identity_http import client_for


def test_acknowledgements_are_explicit_immutable_and_personal(database):
    setup(database)
    client, auth, _ = client_for(database)
    _, alice = auth.sessions.issue(verified())
    _, bob = auth.sessions.issue(verified('bob'))
    policies = beta_candidate('heathcoteadvisoryllc@gmail.com', '2026-11-16')
    store = PostgresPolicyAcknowledgements(database)
    assert not store.acknowledged(alice, policies)
    with pytest.raises(IdentityError, match='conflict'):
        store.accept(alice, policies, '0' * 64)
    assert store.accept(alice, policies, policies.digest)
    assert store.accept(alice, policies, policies.digest)  # uncertain response replay
    assert PostgresPolicyAcknowledgements(database).acknowledged(alice, policies)
    assert not store.acknowledged(bob, policies)
    revised = replace(policies, privacy=policies.privacy + '\nMaterial update.')
    assert not store.acknowledged(alice, revised)
    assert store.accept(alice, revised, revised.digest)
    with database.transaction(read_only=True) as c:
        assert c.scalar(text('SELECT count(*) FROM customer_policy_acknowledgements')) == 2
    with pytest.raises(DatabaseError), database.transaction() as c:
        c.execute(text('DELETE FROM customer_policy_acknowledgements'))


def test_named_transport_csrf_stale_digest_and_existing_rights(database, monkeypatch):
    repo, alice, bob, a, b = setup(database)
    policies = beta_candidate('heathcoteadvisoryllc@gmail.com', '2026-11-16')
    monkeypatch.setenv('TABULA_RASA_LEGAL_APPROVED_SHA256', policies.digest)
    monkeypatch.setenv('TABULA_RASA_SUPPORT_EMAIL', policies.support_email)
    monkeypatch.setenv('TABULA_RASA_LEGAL_EFFECTIVE_DATE', policies.effective_date)
    client, auth, factory = client_for(database)
    raw, session = auth.sessions.issue(verified())
    with client:
        assert client.get('/api/v1/public/policies').json()['beta']
        assert client.get('/api/v1/policies/acknowledgement').status_code == 401
        client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
        client.cookies.set(auth.csrf_cookie, session.csrf_token, domain='127.0.0.1', path='/')
        headers = {'origin': auth.settings.public_origin, 'x-csrf-token': session.csrf_token}
        path = '/api/v1/policies/acknowledgement'
        assert client.get(path).json() == {'digest': policies.digest, 'required': True, 'acknowledged': False}
        assert client.post(path, json={'accepted': True, 'digest': policies.digest}).status_code == 403
        assert client.post(path, headers=headers, json={'accepted': False, 'digest': policies.digest}).status_code == 422
        assert client.post(path, headers=headers, json={'accepted': True, 'digest': '0' * 64}).status_code == 409
        # Existing identity and privacy/closure rights survive missing acknowledgement.
        assert client.get('/api/v1/session').status_code == 200
        assert client.get('/api/v1/customer-lifecycle').status_code == 200
        create = {'request_id': 'synthetic-policy-test', 'business_name': 'Synthetic', 'country': 'US', 'currency': 'USD'}
        assert client.post('/api/v1/businesses', headers=headers, json=create).status_code == 422
        from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository
        invitations = PostgresWorkspaceRepository(database)
        invitation = invitations.invite(bob.id, b, session.verified_email, 'read_only')
        respond = '/api/v1/invitations/respond'
        body = {'credential': invitation.credential, 'accept': True}
        assert client.post(respond, headers=headers, json=body).status_code == 422
        # Declining is never made conditional on agreeing to new terms.
        assert client.post(respond, headers=headers, json={**body, 'accept': False}).status_code == 200
        invitation = invitations.invite(bob.id, b, session.verified_email, 'read_only')
        body['credential'] = invitation.credential
        assert client.post(path, headers=headers, json={'accepted': True, 'digest': policies.digest}).json()['acknowledged']
        assert client.post(path, headers=headers, json={'accepted': True, 'digest': policies.digest}).status_code == 200
        assert client.post('/api/v1/businesses', headers=headers, json=create).status_code == 200
        assert client.post(respond, headers=headers, json=body).status_code == 200
        auth.sessions.revoke(raw)
        assert client.get(path).status_code == 401
    factory.assert_not_called()
