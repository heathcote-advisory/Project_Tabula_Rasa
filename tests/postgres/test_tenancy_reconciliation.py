"""Guarded source maintenance and current-business historical replay."""

from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

import pytest
from psycopg import sql
from sqlalchemy import event, text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.application.workspaces import WorkspaceService
from tabula_rasa.infrastructure.postgres.control_plane import PostgresIdentityRepository
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.migrations import upgrade
from tabula_rasa.infrastructure.postgres.runtime import Database, DatabaseError
from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository
from tabula_rasa.infrastructure.postgres.tenancy_reconciliation import (
    ReconciliationError, _snapshot, apply, fingerprint, prepare, readback, write_plan,
)
from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository
from tests.postgres._support import entity, simple_entry
from tests.postgres.test_business_tenancy import old_schema
from tests.postgres.test_identity import verified
from tests.postgres.historical_identity import historical_access


@pytest.fixture
def source(empty_database, tmp_path):
    db = empty_database
    old_schema(db)
    with db.engine.begin() as connection:
        connection.execute(text("""DO $$ BEGIN IF NOT EXISTS
            (SELECT 1 FROM pg_roles WHERE rolname='tabula_staging_runtime') THEN
            CREATE ROLE tabula_staging_runtime LOGIN; END IF; END $$"""))
    sessions = PostgresSessionRepository(db, 'staging', 'a' * 64)
    sessions.configure()
    with historical_access():
        raw, owner = sessions.issue(verified('owner'))
        _, recipient = sessions.issue(verified('recipient'))
        _, peer = sessions.issue(verified('peer'))
    repository = PostgresIdentityRepository(db)
    service = WorkspaceService(PostgresWorkspaceRepository(db))
    from tests.postgres.historical_identity import legacy_business, legacy_invitation
    a1 = legacy_business(db, owner, workspace='Org A', name='A1')
    b1 = legacy_business(db, peer, workspace='Org B', name='B1')
    invitation = legacy_invitation(db, owner, a1['organization_id'], recipient.verified_email, 'read_only')
    repository.set_membership(a1['organization_id'], recipient.user_id, 'read_only')
    with db.transaction() as connection:
        connection.execute(text("UPDATE workspace_invitations SET state='accepted',responded_by=:user WHERE id=:id"),
                           {'user':recipient.user_id,'id':invitation.id})
    repository.set_membership(a1['organization_id'], recipient.user_id, 'read_only', active=False)
    with historical_access():
        sessions.select(raw, a1['entity_id'])
    key, moved = uuid4().hex, uuid4().hex
    payload = sha256(json.dumps(['Org A', 'A2', a1['organization_id']]).encode()).hexdigest()
    byte_files = {}
    with db.transaction() as connection:
        entity(connection, moved)
        connection.execute(text("UPDATE business_entities SET name='A2' WHERE id=:id"), {'id': moved})
        connection.execute(text('INSERT INTO organization_entities(entity_id,organization_id) VALUES (:entity,:org)'),
                           {'entity': moved, 'org': a1['organization_id']})
        connection.execute(text('''INSERT INTO workspace_bootstraps
            (user_id,request_id,payload_hash,organization_id,entity_id) VALUES (:user,:key,:hash,:org,:entity)'''),
            {'user': owner.user_id, 'key': key, 'hash': payload, 'org': a1['organization_id'], 'entity': moved})
        for item in (a1, b1):
            scope = item['entity_id']
            connection.execute(text("""INSERT INTO accounts
                (entity_id,id,name,name_key,type,classification,incarnation_id)
                VALUES (:entity,'cash','Synthetic cash','synthetic cash','Other','Asset','cash')"""),
                {'entity': scope})
            connection.execute(text("""INSERT INTO categories(entity_id,id,name,name_key,kind,incarnation_id)
                VALUES (:entity,'sales','Synthetic sales','synthetic sales','Income','sales')"""), {'entity': scope})
            simple_entry(connection, scope)
            data = ('synthetic bytes ' + scope).encode()
            path = tmp_path / scope
            path.write_bytes(data)
            byte_files[path] = data
            connection.execute(text('''INSERT INTO documents
                (entity_id,id,incarnation_id,stored_filename,byte_size,sha256,store_kind,store_scope_key,storage_key)
                VALUES (:entity,'receipt','receipt','receipt.txt',:size,:hash,'google_drive',:scope,'original')'''),
                {'entity': scope, 'size': len(data), 'hash': sha256(data).hexdigest(), 'scope': 'folder-' + scope})
            connection.execute(text("""INSERT INTO document_links(entity_id,document_id,entry_id,position)
                VALUES (:entity,'receipt','income',1)"""), {'entity': scope})
            connection.execute(text('''INSERT INTO external_connections
                (entity_id,organization_id,provider,secret_ref,profile_key,store_scope_key,required_scopes,status)
                VALUES (:entity,:org,'google_drive',:secret,'profile_'||:entity,:scope,'[]','connected')'''),
                {'entity': scope, 'org': item['organization_id'], 'secret': uuid4().hex, 'scope': 'folder-' + scope})
    args = dict(source_org=a1['organization_id'], retained_entity=a1['entity_id'], moved_entity=moved,
                owner=owner.user_id, destination_org=uuid4().hex, recovery_sha256='b' * 64)
    return db, service, repository, owner, recipient, peer, a1, b1, key, args, byte_files


