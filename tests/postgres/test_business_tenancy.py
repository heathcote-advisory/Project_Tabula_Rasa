"""Persistent business boundaries and non-destructive legacy rollout."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.application.workspaces import WorkspaceService
from tabula_rasa.infrastructure.postgres.business_tenancy import tenancy_inventory
from tabula_rasa.infrastructure.postgres.control_plane import PostgresIdentityRepository
from tabula_rasa.infrastructure.postgres.migrations import current_revision, migration_config, upgrade
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError
from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository
from tests.postgres._support import entity
from tests.postgres.test_identity import verified
from tests.postgres.test_identity_http import client_for
from tests.postgres.test_workspaces import context, create
from tests.postgres.historical_identity import historical_access, legacy_business, legacy_invitation


def old_schema(database):
    with database.engine.begin() as connection:
        command.upgrade(migration_config(connection), '0015_support_controls')


def snapshot(database):
    with database.transaction(read_only=True) as connection:
        return {table: connection.execute(text(f'SELECT row_to_json(t)::text FROM {table} t ORDER BY row_to_json(t)::text')).scalars().all()
                for table in ('organizations', 'organization_entities', 'memberships', 'identity_users',
                              'identity_bindings', 'identity_sessions', 'workspace_invitations',
                              'workspace_bootstraps', 'legacy_entity_claims', 'external_connections',
                              'business_entities', 'accounts', 'categories', 'documents', 'migration_id_mappings')}


@pytest.mark.parametrize('binding_state', ['active', 'inactive'])
def test_ambiguous_legacy_upgrade_is_repeatably_blocked_without_data_changes(empty_database, binding_state):
    database = empty_database
    old_schema(database)
    with historical_access():
        _, _, owner = context(database)
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    business = legacy_business(database, owner)
    invitation = legacy_invitation(database, owner, business['organization_id'], 'invitee@example.test', 'read_only')
    with database.transaction() as connection:
        entity(connection, 'retained-legacy-business')
        connection.execute(text('''INSERT INTO organization_entities(entity_id,organization_id,state)
            VALUES ('retained-legacy-business',:org,:state)'''),
            {'org': business['organization_id'], 'state': binding_state})
    before = snapshot(database)
    inventory = tenancy_inventory(database)
    assert inventory == tenancy_inventory(database)
    assert inventory['multi_business_organizations'] == 1
    assert not inventory['ready_for_constraint']
    assert 'invitee@example.test' not in json.dumps(inventory)
    for _ in range(2):
        with pytest.raises(DatabaseError, match='invariant') as caught:
            upgrade(database)
        assert business['organization_id'] not in str(caught.value)
        assert snapshot(database) == before
        assert database.health()['revision'] == '0015_support_controls'
    # Frozen source access denies ambiguous scope; current runtime also fails
    # closed because this refused upgrade has not installed its required head.
    with historical_access():
        assert PostgresIdentityRepository(database).allowed_entities(owner.user_id) == ()
    with pytest.raises(IdentityError, match='unavailable'):
        service.members(owner, business['organization_id'])
    with pytest.raises(IdentityError, match='unavailable'):
        service.revoke_invitation(owner, business['organization_id'], invitation.id)


def test_forward_upgrade_preserves_sessions_invitations_defaults_and_receipt_replay(empty_database):
    database = empty_database
    old_schema(database)
    with historical_access():
        sessions, raw, owner = context(database)
        _, _, recipient = context(database, 'recipient')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    key = uuid4().hex
    business = legacy_business(database, owner, key, 'Private container', 'Business A')
    other = legacy_business(database, owner, workspace='Business B')
    with historical_access():
        sessions.select(raw, business['entity_id'])
    invitation = legacy_invitation(database, owner, business['organization_id'], recipient.verified_email, 'read_only')
    # A historical addition receipt for the canonical business must remain
    # recoverable, without authorizing any new addition after rollout.
    legacy_key = uuid4().hex
    payload = sha256(json.dumps(['Private container', 'Business A', business['organization_id']]).encode()).hexdigest()
    with database.transaction() as connection:
        connection.execute(text('''INSERT INTO workspace_bootstraps
            (user_id,request_id,payload_hash,organization_id,entity_id) VALUES (:user,:key,:payload,:org,:entity)'''),
            {'user': owner.user_id, 'key': legacy_key, 'payload': payload,
             'org': business['organization_id'], 'entity': business['entity_id']})
    before = snapshot(database)
    upgrade(database)
    upgrade(database)
    assert snapshot(database) == before
    assert sessions.validate(raw).selected.entity_id == business['entity_id']
    assert service.bootstrap(owner, key, 'Private container', 'Business A') == business
    assert service.bootstrap(owner, legacy_key, 'Private container', 'Business A', business['organization_id']) == business
    assert service.respond(recipient, invitation.credential, True)['state'] == 'accepted'
    assert [item.entity_id for item in sessions.identity.allowed_entities(recipient.user_id)] == [business['entity_id']]
    assert other['organization_id'] != business['organization_id']
    with database.transaction(read_only=True) as connection:
        assert current_revision(connection) == '0022_drive_recovery_binding'


def test_upgrade_preserves_two_claimed_ledgers_provenance_documents_and_provider_bindings(empty_database, monkeypatch):
    from tabula_rasa.infrastructure.postgres.composition import create_postgres_export_service
    from tabula_rasa.infrastructure.postgres.cutover import PostgresCutoverController
    from tests.postgres.test_cutover import loaded, REHEARSAL_EVIDENCE, status
    from tests.postgres.test_legacy_claims import apply_claim, claim_for

    database = empty_database
    old_schema(database)
    # Reproduce the prior checkout's loader/cutover schema guards solely while
    # constructing the historical source fixture. Production guards stay strict.
    for module in ('migration', 'migration_reconciliation', 'cutover'):
        monkeypatch.setattr(f'tabula_rasa.infrastructure.postgres.{module}.head_revision',
                            lambda: '0015_support_controls')
    identity = PostgresIdentityRepository(database)
    controller = PostgresCutoverController(database)
    expected = {}
    authority = {}
    for profile in ('alpha', 'beta'):
        plan = loaded(database, profile)
        controller.prepare(plan, mode='rehearsal', evidence=REHEARSAL_EVIDENCE)
        controller.activate(plan.snapshot.binding)
        user = identity.resolve_user(verified(profile))
        org = identity.create_organization(user.id, profile)
        apply_claim(database, claim_for(database, profile, org, user))
        with database.transaction() as connection:
            connection.execute(text('''INSERT INTO external_connections
                (entity_id,organization_id,provider,secret_ref,profile_key,store_scope_key,required_scopes,status)
                VALUES (:entity,:org,'google_drive',:secret,:entity,:scope,'[]'::jsonb,'connected')'''),
                {'entity': profile, 'org': org, 'secret': uuid4().hex, 'scope': 'folder-' + profile})
        expected[profile] = create_postgres_export_service(database, profile).export()['domains']
        authority[profile] = status(database, profile)
    monkeypatch.undo()
    before = snapshot(database)
    upgrade(database)
    after = snapshot(database)
    after_bindings = [json.loads(row) for row in after.pop('external_connections')]
    for row in after_bindings:
        assert row.pop('drive_recovery_binding') is None
    assert after_bindings == [json.loads(row) for row in before.pop('external_connections')]
    assert after == before
    assert tenancy_inventory(database)['counts']['external_connections'] == 2
    for profile in expected:
        assert create_postgres_export_service(database, profile).export()['domains'] == expected[profile]
        assert status(database, profile) == authority[profile]


def test_direct_and_concurrent_binding_cannot_reuse_even_an_inactive_container(database):
    repo = PostgresIdentityRepository(database)
    owner = repo.resolve_user(verified())
    org = repo.create_organization(owner.id, 'Container')
    with database.transaction() as connection:
        for name in ('a', 'b', 'c'):
            entity(connection, name)
    def bind(name):
        try:
            repo.bind_entity(org, name)
            return 'bound'
        except IdentityError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(bind, ('a', 'b')))
    assert results.count('bound') == 1
    assert all(result in {'bound', 'forbidden', 'concurrent_write', 'duplicate'} for result in results)
    with database.transaction() as connection:
        connection.execute(text("UPDATE organization_entities SET state='inactive' WHERE organization_id=:org"), {'org': org})
    with pytest.raises(IdentityError, match='forbidden'):
        repo.bind_entity(org, 'c')
    with pytest.raises(DatabaseError, match='duplicate'):
        with database.transaction() as connection:
            connection.execute(text("INSERT INTO organization_entities(entity_id,organization_id) VALUES ('c',:org)"), {'org': org})
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM organization_entities WHERE organization_id=:org'), {'org': org}) == 1


def test_unique_constraint_serializes_direct_concurrent_inserts(database):
    repo = PostgresIdentityRepository(database)
    owner = repo.resolve_user(verified())
    org = repo.create_organization(owner.id, 'Container')
    with database.transaction() as connection:
        for name in ('a', 'b'):
            entity(connection, name)
    def insert(name):
        try:
            with database.transaction() as connection:
                connection.execute(text('INSERT INTO organization_entities(entity_id,organization_id) VALUES (:entity,:org)'),
                    {'entity': name, 'org': org})
            return 'bound'
        except DatabaseError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(insert, ('a', 'b'))) == ['bound', 'duplicate']


def test_concurrent_second_business_http_requests_deny_owner_without_partial_records(database):
    client, auth, factory = client_for(database)
    raw, session = auth.sessions.issue(verified())
    with client:
        client.cookies.set(auth.session_cookie, raw, domain='127.0.0.1', path='/')
        headers = {'origin': auth.settings.public_origin, 'x-csrf-token': session.csrf_token}
        result = client.post('/api/v1/workspaces/bootstrap', headers=headers,
            json={'request_id': uuid4().hex, 'workspace_name': 'A', 'business_name': 'A'})
        assert result.status_code == 200
        org = result.json()['organization_id']
        before = snapshot(database)
        def add(_):
            return client.post(f'/api/v1/workspaces/{org}/businesses', headers=headers,
                json={'request_id': uuid4().hex, 'workspace_name': 'A', 'business_name': 'second'}).status_code
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert list(workers.map(add, range(2))) == [403, 403]
        # HTTP session activity may change; domain/control-plane writes must not.
        after = snapshot(database)
        before.pop('identity_sessions')
        after.pop('identity_sessions')
        assert before == after
        factory.assert_not_called()


def test_independent_roles_revocation_and_invitation_do_not_change_other_business(database):
    sessions, raw, alice = context(database)
    _, _, bob = context(database, 'bob')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    a = create(service, alice, 'A')
    b = create(service, bob, 'B')
    c = create(service, bob, 'C')
    repo = sessions.identity
    repo.set_membership(b['organization_id'], alice.user_id, 'admin')
    independent = create(service, alice, 'Independent')
    assert service.members(alice, independent['organization_id']) == [{
        'user_id': alice.user_id, 'display_name': 'Synthetic', 'role': 'owner',
        'state': 'active', 'authorization_version': 1}]
    invitation = service.invite(bob, c['organization_id'], alice.verified_email, 'read_only')
    service.respond(alice, invitation.credential, True)
    roles = {item.entity_id: item.role for item in repo.allowed_entities(alice.user_id)}
    assert roles == {a['entity_id']: 'owner', b['entity_id']: 'admin', c['entity_id']: 'read_only',
                     independent['entity_id']: 'owner'}
    sessions.select(raw, b['entity_id'])
    selected = sessions.validate(raw).selected
    repo.set_membership(a['organization_id'], bob.user_id, 'owner')
    member = next(row for row in service.members(alice, a['organization_id']) if row['user_id'] == alice.user_id)
    service.change_member(alice, a['organization_id'], alice.user_id, 'read_only', False, member['authorization_version'])
    assert sessions.validate(raw).selected == selected
    assert {item.entity_id: item.role for item in repo.allowed_entities(alice.user_id)} == {
        b['entity_id']: 'admin', c['entity_id']: 'read_only', independent['entity_id']: 'owner'}
    with pytest.raises(IdentityError, match='forbidden'):
        service.invite(alice, a['organization_id'], 'third@example.test', 'read_only')
