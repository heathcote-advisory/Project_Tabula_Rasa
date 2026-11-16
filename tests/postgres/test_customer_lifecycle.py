"""Synthetic closure, restart, access, immutable evidence and tenant isolation."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.infrastructure.postgres.customer_lifecycle import PostgresCustomerLifecycle
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError
from tests.postgres.test_identity import setup, verified
from tests.postgres.test_identity_http import client_for


@pytest.fixture
def lifecycle(database):
    repo, alice, bob, a, b = setup(database)
    client, auth, factory = client_for(database)
    raw, session = auth.sessions.issue(verified())
    auth.sessions.select(raw, 'alpha')
    session = auth.sessions.validate(raw)
    service = PostgresCustomerLifecycle(database)
    return repo, alice, bob, a, b, client, auth, factory, raw, session, service


def test_reversible_closure_preserves_rows_exports_and_other_business(database, lifecycle):
    repo, alice, bob, a, b, client, auth, factory, raw, session, service = lifecycle
    request = service.request(session, uuid4().hex, 'business_closure', 'alpha')
    assert service.request(session, request['id'], 'business_closure', 'alpha') == request
    with pytest.raises(IdentityError, match='required'):
        service.change(session, request['id'], 'confirm', 1)
    closed = service.change(session, request['id'], 'confirm', 1, export_saved=True)
    assert closed['effective_state'] == 'read_only'
    assert closed['recover_until'] - closed['confirmed_at'] == timedelta(days=60)
    assert service.change(session, request['id'], 'confirm', 1, export_saved=True) == closed
    fresh = auth.info(auth.sessions.validate(raw))
    assert 'bookkeeping:write' not in fresh.capabilities
    assert 'bookkeeping:export' in fresh.capabilities
    assert 'ownership:manage' in fresh.capabilities
    assert fresh.cache_scope != auth.scope(session)
    with pytest.raises(DatabaseError, match='forbidden'), database.transaction() as c:
        c.execute(text("UPDATE business_entities SET name='blocked' WHERE id='alpha'"))
    with database.transaction() as c:
        c.execute(text("UPDATE business_entities SET name='Synthetic B' WHERE id='beta'"))
    # Restart uses persisted state and another current Owner can cancel.
    repo.set_membership(a, bob.id, 'owner')
    _, second = auth.sessions.issue(verified('bob'))
    cancelled = PostgresCustomerLifecycle(database).change(second, request['id'], 'cancel', 2)
    assert cancelled['state'] == 'cancelled'
    assert cancelled['confirmed_at'] == closed['confirmed_at']
    assert cancelled['recover_until'] == closed['recover_until']
    assert auth.info(auth.sessions.validate(raw)).cache_scope != fresh.cache_scope
    assert auth.sessions.validate(raw).selected.closure_expires_at is None
    with database.transaction() as c:
        c.execute(text("UPDATE business_entities SET name='Reopened' WHERE id='alpha'"))
        assert c.scalar(text('SELECT count(*) FROM business_entities')) == 2
    status = service.status(session, 'alpha')
    assert not status['deletion_available'] and status['deletion_blockers']
    assert [event['action'] for event in status['requests'][0]['events']] == ['requested', 'confirmed', 'cancelled']


def test_expiry_is_enforced_without_worker_or_page_mutation(database, lifecycle):
    _, _, _, _, _, _, auth, _, raw, session, service = lifecycle
    row = service.request(session, uuid4().hex, 'business_closure', 'alpha')
    service.change(session, row['id'], 'confirm', 1, export_saved=True)
    with database.transaction() as c:
        c.execute(text("UPDATE customer_lifecycle_requests SET recover_until=clock_timestamp()-interval '1 second' WHERE id=:id"), {'id': row['id']})
    assert auth.sessions.validate(raw).selected is None
    assert service.status(session, 'alpha')['requests'][0]['effective_state'] == 'closed_retained'
    with pytest.raises(IdentityError, match='conflict'):
        service.change(session, row['id'], 'cancel', 2)
    with database.transaction(read_only=True) as c:
        assert c.scalar(text("SELECT count(*) FROM business_entities WHERE id='alpha'")) == 1


def test_native_authority_freshness_last_owner_and_failed_transaction(database, lifecycle):
    repo, alice, bob, a, _, _, auth, _, _, session, service = lifecycle
    with pytest.raises(IdentityError, match='forbidden'):
        service.request(session, uuid4().hex, 'business_closure', 'beta')
    with pytest.raises(IdentityError, match='invariant'):
        service.request(session, uuid4().hex, 'account_closure')
    row = service.request(session, uuid4().hex, 'privacy_erasure')
    stale = replace(session, authenticated_at=session.authenticated_at-timedelta(minutes=10))
    with pytest.raises(IdentityError, match='reauthentication_required'):
        service.change(stale, row['id'], 'confirm', 1)
    # Event persistence failure rolls back the state; retry is safe after restart.
    with patch.object(service, '_event', side_effect=IdentityError('unavailable')):
        with pytest.raises(IdentityError, match='unavailable'):
            service.change(session, row['id'], 'confirm', 1)
    assert PostgresCustomerLifecycle(database).status(session)['requests'][0]['state'] == 'pending'
    service.change(session, row['id'], 'confirm', 1)
    with pytest.raises(DatabaseError), database.transaction() as c:
        c.execute(text('DELETE FROM customer_lifecycle_events WHERE request_id=:id'), {'id': row['id']})
    repo.set_membership(a, bob.id, 'owner')
    repo.set_membership(a, alice.id, 'admin')
    with pytest.raises(IdentityError, match='forbidden'):
        service.status(session, 'alpha')
    repo.set_user_state(alice.id, 'disabled')
    with pytest.raises(IdentityError, match='forbidden'):
        service.status(session)


def test_http_csrf_cross_tenant_and_disabled_identity(database, lifecycle):
    repo, alice, bob, a, _, client, auth, factory, raw, session, _ = lifecycle
    with client:
        client.cookies.set(auth.session_cookie, raw)
        headers = {'origin': auth.settings.public_origin, 'x-csrf-token': session.csrf_token}
        base = '/api/v1/customer-lifecycle'
        body = {'request_id': uuid4().hex, 'kind': 'business_closure', 'entity_id': 'alpha'}
        assert client.post(base, json=body).status_code == 403
        assert client.post(base, json={**body, 'entity_id': 'beta'}, headers=headers).status_code == 403
        assert client.post(base, json={**body, 'kind': 'privacy_erasure'}, headers=headers).status_code == 422
        response = client.post(base, json=body, headers=headers)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        record = response.json()
        assert client.post(base+'/'+record['id'], json={'action': 'delete', 'version': 1}, headers=headers).status_code == 422
        assert client.post(base+'/'+record['id'], json={'action': 'confirm', 'version': 1, 'export_saved': True}, headers=headers).status_code == 200
        assert 'bookkeeping:export' in client.get('/api/v1/session').json()['capabilities']
        assert client.post('/api/v1/entities/alpha/payees', json={'name': 'blocked'}, headers=headers).status_code == 403
        assert client.get(base, params={'business_id': 'beta'}).status_code == 403
        repo.set_membership(a, bob.id, 'owner')
        repo.set_user_state(alice.id, 'disabled')
        assert client.get(base).status_code == 401
    factory.assert_not_called()


def test_privacy_cases_are_personal_unique_and_survive_membership_revocation(database, lifecycle):
    repo, alice, bob, a, _, _, auth, _, _, session, service = lifecycle
    repo.set_membership(a, bob.id, 'owner')
    _, other = auth.sessions.issue(verified('bob'))
    with pytest.raises(IdentityError, match='validation'):
        service.request(session, uuid4().hex, 'privacy_erasure', 'alpha')
    with pytest.raises(DatabaseError, match='invariant'), database.transaction() as c:
        c.execute(text('''INSERT INTO customer_lifecycle_requests(id,requester_id,entity_id,kind,policy_version)
            VALUES (:id,:actor,'alpha','privacy_erasure','customer-lifecycle-v1')'''),
            {'id': uuid4().hex, 'actor': alice.id})
    case = service.request(session, uuid4().hex, 'privacy_erasure')
    assert service.request(session, case['id'], 'privacy_erasure') == case
    with pytest.raises(IdentityError, match='duplicate'):
        service.request(session, uuid4().hex, 'privacy_erasure')
    assert service.status(other, 'alpha')['requests'] == []
    for action in ('confirm', 'cancel'):
        with pytest.raises(IdentityError, match='forbidden'):
            service.change(other, case['id'], action, 1)
    repo.set_membership(a, alice.id, 'owner', active=False)
    assert service.status(session)['requests'][0]['id'] == case['id']
    assert service.change(session, case['id'], 'confirm', 1)['state'] == 'confirmed'
    assert service.change(session, case['id'], 'cancel', 2)['state'] == 'cancelled'


@pytest.mark.parametrize('inactive_target', ['organization', 'binding'])
def test_unselected_status_excludes_inactive_businesses_but_keeps_personal_cases(database, lifecycle, inactive_target):
    repo, alice, _, org, other_org, _, _, _, _, session, service = lifecycle
    repo.set_membership(other_org, alice.id, 'owner')
    closure = service.request(session, uuid4().hex, 'business_closure', 'alpha')
    other = service.request(session, uuid4().hex, 'business_closure', 'beta')
    personal = service.request(session, uuid4().hex, 'privacy_erasure')
    assert {r['id'] for r in service.status(session)['requests']} == {closure['id'], other['id'], personal['id']}
    with database.transaction() as c:
        if inactive_target == 'organization':
            c.execute(text("UPDATE organizations SET state='suspended' WHERE id=:org"), {'org': org})
        else:
            c.execute(text("UPDATE organization_entities SET state='inactive' WHERE entity_id='alpha'"))
    assert {r['id'] for r in service.status(session)['requests']} == {other['id'], personal['id']}
    with pytest.raises(IdentityError, match='forbidden'):
        service.status(session, 'alpha')
    with pytest.raises(IdentityError, match='forbidden'):
        service.change(session, closure['id'], 'confirm', 1, export_saved=True)
    assert service.change(session, personal['id'], 'confirm', 1)['state'] == 'confirmed'
    with database.transaction() as c:
        if inactive_target == 'organization':
            c.execute(text("UPDATE organizations SET state='active' WHERE id=:org"), {'org': org})
        else:
            c.execute(text("UPDATE organization_entities SET state='active' WHERE entity_id='alpha'"))
    assert {r['id'] for r in service.status(session)['requests']} == {closure['id'], other['id'], personal['id']}


def test_login_closure_rechecks_last_owner_and_revokes_all_sessions(database, lifecycle):
    repo, alice, bob, a, _, _, auth, _, raw, session, service = lifecycle
    repo.set_membership(a, bob.id, 'owner')
    row = service.request(session, uuid4().hex, 'account_closure')
    repo.set_membership(a, bob.id, 'read_only')
    with pytest.raises(IdentityError, match='invariant'):
        service.change(session, row['id'], 'confirm', 1)
    assert auth.sessions.validate(raw).user_id == alice.id
    repo.set_membership(a, bob.id, 'owner')
    second, _ = auth.sessions.issue(verified())
    bob_raw, _ = auth.sessions.issue(verified('bob'))
    with patch.object(service, '_event', side_effect=IdentityError('unavailable')):
        with pytest.raises(IdentityError, match='unavailable'):
            service.change(session, row['id'], 'confirm', 1)
    assert auth.sessions.validate(raw).user_id == alice.id
    assert auth.sessions.validate(second).user_id == alice.id
    result = service.change(session, row['id'], 'confirm', 1)
    assert result['state'] == 'confirmed'
    for credential in (raw, second):
        with pytest.raises(IdentityError, match='unauthorized'):
            auth.sessions.validate(credential)
    assert auth.sessions.validate(bob_raw).user_id == bob.id
    with database.transaction(read_only=True) as c:
        assert c.scalar(text('SELECT count(*) FROM business_entities')) == 2
        assert c.scalar(text('SELECT count(*) FROM identity_users WHERE id=:actor'), {'actor': alice.id}) == 1
        assert c.scalar(text('SELECT count(*) FROM identity_sessions WHERE user_id=:actor AND revoked_at IS NULL'), {'actor': alice.id}) == 0


def test_concurrent_confirmation_and_durable_export_boundary(database, lifecycle):
    _, _, _, _, _, _, _, _, _, session, service = lifecycle
    row = service.request(session, uuid4().hex, 'business_closure', 'alpha')
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: PostgresCustomerLifecycle(database).change(
            session, row['id'], 'confirm', 1, export_saved=True), range(2)))
    assert results[0] == results[1]
    from tabula_rasa.application.entity_export import EntityExportService
    from tabula_rasa.infrastructure.postgres.entity_export import PostgresEntityExportRepository
    artifact = EntityExportService(PostgresEntityExportRepository(database, 'alpha')).export()
    assert len(artifact['domains']) == 27
    assert not any('lifecycle' in key for key in artifact['domains'])
    with database.transaction(read_only=True) as c:
        assert c.scalar(text("SELECT count(*) FROM customer_lifecycle_events WHERE request_id=:id AND action='confirmed'"), {'id': row['id']}) == 1
        # Native guards cover every canonical domain, so workers/import adapters
        # cannot evade the HTTP read-only capability mask.
        from tabula_rasa.bookkeeping_export.schema import DOMAINS
        guarded = set(c.execute(text("SELECT event_object_table FROM information_schema.triggers WHERE trigger_name='customer_closure_guard'")).scalars())
        assert set(DOMAINS) <= guarded


def test_named_period_command_drains_before_business_closure(database, lifecycle):
    from tabula_rasa.infrastructure.postgres.audit import AuditContext, audit_context
    from tabula_rasa.infrastructure.postgres.periods import PostgresPeriodRepository
    from tests.postgres.test_periods import operator

    _, alice, _, _, _, _, _, _, _, session, service = lifecycle
    periods = PostgresPeriodRepository(database, 'alpha')
    with operator(database):
        periods.configure(enabled=True, reason='Adopt control')
    request = service.request(session, uuid4().hex, 'business_closure', 'alpha')
    held, release = Event(), Event()

    def pause_after_authority(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('SELECT require_period_authority('):
            held.set()
            assert release.wait(10)

    def close_period():
        with audit_context(database, AuditContext('user', alice.id, 'test', 'alpha')):
            return periods.change('2026-10-01', closed=True, reason='Close month', expected_version=0)

    event.listen(database.engine, 'after_cursor_execute', pause_after_authority)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(close_period)
            assert held.wait(10)
            second = pool.submit(service.change, session, request['id'], 'confirm', 1, export_saved=True)
            try:
                deadline = monotonic() + 5
                blocked = False
                while monotonic() < deadline:
                    with database.transaction(read_only=True) as c:
                        blocked = c.scalar(text('''SELECT EXISTS (SELECT 1 FROM pg_locks
                            WHERE locktype='advisory' AND classid=346 AND NOT granted
                            AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))'''))
                    if blocked:
                        break
                    sleep(0.01)
                assert blocked
                # The closure fence is scoped; another business is still writable.
                with database.transaction() as c:
                    c.execute(text("UPDATE business_entities SET name='Still active' WHERE id='beta'"))
            finally:
                release.set()
            assert first.result(timeout=10)['closed']
            assert second.result(timeout=10)['effective_state'] == 'read_only'
        with audit_context(database, AuditContext('user', alice.id, 'test', 'alpha')):
            with pytest.raises(IdentityError, match='forbidden'):
                periods.change('2026-10-01', closed=False, reason='Too late', expected_version=1)
    finally:
        release.set()
        event.remove(database.engine, 'after_cursor_execute', pause_after_authority)


def test_real_http_export_survives_closure_but_not_access_expiry(database):
    from tests.postgres.test_authorization_http import named_client
    with named_client(database) as (client, auth, _, _, _, _, headers):
        session = auth.sessions.validate(client.cookies.get(auth.session_cookie))
        service = PostgresCustomerLifecycle(database)
        row = service.request(session, uuid4().hex, 'business_closure', 'alpha')
        service.change(session, row['id'], 'confirm', 1, export_saved=True)
        result = client.post('/api/v1/entities/alpha/exports/handoff', headers=headers, json={'scope': 'metadata_only'})
        assert result.status_code == 200 and result.content.startswith(b'PK')
        with database.transaction() as c:
            c.execute(text("UPDATE customer_lifecycle_requests SET recover_until=clock_timestamp()-interval '1 second' WHERE id=:id"), {'id': row['id']})
        assert client.post('/api/v1/entities/alpha/exports/handoff', headers=headers, json={'scope': 'metadata_only'}).status_code == 403
        assert client.get('/api/v1/customer-lifecycle').json()['requests'][0]['effective_state'] == 'closed_retained'


def test_queued_recurring_worker_cannot_write_a_closed_business(database):
    from tests.postgres.core_support import seed_core
    from tests.postgres.secondary_support import schedule
    from tests.postgres.test_recurring_jobs import activate, enqueue_reconciliation, worker
    from tabula_rasa.infrastructure.postgres.control_plane import PostgresIdentityRepository
    alpha, beta = seed_core(database)
    for service in (alpha, beta):
        service.recurring.create(schedule_id='same', **schedule())
    repo = PostgresIdentityRepository(database)
    owner = repo.resolve_user(verified())
    org = repo.create_organization(owner.id, 'Synthetic closure')
    repo.bind_entity(org, 'alpha')
    _, auth, _ = client_for(database)
    _, session = auth.sessions.issue(verified())
    for scope in ('alpha', 'beta'):
        activate(database, scope)
        enqueue_reconciliation(database, scope, through_date='2026-02-01')
    lifecycle = PostgresCustomerLifecycle(database)
    row = lifecycle.request(session, uuid4().hex, 'business_closure', 'alpha')
    lifecycle.change(session, row['id'], 'confirm', 1, export_saved=True)
    queue = worker(database)
    claim = queue.claim()
    assert claim and queue.execute(*claim) is False
    assert alpha.transactions.list() == []
    healthy = worker(database, 'beta')
    claim = healthy.claim()
    assert claim and healthy.execute(*claim) is True
    assert len(beta.transactions.list()) == 2
    with database.transaction(read_only=True) as c:
        assert c.scalar(text("SELECT count(*) FROM job_attempts WHERE entity_id='alpha'")) == 1
        assert c.scalar(text("SELECT count(*) FROM recurring_schedules WHERE entity_id='alpha'")) == 1