def state(db):
    with db.transaction(read_only=True) as connection:
        return _snapshot(connection)


def test_atomic_reconciliation_preserves_scope_provenance_and_upgrades(source, tmp_path):
    db, service, repo, owner, recipient, peer, a1, b1, key, args, byte_files = source
    plan = prepare(db, **args)
    path = tmp_path / 'private-plan.json'
    approval = write_plan(path, plan)
    original = path.read_bytes()
    assert readback(db, plan, approval=approval) == 'not_applied'
    assert apply(db, plan, approval=approval, writers_drained=True) == 'applied'
    assert readback(db, plan, approval=approval) == 'applied'
    assert path.read_bytes() == original
    assert json.loads(original)['before'] == plan['before']
    with pytest.raises(FileExistsError):
        write_plan(path, plan)
    with pytest.raises(ReconciliationError):
        apply(db, plan, approval=approval, writers_drained=True)
    assert {p: p.read_bytes() for p in byte_files} == byte_files
    result = dict(state='ready', organization_id=args['destination_org'], entity_id=args['moved_entity'])
    with historical_access():
        assert [a.entity_id for a in repo.allowed_entities(recipient.user_id)] == []
        assert {a.entity_id for a in repo.allowed_entities(owner.user_id)} == {args['retained_entity'], args['moved_entity']}
        assert {a.entity_id for a in repo.allowed_entities(peer.user_id)} == {b1['entity_id']}
        assert service.bootstrap_status(owner, key) == result
    upgrade(db)
    assert service.bootstrap(owner, key, 'Org A', 'A2', a1['organization_id']) == result
    assert db.health()['revision'] == '0022_drive_recovery_binding'
    assert service.bootstrap_status(owner, key) == result
    assert {p: p.read_bytes() for p in byte_files} == byte_files
    # Keep another active Owner in A1 so its original owner can legitimately
    # lose A1 while retaining A2. Historical recovery must use current A2 scope.
    repo.set_membership(a1['organization_id'], peer.user_id, 'owner')
    repo.set_membership(a1['organization_id'], owner.user_id, 'owner', active=False)
    assert service.bootstrap(owner, key, 'Org A', 'A2', a1['organization_id']) == result
    assert service.bootstrap_status(owner, key) == result
    with pytest.raises(IdentityError, match='conflict'):
        service.bootstrap(owner, key, 'Org A', 'changed', a1['organization_id'])
    repo.set_membership(args['destination_org'], peer.user_id, 'owner')
    repo.set_membership(args['destination_org'], owner.user_id, 'read_only')
    assert service.bootstrap_status(owner, key) == result
    with pytest.raises(IdentityError, match='forbidden'):
        service.bootstrap(owner, key, 'Org A', 'A2', a1['organization_id'])
    repo.set_membership(args['destination_org'], owner.user_id, 'owner', active=False)
    repo.set_membership(a1['organization_id'], owner.user_id, 'owner')
    for operation in (lambda: service.bootstrap_status(owner, key),
                      lambda: service.bootstrap(owner, key, 'Org A', 'A2', a1['organization_id'])):
        with pytest.raises(IdentityError, match='forbidden'):
            operation()
    assert service.bootstrap_status(peer, key)['state'] == 'not_started'
    with db.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM business_entities')) == 3
        assert connection.scalar(text("SELECT state FROM workspace_invitations")) == 'accepted'
        assert connection.scalar(text('SELECT count(*) FROM external_connections')) == 2
    with pytest.raises(DatabaseError, match='invariant'):
        with db.transaction() as connection:
            connection.execute(text('UPDATE organization_entities SET organization_id=:new WHERE entity_id=:id'),
                               {'new': b1['organization_id'], 'id': args['moved_entity']})


