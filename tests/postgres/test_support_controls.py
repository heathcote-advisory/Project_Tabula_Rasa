"""Real PostgreSQL support boundaries; all data and identities are synthetic."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.infrastructure.postgres.audit import AuditReader
from tabula_rasa.infrastructure.postgres.jobs import JobQueue, JobRegistry
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError
from tabula_rasa.infrastructure.postgres.support import AUDIT_CAPABILITIES, PostgresSupport
from tests.postgres._support import account, category, simple_entry
from tests.postgres.test_identity import setup, verified
from tests.postgres.test_identity_http import client_for


@pytest.fixture
def support(database):
    repo, alice, bob, org_a, org_b = setup(database)
    client, auth, factory = client_for(database)
    operator = repo.resolve_user(verified('operator'))
    raw, session = auth.sessions.issue(verified('operator'))
    csrf = session.csrf_token
    with database.transaction() as connection:
        connection.execute(text("""INSERT INTO support_grants
            (user_id,environment,organization_id,can_admin,expires_at,approval_reference)
            VALUES (:user,'test',:org,true,clock_timestamp()+interval '1 hour','synthetic-approval')"""),
            {'user': operator.id, 'org': org_a})
        account(connection)
        category(connection)
        simple_entry(connection, amount='94721.31')
        account(connection, 'beta', 'unrelated-account')
        connection.execute(text("""INSERT INTO migration_runs(entity_id,id,incarnation_id,source_profile_key,
            source_workbook_key,source_kind,manifest_version,mapping_version,source_snapshot_sha256,extracted_at,status)
            VALUES ('alpha','failed-migration','failed-migration','private-source-profile','private-workbook',
                'current_workbook','synthetic','synthetic',:digest,clock_timestamp(),'failed')"""), {'digest': 'd' * 64})
        connection.execute(text("""UPDATE accounts SET name='private-financial-name' WHERE entity_id='alpha'"""))
        connection.execute(text("""INSERT INTO external_connections
            (entity_id,organization_id,provider,secret_ref,profile_key,store_scope_key,required_scopes,status)
            VALUES ('alpha',:org,'google_drive',:secret,'private-profile','private-folder','[]','reconnect_required')"""),
            {'org': org_a, 'secret': 'f' * 32})
    registry = JobRegistry()
    def failed_job(*_):
        raise ValueError('private-job-exception')
    registry.register('synthetic-job', failed_job)
    queue = JobQueue(database, 'alpha', registry, service_id='synthetic')
    queue.enqueue('synthetic-job', 'private-job-key', max_attempts=1)
    assert queue.execute(*queue.claim()) is False
    return PostgresSupport(database, auth.sessions), raw, csrf, repo, alice, bob, operator, org_a, org_b, client, auth, factory


def test_metadata_diagnostics_and_audit_are_bounded_redacted_and_recorded(database, support):
    service, raw, _, _, _, _, operator, org_a, _, _, _, _ = support
    diagnostic = service.diagnostics(raw, org_a, 'alpha', 'incident')
    assert diagnostic.accounts[0].id == 'cash'
    assert len(diagnostic.members) == 1
    assert diagnostic.connections[0].status == 'reconnect_required'
    assert diagnostic.jobs[0].job_type == 'synthetic-job'
    assert diagnostic.jobs[0].state == 'dead' and diagnostic.jobs[0].failure_code == 'poison'
    assert diagnostic.migrations[0].id == 'failed-migration' and diagnostic.migrations[0].status == 'failed'
    assert diagnostic.export_status == 'synchronous_not_retained'
    page = service.audit(raw, org_a, 'alpha', 'audit_review', limit=2)
    assert len(page.events) == 2 and page.next_sequence == page.events[-1].sequence
    later = service.audit(raw, org_a, 'alpha', 'audit_review', after_sequence=page.next_sequence, limit=100)
    assert all(row.sequence > page.next_sequence for row in later.events)
    assert all(row.actor_id == operator.id for row in service.audit(
        raw, org_a, 'alpha', 'audit_review', actor_id=operator.id, source='support').events)
    encoded = repr(diagnostic) + repr(page) + repr(later)
    for forbidden in ('94721.31', 'private-financial-name', 'private-folder', 'private-profile',
                      'private-job-key', 'private-job-exception', 'private-workbook', 'private-source-profile', 'unrelated-account', 'after_summary', 'before_summary', 'f' * 32):
        assert forbidden not in encoded
    reader = AuditReader(database, 'alpha', capabilities=AUDIT_CAPABILITIES)
    rows = reader.query(source='support', actor_id=operator.id)
    assert len(rows) == 4 and all(row['actor_type'] == 'operator' for row in rows)
    assert reader.verify()['status'] == 'matched'
    for statement in ('UPDATE support_actions SET reason_code=reason_code', 'DELETE FROM support_actions',
                      'TRUNCATE support_actions', 'UPDATE audit_events SET source=source'):
        with pytest.raises(DatabaseError):
            with database.transaction() as connection:
                connection.execute(text(statement))


@pytest.mark.parametrize('mode', ['none', 'revoked', 'expired', 'wrong_environment', 'disabled', 'revoked_session', 'rotated_environment'])
def test_lost_operator_authority_denies_every_call(database, support, mode):
    service, raw, _, repo, _, _, operator, org_a, _, _, auth, _ = support
    with database.transaction() as connection:
        if mode == 'none':
            connection.execute(text('DELETE FROM support_grants'))
        elif mode == 'revoked':
            connection.execute(text('UPDATE support_grants SET revoked_at=clock_timestamp()'))
        elif mode == 'expired':
            connection.execute(text("UPDATE support_grants SET expires_at=clock_timestamp()-interval '1 second'"))
        elif mode == 'wrong_environment':
            connection.execute(text("""INSERT INTO identity_environments(environment,configuration_hash)
                VALUES ('staging',:hash)"""), {'hash': 'a' * 64})
            connection.execute(text("UPDATE support_grants SET environment='staging'"))
    if mode == 'disabled':
        repo.set_user_state(operator.id, 'disabled')
    elif mode == 'revoked_session':
        auth.sessions.revoke(raw)
    elif mode == 'rotated_environment':
        with database.transaction() as connection:
            connection.execute(text('UPDATE identity_environments SET epoch=epoch+1'))
    for call in (lambda: service.diagnostics(raw, org_a, 'alpha', 'incident'),
                 lambda: service.audit(raw, org_a, 'alpha', 'audit_review'),
                 lambda: service.administer(raw, org_a, 'alpha', 'incident', 'suspend_organization')):
        with pytest.raises(IdentityError):
            call()
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM support_actions')) == 0


def test_forged_scope_never_enumerates_or_writes_other_tenants(database, support):
    service, raw, _, _, _, _, _, org_a, org_b, _, _, _ = support
    for org, scope in ((org_a, 'beta'), (org_b, 'alpha'), (org_b, 'beta'), ('unknown', 'missing')):
        for call in (lambda: service.diagnostics(raw, org, scope, 'incident'),
                     lambda: service.audit(raw, org, scope, 'audit_review'),
                     lambda: service.administer(raw, org, scope, 'incident', 'suspend_organization')):
            with pytest.raises(IdentityError, match='forbidden'):
                call()
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM support_actions')) == 0


def test_admin_preview_confirmation_read_grant_and_last_owner(database, support):
    service, raw, _, _, alice, bob, _, org_a, _, _, _, _ = support
    with pytest.raises(IdentityError):
        service.administer(raw, org_a, 'alpha', 'incident', 'restore', confirm_plan='a' * 64)
    with pytest.raises(IdentityError):
        service.administer(raw, org_a, 'alpha', 'incident', 'suspend_member', target_user_id=bob.id)
    preview = service.administer(raw, org_a, 'alpha', 'access_request', 'suspend_member', target_user_id=alice.id)
    assert not preview.applied
    with pytest.raises(IdentityError, match='concurrent_write'):
        service.administer(raw, org_a, 'alpha', 'access_request', 'suspend_member',
                           target_user_id=alice.id, confirm_plan='0' * 64)
    with pytest.raises(IdentityError, match='invariant'):
        service.administer(raw, org_a, 'alpha', 'access_request', 'suspend_member',
                           target_user_id=alice.id, confirm_plan=preview.plan_sha256)
    with database.transaction() as connection:
        connection.execute(text('UPDATE support_grants SET can_admin=false'))
    assert service.diagnostics(raw, org_a, 'alpha', 'incident')
    with pytest.raises(IdentityError, match='forbidden'):
        service.administer(raw, org_a, 'alpha', 'incident', 'suspend_organization')


def test_scoped_member_suspension_and_stale_plan_preserve_other_org(database, support):
    service, raw, _, repo, alice, bob, operator, org_a, org_b, _, auth, _ = support
    repo.set_membership(org_a, bob.id, 'editor')
    preview = service.administer(raw, org_a, 'alpha', 'incident', 'suspend_member', target_user_id=bob.id)
    repo.set_membership(org_a, bob.id, 'read_only')
    with pytest.raises(IdentityError, match='concurrent_write'):
        service.administer(raw, org_a, 'alpha', 'incident', 'suspend_member',
                           target_user_id=bob.id, confirm_plan=preview.plan_sha256)
    preview = service.administer(raw, org_a, 'alpha', 'incident', 'suspend_member', target_user_id=bob.id)
    assert service.administer(raw, org_a, 'alpha', 'incident', 'suspend_member',
                             target_user_id=bob.id, confirm_plan=preview.plan_sha256).applied
    assert [item.entity_id for item in repo.allowed_entities(bob.id)] == ['beta']
    assert repo.active_memberships(alice.id)[0]['role'] == 'owner'
    events = AuditReader(database, 'alpha', capabilities=AUDIT_CAPABILITIES).query(source='support')
    assert events[-1]['actor_id'] == operator.id
    preview = service.administer(raw, org_a, 'alpha', 'incident', 'suspend_organization')
    service.administer(raw, org_a, 'alpha', 'incident', 'suspend_organization', confirm_plan=preview.plan_sha256)
    assert not repo.allowed_entities(alice.id)
    assert repo.active_memberships(bob.id)[0]['organization_id'] == org_b
    # Operator remains authorized without relying on tenant membership or selection.
    assert auth.sessions.validate(raw).selected is None
    assert service.diagnostics(raw, org_a, 'alpha', 'incident').organization_state == 'suspended'


def test_session_revoke_is_environment_and_organization_scoped(database, support):
    service, raw, _, repo, alice, bob, _, org_a, _, _, auth, _ = support
    repo.set_membership(org_a, bob.id, 'editor')
    alpha_raw, _ = auth.sessions.issue(verified('bob'))
    auth.sessions.select(alpha_raw, 'alpha')
    beta_raw, _ = auth.sessions.issue(verified('bob'))
    auth.sessions.select(beta_raw, 'beta')
    preview = service.administer(raw, org_a, 'alpha', 'incident', 'revoke_sessions', target_user_id=bob.id)
    fresh_raw, _ = auth.sessions.issue(verified('bob'))
    auth.sessions.select(fresh_raw, 'alpha')
    with pytest.raises(IdentityError, match='concurrent_write'):
        service.administer(raw, org_a, 'alpha', 'incident', 'revoke_sessions',
                           target_user_id=bob.id, confirm_plan=preview.plan_sha256)
    from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository
    staging = PostgresSessionRepository(database, 'staging', 'b' * 64)
    staging.configure()
    staging_raw, _ = staging.issue(verified('bob'))
    staging.select(staging_raw, 'alpha')
    preview = service.administer(raw, org_a, 'alpha', 'incident', 'revoke_sessions', target_user_id=bob.id)
    service.administer(raw, org_a, 'alpha', 'incident', 'revoke_sessions',
                       target_user_id=bob.id, confirm_plan=preview.plan_sha256)
    assert staging.validate(staging_raw).selected.entity_id == 'alpha'
    with pytest.raises(IdentityError):
        auth.sessions.validate(alpha_raw)
    assert auth.sessions.validate(beta_raw).selected.entity_id == 'beta'
    assert len(repo.allowed_entities(bob.id)) == 2  # Revocation is not suspension.


def test_audit_failure_rolls_back_privileged_effect(database, support, monkeypatch):
    service, raw, _, repo, _, bob, _, org_a, _, _, _, _ = support
    repo.set_membership(org_a, bob.id, 'editor')
    preview = service.administer(raw, org_a, 'alpha', 'incident', 'suspend_member', target_user_id=bob.id)
    def fail(*_, **__):
        raise IdentityError('unavailable')
    monkeypatch.setattr(service, '_record', fail)
    with pytest.raises(IdentityError):
        service.administer(raw, org_a, 'alpha', 'incident', 'suspend_member',
                           target_user_id=bob.id, confirm_plan=preview.plan_sha256)
    assert len(repo.allowed_entities(bob.id)) == 2


@pytest.mark.parametrize('filters', [
    {'limit': 101}, {'after_sequence': -1}, {'limit': True}, {'unrecognized_filter': 'forged'},
    {'since': datetime(2026, 1, 1)}, {'since': datetime.now(timezone.utc), 'until': datetime.now(timezone.utc)-timedelta(days=1)},
])
def test_audit_budgets_and_filter_contract(support, filters):
    service, raw, _, _, _, _, _, org_a, _, _, _, _ = support
    with pytest.raises(IdentityError):
        service.audit(raw, org_a, 'alpha', 'audit_review', **filters)


def test_http_support_guards_tenant_roles_unsafe_actions_csrf_and_redacted_denials(database, support, caplog):
    service, raw, csrf, _, alice, _, _, org_a, org_b, client, auth, factory = support
    caplog.set_level('INFO', logger='tabula_rasa.http')
    url = f'/api/v1/support/{org_a}/alpha'
    headers = {'origin': auth.settings.public_origin, 'x-csrf-token': csrf}
    with client:
        client.cookies.set(auth.session_cookie, raw)
        assert client.post(url + '/diagnostics', json={'reason': 'incident'}).status_code == 403
        response = client.post(url + '/diagnostics', json={'reason': 'incident'}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        assert client.post(url + '/audit', json={'reason': 'audit_review', 'limit': 1}, headers=headers).status_code == 200
        assert client.post(url + '/administration', json={'reason': 'incident', 'operation': 'restore',
            'confirm_plan': 'a' * 64}, headers=headers).status_code == 422
        assert client.post(url + '/audit', json={'reason': 'audit_review', 'limit': 101}, headers=headers).status_code == 422
        assert client.post(f'/api/v1/support/{org_b}/beta/diagnostics', json={'reason': 'incident'}, headers=headers).status_code == 403
        for private in ('accounts', 'transactions', 'documents', 'documents/private/content'):
            assert client.get('/api/v1/entities/alpha/' + private).status_code == 403
        owner_raw, owner_session = auth.sessions.issue(verified())
        owner_csrf = owner_session.csrf_token
        client.cookies.set(auth.session_cookie, owner_raw)
        owner_headers = {'origin': auth.settings.public_origin, 'x-csrf-token': owner_csrf}
        assert client.post(url + '/diagnostics', json={'reason': 'incident'}, headers=owner_headers).status_code == 403
        factory.assert_not_called()
        assert alice.id not in caplog.text and org_a not in caplog.text and org_b not in caplog.text
        assert raw not in caplog.text and csrf not in caplog.text and 'private-folder' not in caplog.text
        events = [json.loads(record.message) for record in caplog.records if record.message.startswith('{')]
        assert any(event.get('security_event') == 'privileged_denied' for event in events)

def test_manual_provisioning_is_explicit_scoped_audited_and_revocable(database, support):
    from scripts.provision_support_operator import provision
    service, raw, _, repo, alice, _, operator, org_a, _, _, _, _ = support
    with database.transaction() as connection:
        connection.execute(text('DELETE FROM support_grants'))
    args = dict(administrator_id=alice.id, user_id=operator.id, organization_id=org_a,
                entity_id='alpha', environment='test', approval_reference='synthetic-approved',
                expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    preview = provision(database, **args)
    with pytest.raises(IdentityError):
        service.diagnostics(raw, org_a, 'alpha', 'incident')
    with pytest.raises(IdentityError, match='concurrent_write'):
        provision(database, **args, confirm_plan='0' * 64)
    assert provision(database, **args, confirm_plan=preview['plan_sha256'])['applied']
    assert service.diagnostics(raw, org_a, 'alpha', 'incident')
    with pytest.raises(IdentityError):
        service.administer(raw, org_a, 'alpha', 'incident', 'suspend_organization')
    with database.transaction(read_only=True) as connection:
        evidence = connection.execute(text("""SELECT grant_environment,grant_can_admin,grant_expires_at,
            approval_reference FROM support_actions WHERE operation='provision_grant'""")).mappings().one()
        assert evidence['grant_environment'] == 'test' and evidence['grant_can_admin'] is False
        assert evidence['grant_expires_at'] == args['expires_at']
        assert evidence['approval_reference'] == args['approval_reference']
    # Disabled operators must still be revocable by the database administrator.
    repo.set_user_state(operator.id, 'disabled')
    revoke = {**args, 'revoke': True}
    preview = provision(database, **revoke)
    assert provision(database, **revoke, confirm_plan=preview['plan_sha256'])['applied']
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT revoked_at IS NOT NULL FROM support_grants'))
    assert AuditReader(database, 'alpha', capabilities=AUDIT_CAPABILITIES).query(
        source='support_provisioning')[-1]['actor_id'] == alice.id


def test_provisioning_audit_failure_rolls_back_grant(database, support, monkeypatch):
    from scripts.provision_support_operator import provision
    service, _, _, _, alice, _, operator, org_a, _, _, _, _ = support
    args = dict(administrator_id=alice.id, user_id=operator.id, organization_id=org_a,
                entity_id='alpha', environment='test', approval_reference='synthetic',
                expires_at=datetime.now(timezone.utc)+timedelta(hours=1), can_admin=False)
    plan = provision(database, **args)['plan_sha256']
    def fail(*_, **__):
        raise IdentityError('unavailable')
    monkeypatch.setattr(service.__class__, '_record', fail)
    with pytest.raises(IdentityError):
        provision(database, **args, confirm_plan=plan)
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT can_admin FROM support_grants')) is True


def test_date_target_action_and_operation_audit_filters(support):
    service, raw, _, _, _, _, _, org_a, _, _, _, _ = support
    now = datetime.now(timezone.utc)
    events = service.audit(raw, org_a, 'alpha', 'audit_review', since=now-timedelta(days=1), until=now,
                           action='create', target_type='entries', target_id='income').events
    assert len(events) == 1 and events[0].target_key == {'id': 'income'}
    assert service.audit(raw, org_a, 'alpha', 'audit_review', operation_id=events[0].operation_id,
                         target_type='entries').events == events
    assert not service.audit(raw, org_a, 'alpha', 'audit_review', batch_id='missing-batch').events


@pytest.mark.parametrize('role', ['owner', 'admin', 'editor', 'accountant', 'read_only'])
def test_tenant_roles_never_grant_support_inspection(database, support, role):
    service, _, _, repo, _, bob, _, org_a, _, _, auth, _ = support
    repo.set_membership(org_a, bob.id, role)
    raw, _ = auth.sessions.issue(verified('bob'))
    auth.sessions.select(raw, 'alpha')
    with pytest.raises(IdentityError, match='forbidden'):
        service.audit(raw, org_a, 'alpha', 'audit_review')


def test_http_applies_only_confirmed_scope_and_operator_denial_after_revocation(database, support):
    _, raw, csrf, repo, _, bob, _, org_a, _, client, auth, _ = support
    repo.set_membership(org_a, bob.id, 'editor')
    url = f'/api/v1/support/{org_a}/alpha/administration'
    body = {'reason': 'incident', 'operation': 'suspend_member', 'target_user_id': bob.id}
    headers = {'origin': auth.settings.public_origin, 'x-csrf-token': csrf}
    with client:
        client.cookies.set(auth.session_cookie, raw)
        preview = client.post(url, json=body, headers=headers)
        assert preview.status_code == 200 and not preview.json()['applied']
        applied = client.post(url, json={**body, 'confirm_plan': preview.json()['plan_sha256']}, headers=headers)
        assert applied.status_code == 200 and applied.json()['applied']
        assert [item.entity_id for item in repo.allowed_entities(bob.id)] == ['beta']
        with database.transaction() as connection:
            connection.execute(text('UPDATE support_grants SET revoked_at=clock_timestamp()'))
        assert client.post(url, json=body, headers=headers).status_code == 403

def test_audit_pagination_snapshot_finishes_despite_recording_each_read(support):
    service, raw, _, _, _, _, _, org_a, _, _, _, _ = support
    first = service.audit(raw, org_a, 'alpha', 'audit_review', limit=1)
    assert first.next_sequence is not None
    through = first.through_sequence
    seen = list(first.events)
    cursor = first.next_sequence
    for _ in range(20):
        page = service.audit(raw, org_a, 'alpha', 'audit_review', limit=1,
                             after_sequence=cursor, through_sequence=through)
        seen.extend(page.events)
        assert all(event.sequence <= through for event in page.events)
        if page.next_sequence is None:
            break
        cursor = page.next_sequence
    else:
        pytest.fail('Audit traversal must not chase its own inspection events.')
    assert len({event.sequence for event in seen}) == len(seen)

def test_nonowner_runtime_can_append_scoped_history_but_cannot_provision_or_mutate(database, support, monkeypatch):
    from uuid import uuid4
    from tabula_rasa.infrastructure.postgres.audit import AuditContext, audit_context
    service, raw, _, _, _, _, operator, org_a, _, _, _, _ = support
    role = 'support_test_' + uuid4().hex
    with database.engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        connection.exec_driver_sql(f'GRANT SELECT ON support_grants,support_actions,audit_events TO "{role}"')
        connection.exec_driver_sql(f'GRANT INSERT ON support_actions TO "{role}"')
        connection.exec_driver_sql(f'GRANT SELECT ON identity_users,identity_bindings,organizations,memberships, '
            f'organization_entities,identity_environments,identity_sessions,business_entities,accounts, '
            f'external_connections,migration_runs,jobs,customer_lifecycle_requests TO "{role}"')
        connection.exec_driver_sql(f'GRANT SELECT (organization_id) ON beta_admissions TO "{role}"')
        connection.exec_driver_sql(f'GRANT UPDATE ON identity_sessions,identity_environments,organizations TO "{role}"')
    try:
        with audit_context(database, AuditContext('operator', operator.id, 'support', 'alpha')):
            with database.transaction() as connection:
                connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                PostgresSupport._record(connection, 'alpha', 'diagnostics', 'incident', environment='test')
        for scope, operation in (('beta', 'diagnostics'), ('alpha', 'provision_grant')):
            with pytest.raises(DatabaseError):
                with audit_context(database, AuditContext('operator', operator.id, 'support', 'alpha')):
                    with database.transaction() as connection:
                        connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                        PostgresSupport._record(connection, scope, operation, 'incident', environment='test')
        with database.transaction(read_only=True) as connection:
            connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            assert connection.scalar(text('SELECT count(*) FROM support_actions')) == 0
            connection.execute(text("SELECT set_config('tabula.audit_reader_entity','alpha',true)"))
            assert connection.scalar(text('SELECT count(*) FROM support_actions')) == 1
            assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE source='support'")) == 1
        with pytest.raises(DatabaseError):
            with database.transaction() as connection:
                connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                connection.execute(text('UPDATE support_grants SET can_admin=true'))
        with pytest.raises(DatabaseError):
            with database.transaction() as connection:
                connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                connection.execute(text("UPDATE customer_lifecycle_requests SET state='cancelled'"))
        # Exercise the complete named-session/service boundary with those actual
        # runtime privileges, not just direct SQL under the database owner.
        from contextlib import contextmanager
        original_transaction = database.transaction
        @contextmanager
        def runtime_transaction(**kwargs):
            outer = database._operation.get() is None
            with original_transaction(**kwargs) as connection:
                if outer:
                    connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                yield connection
        with monkeypatch.context() as scoped:
            scoped.setattr(database, 'transaction', runtime_transaction)
            assert service.diagnostics(raw, org_a, 'alpha', 'incident').entity_id == 'alpha'
            assert service.audit(raw, org_a, 'alpha', 'audit_review').events
    finally:
        with database.engine.begin() as connection:
            connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
            connection.exec_driver_sql(f'DROP ROLE "{role}"')

@pytest.mark.parametrize('movement', ['arriving', 'departing'])
def test_confirmed_revocation_uses_exact_locked_session_set(database, support, movement):
    from sqlalchemy import event
    from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
    from tabula_rasa.infrastructure.postgres.runtime import Database
    from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository, credential_hash
    service, raw, _, repo, _, bob, _, org_a, org_b, _, auth, _ = support
    repo.set_membership(org_a, bob.id, 'editor')
    selected_raw, _ = auth.sessions.issue(verified('bob'))
    auth.sessions.select(selected_raw, 'alpha')
    other_raw, _ = auth.sessions.issue(verified('bob'))
    auth.sessions.select(other_raw, 'beta')
    preview = service.administer(raw, org_a, 'alpha', 'incident', 'revoke_sessions', target_user_id=bob.id)
    other = Database(DatabaseSettings(database.engine.url))
    other_sessions = PostgresSessionRepository(other, 'test', 'a' * 64)
    observed = []
    def before_update(connection, cursor, statement, parameters, context, many):
        if observed or not statement.lstrip().startswith('UPDATE identity_sessions SET revoked_at=clock_timestamp()'):
            return
        observed.append(movement)
        if movement == 'arriving':
            # A real second connection moves an unconfirmed session into this
            # business between the confirmation-set read and revocation UPDATE.
            other_sessions.select(other_raw, 'alpha')
        else:
            # A confirmed session must not escape by changing its selection
            # during that same window; existing session mutation shares its lock.
            with pytest.raises(DatabaseError):
                with other.transaction() as concurrent:
                    concurrent.execute(text("SET LOCAL lock_timeout='150ms'"))
                    concurrent.execute(text("""UPDATE identity_sessions SET organization_id=:org,entity_id='beta'
                        WHERE credential_hash=:credential"""),
                        {'org': org_b, 'credential': credential_hash(selected_raw)})
    event.listen(database.engine, 'before_cursor_execute', before_update)
    try:
        result = service.administer(raw, org_a, 'alpha', 'incident', 'revoke_sessions',
                                    target_user_id=bob.id, confirm_plan=preview.plan_sha256)
        assert result.applied and observed == [movement]
    finally:
        event.remove(database.engine, 'before_cursor_execute', before_update)
        other.dispose()
    with pytest.raises(IdentityError):
        auth.sessions.validate(selected_raw)
    assert auth.sessions.validate(other_raw).selected.entity_id == ('alpha' if movement == 'arriving' else 'beta')


def test_support_rebinds_same_database_sessions_and_rejects_foreign_database(database, support):
    from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
    from tabula_rasa.infrastructure.postgres.runtime import Database
    from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository

    _, raw, _, _, _, bob, _, org_a, _, _, auth, _ = support
    other_pool = Database(DatabaseSettings(database.engine.url))
    foreign = Database(DatabaseSettings(database.engine.url.set(database='unrelated_database')))
    try:
        sessions = PostgresSessionRepository(other_pool, auth.sessions.environment,
            auth.sessions.configuration_hash, clock=auth.sessions.clock)
        service = PostgresSupport(database, sessions)
        assert service.sessions.database is database
        assert service.sessions.clock is sessions.clock
        assert service.diagnostics(raw, org_a, 'alpha', 'incident').organization_id == org_a
        # The target has no sessions in this organization; PostgreSQL infers the
        # empty ANY parameter from cache_id's text type and safely updates zero rows.
        support[3].set_membership(org_a, bob.id, 'editor')
        preview = service.administer(raw, org_a, 'alpha', 'incident',
            'revoke_sessions', target_user_id=bob.id)
        assert service.administer(raw, org_a, 'alpha', 'incident', 'revoke_sessions',
            target_user_id=bob.id, confirm_plan=preview.plan_sha256).applied
        with pytest.raises(ValueError, match='runtime session database'):
            PostgresSupport(database, PostgresSessionRepository(foreign,
                sessions.environment, sessions.configuration_hash))
    finally:
        other_pool.dispose()
        foreign.dispose()
