"""Native lifecycle/atomicity and HTTP tests, using synthetic identities only."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.application.workspaces import WorkspaceService
from tabula_rasa.errors import StorageError
from tabula_rasa.infrastructure.postgres.control_plane import PostgresIdentityRepository
from tabula_rasa.infrastructure.postgres.reference_data import PostgresLedgerSetupRepository
from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository
from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository
from tests.postgres.test_identity import verified
from tests.postgres.test_identity_http import client_for


def approve_internal(database, *users):
    with database.transaction() as connection:
        if connection.scalar(text("SELECT to_regclass('beta_internal_users')")) is None:
            return
        for user in users:
            connection.execute(text("INSERT INTO beta_internal_users(user_id,approval_reference) VALUES (:user,'synthetic-internal') ON CONFLICT DO NOTHING"), {'user':user})


def context(database, subject='alice', email=None):
    sessions = PostgresSessionRepository(database, 'test', 'a' * 64)
    sessions.configure()
    identity = replace(verified(subject), email=email or subject + '@example.test')
    raw, session = sessions.issue(identity)
    approve_internal(database, session.user_id)
    return sessions, raw, session


def create(service, session, name='Workspace'):
    return service.bootstrap(session, uuid4().hex, name, name + ' business')


def test_bootstrap_atomic_retry_restart_multiple_orgs_and_isolation(database, monkeypatch):
    sessions, raw, alice = context(database)
    _, _, bob = context(database, 'bob')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    key = uuid4().hex
    assert service.bootstrap_status(alice, key)['state'] == 'not_started'
    original = PostgresLedgerSetupRepository.ensure_defaults
    def fail_after_defaults(repository):
        original(repository)
        raise StorageError(code='unavailable')
    monkeypatch.setattr(PostgresLedgerSetupRepository, 'ensure_defaults', fail_after_defaults)
    with pytest.raises(StorageError):
        service.create_business(alice, key, 'First business')
    with database.transaction(read_only=True) as connection:
        for table in ('organizations', 'memberships', 'organization_entities', 'business_entities', 'accounts', 'categories', 'workspace_bootstraps'):
            assert connection.scalar(text(f'SELECT count(*) FROM {table}')) == 0
    monkeypatch.setattr(PostgresLedgerSetupRepository, 'ensure_defaults', original)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: service.create_business(alice, key, 'First business'), range(2)))
    assert results[0] == results[1]
    database.dispose()  # Cold pool/repository, persisted receipt survives.
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    assert service.bootstrap_status(alice, key) == results[0]
    assert service.create_business(alice, key, 'First business') == results[0]
    with pytest.raises(IdentityError, match='conflict'):
        service.bootstrap(alice, key, 'A', 'Changed business')
    assert service.bootstrap_status(bob, key)['state'] == 'not_started'
    second = create(service, alice, 'Second')
    foreign = create(service, bob, 'Foreign')
    for org in (foreign['organization_id'], 'unknown'):
        with pytest.raises(IdentityError, match='forbidden'):
            service.bootstrap(alice, uuid4().hex, 'A', 'Attack', org)
    with pytest.raises(IdentityError, match='forbidden'):
        service.bootstrap(alice, uuid4().hex, 'A', 'Another business', results[0]['organization_id'])
    assert second['organization_id'] != results[0]['organization_id']
    assert len(sessions.identity.allowed_entities(alice.user_id)) == 2
    assert len(sessions.identity.allowed_entities(bob.user_id)) == 1
    with database.transaction(read_only=True) as connection:
        from tabula_rasa.ledger_defaults import DEFAULT_ACCOUNTS, DEFAULT_CATEGORIES
        assert connection.scalar(text('SELECT count(*) FROM accounts WHERE entity_id=:id'), {'id': results[0]['entity_id']}) == len(DEFAULT_ACCOUNTS)
        assert connection.scalar(text('SELECT count(*) FROM categories WHERE entity_id=:id'), {'id': results[0]['entity_id']}) == sum(map(len, DEFAULT_CATEGORIES.values()))
    assert sessions.validate(raw).selected is not None


def test_invitation_verified_recipient_freshness_concurrency_replay_and_no_upgrade(database):
    sessions, _, alice = context(database)
    _, _, bob = context(database, 'bob')
    _, _, impostor = context(database, 'impostor')
    repository = PostgresWorkspaceRepository(database)
    service = WorkspaceService(repository)
    org = create(service, alice)['organization_id']
    invitation = service.invite(alice, org, ' BOB@EXAMPLE.TEST ', 'editor')
    assert invitation.credential not in repr(invitation)
    with pytest.raises(IdentityError, match='invitation_invalid'):
        service.respond(impostor, invitation.credential, True)
    with pytest.raises(IdentityError, match='reauthentication_required'):
        service.respond(replace(bob, authenticated_at=bob.authenticated_at - timedelta(minutes=6)), invitation.credential, True)
    with pytest.raises(IdentityError, match='reauthentication_required'):
        service.respond(replace(bob, verified_email=None), invitation.credential, True)
    def accept(_):
        try:
            return service.respond(bob, invitation.credential, True)['state']
        except IdentityError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(accept, range(2))) == ['accepted', 'invitation_used']
    next_invite = service.invite(alice, org, bob.verified_email, 'admin')
    assert service.respond(bob, next_invite.credential, True)['state'] == 'accepted'
    assert sessions.identity.active_memberships(bob.user_id)[0]['role'] == 'editor'
    with database.transaction(read_only=True) as connection:
        rows = connection.execute(text('SELECT * FROM workspace_invitations')).mappings().all()
        assert len(rows) == 2
        assert all(invitation.credential not in str(dict(row)) for row in rows)


def test_invitation_expire_decline_revoke_and_inviter_loses_authority(database):
    _, _, alice = context(database)
    _, _, bob = context(database, 'bob')
    repository = PostgresWorkspaceRepository(database)
    service = WorkspaceService(repository)
    org = create(service, alice)['organization_id']
    expired = service.invite(alice, org, bob.verified_email, 'read_only')
    with database.transaction() as connection:
        connection.execute(text('UPDATE workspace_invitations SET expires_at=CURRENT_TIMESTAMP WHERE id=:id'), {'id': expired.id})
    with pytest.raises(IdentityError, match='invitation_expired'):
        service.respond(bob, expired.credential, True)
    assert service.invitations(alice, org)[0]['state'] == 'expired'
    declined = service.invite(alice, org, bob.verified_email, 'read_only')
    assert service.respond(bob, declined.credential, False)['state'] == 'declined'
    revoked = service.invite(alice, org, bob.verified_email, 'read_only')
    service.revoke_invitation(alice, org, revoked.id)
    with pytest.raises(IdentityError, match='invitation_used'):
        service.respond(bob, revoked.credential, True)
    issued = service.invite(alice, org, bob.verified_email, 'admin')
    repository.identity.set_membership(org, bob.user_id, 'owner')
    repository.identity.set_membership(org, alice.user_id, 'admin')
    with pytest.raises(IdentityError, match='forbidden'):
        service.respond(bob, issued.credential, True)


def test_membership_roles_last_owner_stale_and_scope_revocation(database):
    sessions, raw, alice = context(database)
    _, bob_raw, bob = context(database, 'bob')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    org = create(service, alice)['organization_id']
    other = create(service, bob, 'Other')
    repo = PostgresIdentityRepository(database)
    repo.set_membership(org, bob.user_id, 'admin')
    members = {item['user_id']: item for item in service.members(alice, org)}
    with pytest.raises(IdentityError, match='forbidden'):
        service.change_member(bob, org, bob.user_id, 'owner', True, members[bob.user_id]['authorization_version'])
    with pytest.raises(IdentityError, match='forbidden'):
        service.change_member(bob, org, alice.user_id, 'editor', True, members[alice.user_id]['authorization_version'])
    with pytest.raises(IdentityError, match='invariant'):
        service.change_member(alice, org, alice.user_id, 'admin', True, members[alice.user_id]['authorization_version'])
    service.change_member(alice, org, bob.user_id, 'read_only', True, members[bob.user_id]['authorization_version'])
    with pytest.raises(IdentityError, match='stale'):
        service.change_member(alice, org, bob.user_id, 'editor', True, members[bob.user_id]['authorization_version'])
    selected = repo.allowed_entities(bob.user_id)
    local_entity = next(item.entity_id for item in selected if item.organization_id == org)
    sessions.select(bob_raw, local_entity)
    assert sessions.validate(bob_raw).selected.role == 'read_only'
    current = next(item for item in service.members(alice, org) if item['user_id'] == bob.user_id)
    service.change_member(alice, org, bob.user_id, 'read_only', False, current['authorization_version'])
    assert sessions.validate(bob_raw).selected.organization_id == other['organization_id']
    assert sessions.validate(raw).selected.organization_id == org


def test_admin_self_deactivation_is_scoped_and_cannot_edit_peer_admin(database):
    _, _, alice = context(database)
    _, _, bob = context(database, 'bob')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    org = create(service, alice)['organization_id']
    other = create(service, alice, 'Other')['organization_id']
    repo = PostgresIdentityRepository(database)
    for organization in (org, other):
        repo.set_membership(organization, bob.user_id, 'admin')
    _, _, peer = context(database, 'peer')
    repo.set_membership(org, peer.user_id, 'admin')
    members = {item['user_id']: item for item in service.members(alice, org)}
    with pytest.raises(IdentityError, match='forbidden'):
        service.change_member(bob, org, peer.user_id, 'admin', False, members[peer.user_id]['authorization_version'])
    with pytest.raises(IdentityError, match='forbidden'):
        service.change_member(bob, org, bob.user_id, 'owner', True, members[bob.user_id]['authorization_version'])
    service.change_member(bob, org, bob.user_id, 'admin', False, members[bob.user_id]['authorization_version'])
    assert [(row['organization_id'], row['role']) for row in repo.active_memberships(bob.user_id)] == [(other, 'admin')]
    current = {item['user_id']: item for item in service.members(alice, org)}
    assert current[bob.user_id]['state'] == 'inactive'
    assert current[peer.user_id]['state'] == 'active'
    with pytest.raises(IdentityError, match='forbidden'):
        service.change_member(bob, org, bob.user_id, 'admin', True, current[bob.user_id]['authorization_version'])


def test_workspace_http_setup_csrf_foreign_scope_and_secret_safe_errors(database, caplog):
    import logging
    caplog.set_level(logging.INFO, logger='tabula_rasa.http')
    client, auth, factory = client_for(database)
    raw, session = auth.sessions.issue(replace(verified(), email='alice@example.test'))
    approve_internal(database, session.user_id)
    with client:
        client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
        headers = {'origin': auth.settings.public_origin, 'x-csrf-token': session.csrf_token}
        body = {'request_id': uuid4().hex, 'workspace_name': 'Workspace', 'business_name': 'Business'}
        for invalid_id in ('a' * 16 + '/b', 'a' * 16 + '?b', 'a' * 16 + '%2Fb', 'a' * 16 + ' b'):
            rejected = client.post('/api/v1/workspaces/bootstrap', json={**body, 'request_id': invalid_id}, headers=headers)
            assert rejected.status_code == 422
        assert client.get('/api/v1/workspaces/bootstrap/' + 'a' * 16 + '%20b').status_code == 422
        assert client.post('/api/v1/workspaces/bootstrap', json=body).status_code == 403
        result = client.post('/api/v1/workspaces/bootstrap', json=body, headers=headers)
        assert result.status_code == 200 and result.json()['state'] == 'ready'
        org = result.json()['organization_id']
        assert client.post('/api/v1/workspaces/bootstrap', json=body, headers=headers).json() == result.json()
        assert client.get('/api/v1/workspaces/bootstrap/' + body['request_id']).json() == result.json()
        safe_body = {**body, 'request_id': 'Recoverable_ID-123'}
        safe_result = client.post('/api/v1/workspaces/bootstrap', json=safe_body, headers=headers)
        assert safe_result.status_code == 200
        assert client.get('/api/v1/workspaces/bootstrap/' + safe_body['request_id']).json() == safe_result.json()
        choices = client.get('/api/v1/entities').json()['items']
        assert choices[0]['organization_name'] == 'Workspace' and choices[0]['role'] == 'owner'
        assert client.get('/api/v1/workspaces/foreign/members').status_code == 403
        issued = client.post(f'/api/v1/workspaces/{org}/invitations', json={'email': 'bob@example.test', 'role': 'read_only'}, headers=headers)
        assert issued.status_code == 200
        credential = issued.json()['credential']
        assert credential not in client.get(f'/api/v1/workspaces/{org}/invitations').text
        response = client.post('/api/v1/invitations/respond', json={'credential': credential, 'accept': True}, headers=headers)
        assert response.status_code == 403 and response.json()['error']['code'] == 'invitation_invalid'
        assert credential not in response.text and credential not in caplog.text
        assert 'alice@example.test' not in caplog.text and 'bob@example.test' not in caplog.text
        assert any(record.name == 'tabula_rasa.http' for record in caplog.records)
        factory.assert_not_called()


@pytest.mark.parametrize('method,path', [
    ('POST', '/api/v1/businesses'),
    ('GET', '/api/v1/businesses/creation/unknown-request-id'),
    ('GET', '/api/v1/workspaces/foreign/members'),
    ('GET', '/api/v1/workspaces/foreign/invitations'),
    ('GET', '/api/v1/workspaces/bootstrap/unknown'),
    ('POST', '/api/v1/workspaces/bootstrap'),
    ('POST', '/api/v1/workspaces/foreign/businesses'),
    ('POST', '/api/v1/workspaces/foreign/invitations'),
    ('POST', '/api/v1/workspaces/foreign/members/unknown/ownership'),
    ('PATCH', '/api/v1/workspaces/foreign/members/unknown'),
    ('DELETE', '/api/v1/workspaces/foreign/invitations/unknown'),
    ('POST', '/api/v1/invitations/respond'),
])
def test_every_workspace_route_requires_named_session_and_unsafe_csrf(database, method, path):
    client, auth, factory = client_for(database)
    with client:
        assert client.request(method, path).status_code == 401
        raw, _ = auth.sessions.issue(verified())
        client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
        if method != 'GET':
            assert client.request(method, path, headers={'origin': auth.settings.public_origin}).status_code == 403
        factory.assert_not_called()


def test_owner_grant_requires_recent_authentication_and_admin_can_self_demote(database):
    _, _, alice = context(database)
    _, _, bob = context(database, 'bob')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    org = create(service, alice)['organization_id']
    repo = PostgresIdentityRepository(database)
    repo.set_membership(org, bob.user_id, 'admin')
    member = next(item for item in service.members(alice, org) if item['user_id'] == bob.user_id)
    service.change_member(bob, org, bob.user_id, 'editor', True, member['authorization_version'])
    member = next(item for item in service.members(alice, org) if item['user_id'] == bob.user_id)
    with pytest.raises(IdentityError, match='forbidden'):
        service.change_member(alice, org, bob.user_id, 'owner', True, member['authorization_version'])
    stale = replace(alice, authenticated_at=alice.authenticated_at - timedelta(minutes=6))
    with pytest.raises(IdentityError, match='reauthentication_required'):
        service.grant_ownership(stale, org, bob.user_id, member['authorization_version'])
    service.grant_ownership(alice, org, bob.user_id, member['authorization_version'])
    assert next(item for item in service.members(alice, org) if item['user_id'] == bob.user_id)['role'] == 'owner'
    alice_version = next(item['authorization_version'] for item in service.members(alice, org) if item['user_id'] == alice.user_id)
    with pytest.raises(IdentityError, match='reauthentication_required'):
        service.change_member(stale, org, alice.user_id, 'admin', True, alice_version)


@pytest.mark.parametrize('business_scope', [False, True])
def test_concurrent_last_owner_removals_retain_one_owner(database, business_scope):
    _, _, alice = context(database)
    _, _, bob = context(database, 'bob')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    business = create(service, alice)
    org = business['organization_id']
    repo = PostgresIdentityRepository(database)
    repo.set_membership(org, bob.user_id, 'owner')
    versions = {item['user_id']: item['authorization_version'] for item in service.members(alice, org)}
    def demote(session):
        try:
            if business_scope:
                service.business_operation(session, business['entity_id'], 'change_member',
                    session.user_id, 'editor', True, versions[session.user_id])
            else:
                service.change_member(session, org, session.user_id, 'editor', True, versions[session.user_id])
            return 'demoted'
        except IdentityError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(demote, (alice, bob))) == ['demoted', 'invariant']
    owners = [user for user in (alice, bob) if repo.active_memberships(user.user_id)[0]['role'] == 'owner']
    assert len(owners) == 1


def test_bootstrap_additional_business_denies_admin_and_removed_receipt_owner(database):
    _, _, alice = context(database)
    _, _, bob = context(database, 'bob')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    key = uuid4().hex
    result = service.bootstrap(alice, key, 'A', 'Business')
    org = result['organization_id']
    repo = PostgresIdentityRepository(database)
    repo.set_membership(org, bob.user_id, 'owner')
    repo.set_membership(org, alice.user_id, 'admin')
    with pytest.raises(IdentityError, match='forbidden'):
        service.bootstrap(alice, uuid4().hex, 'A', 'Another', org)
    repo.set_membership(org, alice.user_id, 'admin', active=False)
    with pytest.raises(IdentityError, match='forbidden'):
        service.bootstrap_status(alice, key)
    with pytest.raises(IdentityError, match='forbidden'):
        service.bootstrap(alice, key, 'A', 'Business')


def test_business_only_http_contract_and_independent_owner_access(database):
    client, auth, _ = client_for(database)
    raw, alice = auth.sessions.issue(replace(verified(), email='alice@example.test'))
    _, bob = auth.sessions.issue(replace(verified('bob'), email='bob@example.test'))
    approve_internal(database, alice.user_id, bob.user_id)
    repo = PostgresIdentityRepository(database)
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    with client:
        client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
        headers = {'origin': auth.settings.public_origin, 'x-csrf-token': alice.csrf_token}
        body = {'request_id': uuid4().hex, 'business_name': ' Business A '}
        for invalid in ({**body, 'business_name': '  '}, {**body, 'business_name': 'x' * 161},
                        {**body, 'request_id': 'invalid'}, {**body, 'organization_id': 'forged'},
                        {**body, 'workspace_name': 'forged'}, {**body, 'entity_id': 'forged'}):
            assert client.post('/api/v1/businesses', json=invalid, headers=headers).status_code == 422
        first = client.post('/api/v1/businesses', json=body, headers=headers)
        assert first.status_code == 200
        assert set(first.json()) == {'state', 'entity_id'}
        assert first.json()['state'] == 'ready'
        assert client.post('/api/v1/businesses', json=body, headers=headers).json() == first.json()
        assert client.get('/api/v1/businesses/creation/' + body['request_id']).json() == first.json()
        assert client.post('/api/v1/businesses', json={**body, 'business_name': 'changed'}, headers=headers).status_code == 409
        a = repo.allowed_entities(alice.user_id)[0]
        assert a.name == 'Business A'
        repo.set_membership(a.organization_id, bob.user_id, 'read_only')
        before = service.members(alice, a.organization_id)
        second = client.post('/api/v1/businesses', json={'request_id': uuid4().hex, 'business_name': 'Business B'}, headers=headers)
        assert second.status_code == 200
        b = next(item for item in repo.allowed_entities(alice.user_id) if item.entity_id == second.json()['entity_id'])
        assert b.organization_id != a.organization_id and b.role == a.role == 'owner'
        assert service.members(alice, a.organization_id) == before
        assert [item.entity_id for item in repo.allowed_entities(bob.user_id)] == [a.entity_id]
        for org in (a.organization_id, b.organization_id, 'forged'):
            denied = client.post('/api/v1/workspaces/' + org + '/businesses',
                json={'request_id': uuid4().hex, 'workspace_name': 'ignored', 'business_name': 'Attack'}, headers=headers)
            assert denied.status_code == 403
        # A read-only member may independently own a new business without changing A.
        own = service.create_business(bob, uuid4().hex, 'Bob business')
        assert len(repo.allowed_entities(bob.user_id)) == 2
        assert own['entity_id'] not in {item.entity_id for item in repo.allowed_entities(alice.user_id)}
        client.cookies.clear()
        bob_raw, bob_session = auth.sessions.issue(replace(verified('bob'), email='bob@example.test'))
        client.cookies.set(auth.session_cookie, bob_raw, domain='127.0.0.1', path='/')
        assert client.get('/api/v1/businesses/creation/' + body['request_id']).json() == {'state': 'not_started', 'entity_id': None}
        assert client.get('/api/v1/entities/' + b.entity_id + '/accounts').status_code == 403
        assert client.post('/api/v1/session/entity', json={'entity_id': b.entity_id},
            headers={**headers, 'x-csrf-token': bob_session.csrf_token}).status_code == 403


def test_legacy_addition_receipt_can_be_replayed_without_creating(database):
    import json
    from hashlib import sha256

    _, _, alice = context(database)
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    original = service.create_business(alice, uuid4().hex, 'Existing business')
    org, key = original['organization_id'], uuid4().hex
    payload = sha256(json.dumps(['Legacy', 'Existing business', org]).encode()).hexdigest()
    with database.transaction() as connection:
        connection.execute(text("""INSERT INTO workspace_bootstraps
            (user_id, request_id, payload_hash, organization_id, entity_id)
            VALUES (:user, :key, :payload, :org, :entity)"""),
            {'user': alice.user_id, 'key': key, 'payload': payload, 'org': org, 'entity': original['entity_id']})
    assert service.bootstrap(alice, key, 'Legacy', 'Existing business', org) == original
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM business_entities')) == 1


def test_historical_receipt_replay_uses_current_business_scope(database):
    import json
    from hashlib import sha256

    _, _, alice = context(database)
    _, _, bob = context(database, 'bob')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    current = service.create_business(alice, uuid4().hex, 'Current business')
    source = service.create_business(alice, uuid4().hex, 'Original container')
    key = uuid4().hex
    payload = sha256(json.dumps(['Legacy', 'Current business', source['organization_id']]).encode()).hexdigest()
    # Seed an already reconciled receipt independently of retired migration tooling.
    with database.transaction() as connection:
        connection.execute(text("""INSERT INTO workspace_bootstraps
            (user_id,request_id,payload_hash,organization_id,entity_id)
            VALUES (:user,:key,:payload,:org,:entity)"""),
            {'user': alice.user_id, 'key': key, 'payload': payload,
             'org': current['organization_id'], 'entity': current['entity_id']})
    def replay():
        return service.bootstrap(alice, key, 'Legacy', 'Current business', source['organization_id'])
    repo = PostgresIdentityRepository(database)
    repo.set_membership(source['organization_id'], bob.user_id, 'owner')
    repo.set_membership(source['organization_id'], alice.user_id, 'owner', active=False)
    assert replay() == service.bootstrap_status(alice, key) == current
    with pytest.raises(IdentityError, match='conflict'):
        service.bootstrap(alice, key, 'Legacy', 'Changed', source['organization_id'])
    repo.set_membership(current['organization_id'], bob.user_id, 'owner')
    repo.set_membership(current['organization_id'], alice.user_id, 'read_only')
    assert service.bootstrap_status(alice, key) == current
    with pytest.raises(IdentityError, match='forbidden'):
        replay()
    repo.set_membership(current['organization_id'], alice.user_id, 'owner', active=False)
    repo.set_membership(source['organization_id'], alice.user_id, 'owner')
    for operation in (replay, lambda: service.bootstrap_status(alice, key)):
        with pytest.raises(IdentityError, match='forbidden'):
            operation()
    assert service.bootstrap_status(bob, key)['state'] == 'not_started'
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM business_entities')) == 2