@pytest.mark.parametrize('point', ['INSERT INTO organizations', 'INSERT INTO memberships',
                                  'UPDATE organization_entities', 'UPDATE workspace_bootstraps',
                                  'SET CONSTRAINTS ALL IMMEDIATE'])
def test_failure_rolls_back_rows_guards_and_fk_definition(source, point):
    db, *_, args, byte_files = source
    plan = prepare(db, **args)
    before = state(db)
    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(point):
            raise RuntimeError('synthetic interruption')
    event.listen(db.engine, 'after_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='synthetic interruption'):
            apply(db, plan, approval=fingerprint(plan), writers_drained=True)
    finally:
        event.remove(db.engine, 'after_cursor_execute', fail)
    assert state(db) == before
    assert readback(db, plan, approval=fingerprint(plan)) == 'not_applied'
    assert {p: p.read_bytes() for p in byte_files} == byte_files


@pytest.mark.parametrize('change', ['membership', 'receipt', 'provider', 'session', 'binding', 'guard'])
def test_stale_approved_plan_refuses_changed_state(source, change):
    db, service, repo, owner, recipient, peer, a1, b1, key, args, _ = source
    plan = prepare(db, **args)
    with db.transaction() as connection:
        if change == 'membership':
            connection.execute(text("UPDATE memberships SET role='editor' WHERE organization_id=:org AND state='inactive'"),
                               {'org': args['source_org']})
        elif change == 'receipt':
            connection.execute(text('UPDATE workspace_bootstraps SET payload_hash=:hash WHERE request_id=:key'),
                               {'hash': 'c' * 64, 'key': key})
        elif change == 'provider':
            connection.execute(text("""INSERT INTO external_connections
                (entity_id,organization_id,provider,secret_ref,profile_key,store_scope_key,required_scopes,status)
                VALUES (:entity,:org,'google_drive',:secret,'profile_'||:entity,'unexpected','[]','pending')"""),
                {'entity': args['moved_entity'], 'org': args['source_org'], 'secret': uuid4().hex})
        elif change == 'session':
            connection.execute(text('UPDATE identity_sessions SET organization_id=:org,entity_id=:entity WHERE user_id=:user'),
                               {'org': args['source_org'], 'entity': args['moved_entity'], 'user': owner.user_id})
        elif change == 'binding':
            connection.execute(text("UPDATE organization_entities SET state='inactive' WHERE entity_id=:entity"),
                               {'entity': args['moved_entity']})
        else:
            connection.execute(text('ALTER FUNCTION guard_identity_change() SECURITY DEFINER'))
    before = state(db)
    with pytest.raises(ReconciliationError):
        apply(db, plan, approval=fingerprint(plan), writers_drained=True)
    assert state(db) == before


def test_manifest_target_approval_drain_and_database_role_are_required(source):
    db, *_, args, byte_files = source
    plan = prepare(db, **args)
    before = state(db)
    for candidate, approval, drained in ((plan, '0' * 64, True), (plan, fingerprint(plan), False)):
        with pytest.raises(ReconciliationError):
            apply(db, candidate, approval=approval, writers_drained=drained)
    forged = deepcopy(plan)
    forged['database_sha256'] = 'd' * 64
    with pytest.raises(ReconciliationError):
        apply(db, forged, approval=fingerprint(forged), writers_drained=True)
    assert state(db) == before
    role = 'runtime_' + uuid4().hex
    with db.engine.begin() as connection:
        connection.execute(text(f'CREATE ROLE {role} LOGIN'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA public TO {role}'))
        connection.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}'))
    # The ordinary runtime cannot own/replace the guard or take maintenance
    # table locks; no lasting privileged callable is installed for it.
    runtime = Database(DatabaseSettings(db.engine.url.set(username=role)))
    protected = state(db)
    try:
        with pytest.raises((ReconciliationError, DatabaseError)):
            apply(runtime, plan, approval=fingerprint(plan), writers_drained=True)
        assert state(db) == protected
    finally:
        runtime.dispose()
        with db.engine.begin() as connection:
            connection.execute(text(f'DROP OWNED BY {role}'))
            connection.execute(text(f'DROP ROLE {role}'))


def test_active_runtime_connection_prevents_maintenance(source):
    db, *_, args, byte_files = source
    plan = prepare(db, **args)
    runtime = Database(DatabaseSettings(db.engine.url.set(username='tabula_staging_runtime')))
    before = state(db)
    try:
        with runtime.engine.connect() as active:
            active.execute(text('SELECT 1'))
            with pytest.raises(ReconciliationError):
                apply(db, plan, approval=fingerprint(plan), writers_drained=True)
        assert state(db) == before
    finally:
        runtime.dispose()


def test_disabled_ownership_guard_refuses_prepare(source):
    db, *_, args, byte_files = source
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE organization_entities DISABLE TRIGGER identity_change'))
    before = state(db)
    with pytest.raises(ReconciliationError):
        prepare(db, **args)
    assert state(db) == before


def test_plan_uses_postgres_timestamp_precision_for_exact_final_state(source):
    db, *_, args, byte_files = source
    def fixed_timestamp(connection, cursor, statement, parameters, context, executemany):
        if statement == 'SELECT to_json(CURRENT_TIMESTAMP)':
            return "SELECT to_json(TIMESTAMPTZ '2026-10-27T10:00:00.123450+00:00')", parameters
        return statement, parameters
    event.listen(db.engine, 'before_cursor_execute', fixed_timestamp, retval=True)
    try:
        plan = prepare(db, **args)
    finally:
        event.remove(db.engine, 'before_cursor_execute', fixed_timestamp)
    assert plan['created_at'] == '2026-10-27T10:00:00.12345+00:00'
    assert apply(db, plan, approval=fingerprint(plan), writers_drained=True) == 'applied'


def test_runtime_role_cannot_gain_ownership_between_plan_and_apply(source):
    db, *_, args, byte_files = source
    plan = prepare(db, **args)
    with db.engine.begin() as connection:
        owner = connection.scalar(text('SELECT current_user'))
        connection.exec_driver_sql(sql.SQL('GRANT {} TO tabula_staging_runtime').format(sql.Identifier(owner)).as_string())
    before = state(db)
    try:
        with pytest.raises(ReconciliationError):
            apply(db, plan, approval=fingerprint(plan), writers_drained=True)
        assert state(db) == before
    finally:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(sql.SQL('REVOKE {} FROM tabula_staging_runtime').format(sql.Identifier(owner)).as_string())


@pytest.mark.parametrize('dependency', ['external_connections', 'identity_sessions'])
def test_prepare_rejects_move_with_new_dependencies(source, dependency):
    db, service, repo, owner, recipient, peer, a1, b1, key, args, _ = source
    with db.transaction() as connection:
        if dependency == 'identity_sessions':
            connection.execute(text('UPDATE identity_sessions SET organization_id=:org,entity_id=:entity WHERE user_id=:user'),
                               {'org': args['source_org'], 'entity': args['moved_entity'], 'user': owner.user_id})
        else:
            connection.execute(text("""INSERT INTO external_connections
                (entity_id,organization_id,provider,secret_ref,profile_key,store_scope_key,required_scopes,status)
                VALUES (:entity,:org,'google_drive',:secret,'profile_'||:entity,'unexpected','[]','pending')"""),
                {'entity': args['moved_entity'], 'org': args['source_org'], 'secret': uuid4().hex})
    before = state(db)
    with pytest.raises(ReconciliationError):
        prepare(db, **args)
    assert state(db) == before
