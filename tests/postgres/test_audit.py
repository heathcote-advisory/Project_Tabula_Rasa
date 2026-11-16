"""Native atomicity, honest identity, isolation, privacy and audit recovery."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from time import monotonic
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.errors import StorageConflictError, StorageValidationError
from tabula_rasa.infrastructure.postgres.audit import AuditContext, AuditReader, audit_context
from tabula_rasa.infrastructure.postgres.migrations import migration_config, upgrade
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError
from tests.postgres.core_support import seed_core, expense, journal_lines
from tests.postgres.test_mileage_repository import trip, source
from tests.postgres.test_import_repository import csv, qb
from tests.postgres._support import account, category, entity, simple_entry

CAPABILITIES = frozenset({'platform:operate', 'audit:inspect'})


def reader(database, scope='alpha'):
    return AuditReader(database, scope, capabilities=CAPABILITIES)


def test_crud_is_atomic_redacted_filterable_and_stale_edits_have_no_event(database):
    alpha, beta = seed_core(database)
    context = AuditContext('operator', 'test-operator', 'correction', 'alpha', correlation_id='request-1')
    with audit_context(database, context):
        original = alpha.transactions.create(transaction_id='expense', **expense(description='private financial memo'))
        changed = alpha.transactions.update('expense', expected=original, amount='15')
        before = reader(database).query()
        with pytest.raises(StorageConflictError):
            alpha.transactions.update('expense', expected=original, amount='18')
        assert reader(database).query() == before
        alpha.transactions.delete('expense', expected=changed)
    events = reader(database).query(target_type='entries', target_id='expense')
    assert {e['action'] for e in events} == {'create', 'update', 'delete'}
    assert all(e['actor_type'] == 'operator' and e['actor_id'] == 'test-operator' for e in events)
    assert all(e['correlation_id'] == 'request-1' and e['source'] == 'correction' for e in events)
    assert 'private financial memo' not in str(reader(database).query())
    assert events[-1]['after_summary'] is None
    assert reader(database).verify()['mismatches'] == 0
    assert reader(database, 'beta').query(target_type='entries') == ()
    assert beta.transactions.list() == []
    assert reader(database).query(source='correction', action='delete', actor_id='test-operator')
    assert reader(database).query(organization_id='foreign', target_id='expense') == ()
    assert reader(database).query(since=events[0]['occurred_at'], until=events[0]['occurred_at']) == ()
    assert reader(database).query(after_sequence=events[-1]['sequence'], target_type='entries') == ()


def test_aborted_ledger_and_failed_audit_both_rollback_without_phantom_event(database, monkeypatch):
    from tabula_rasa.infrastructure.postgres.accounting import PostgresTransactionRepository
    alpha, _ = seed_core(database)
    before = reader(database).query()
    write = PostgresTransactionRepository.write_lines

    def unbalance(self, connection, identity, lines):
        write(self, connection, identity, lines)
        connection.execute(text("UPDATE postings SET amount=amount+1 WHERE entry_id=:id AND position=2"), {'id': identity})

    monkeypatch.setattr(PostgresTransactionRepository, 'write_lines', unbalance)
    with pytest.raises(StorageValidationError):
        alpha.transactions.create(transaction_id='failed', **expense(new_payee_name='private vendor'))
    assert not alpha.transactions.list() and not alpha.payees.list()
    assert reader(database).query() == before
    monkeypatch.undo()
    # A missing actor fails at commit and rolls back the otherwise valid ledger.
    with pytest.raises(DatabaseError):
        with database.transaction() as connection:
            connection.execute(text("SELECT set_config('tabula.audit_context','',true)"))
            alpha.transactions.create(transaction_id='no-actor', **expense())
    assert not alpha.transactions.list() and reader(database).query() == before
    with pytest.raises(RuntimeError):
        with database.transaction():
            alpha.transactions.create(transaction_id='rollback', **expense())
            raise RuntimeError('synthetic rollback')
    assert not alpha.transactions.list() and reader(database).query() == before


def test_named_attribution_and_entity_boundary_reject_invalid_or_foreign_actors(database):
    from tests.postgres.test_identity import setup
    from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
    repo, alice, bob, org_a, _ = setup(database)
    alpha = create_postgres_services(database, 'alpha')
    with audit_context(database, AuditContext('user', alice.id, 'account.create', 'alpha')):
        made = alpha.accounts.create('Private name', 'Bank', '0')
    event, = reader(database).query(target_type='accounts', target_id=made['id'], action='create')
    assert event['actor_id'] == alice.id and event['organization_id'] == org_a
    for actor, scope in ((bob.id, 'alpha'), (alice.id, 'beta'), ('unknown', 'alpha')):
        with pytest.raises(StorageValidationError):
            with audit_context(database, AuditContext('user', actor, 'account.create', scope)):
                alpha.accounts.create('Forbidden', 'Bank', '0')
    with pytest.raises(ValueError):
        AuditContext('user', '', 'http', 'alpha')
    with pytest.raises(ValueError):
        AuditContext('user', 'alice', 'http')
    with pytest.raises(ValueError):
        with database.transaction(), audit_context(database, AuditContext('operator', 'test', 'test')):
            pass
    repo.set_membership(org_a, bob.id, 'owner')
    repo.set_membership(org_a, alice.id, 'read_only')
    with pytest.raises(StorageValidationError):
        with audit_context(database, AuditContext('user', alice.id, 'account.create', 'alpha')):
            alpha.accounts.create('Forbidden', 'Bank', '0')
    assert len(alpha.accounts.list()) == 1
    for caps in (frozenset(), frozenset({'bookkeeping:read', 'bookkeeping:export'})):
        with pytest.raises(IdentityError):
            AuditReader(database, 'alpha', capabilities=caps)


def test_events_cannot_be_forged_edited_deleted_or_truncated(database):
    seed_core(database)
    for sql in ("UPDATE audit_events SET actor_id='forged'", 'DELETE FROM audit_events',
                'TRUNCATE audit_events', 'TRUNCATE audit_baseline', 'DELETE FROM audit_activation',
                "INSERT INTO audit_baseline(entity_id,target_type,target_key,row_digest) VALUES ('alpha','entries','{}','forged')",
                "INSERT INTO audit_events(entity_id,operation_id,actor_type,actor_id,correlation_id,source,action,target_type,target_key) "
                "VALUES ('alpha','forged','system','forged','','test','create','entries','{}')"):
        with pytest.raises(DatabaseError):
            with database.transaction() as connection:
                connection.execute(text(sql))
    assert reader(database).verify()['mismatches'] == 0


def test_native_nonowner_rls_scopes_audit_rows(database):
    seed_core(database)
    role = 'audit_test_' + uuid4().hex
    # Cluster role has no login, privileges or ownership; removed after this test.
    with database.engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        connection.exec_driver_sql(f'GRANT SELECT ON audit_events,audit_baseline TO "{role}"')
    try:
        with database.transaction(read_only=True) as connection:
            connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            assert connection.scalar(text('SELECT count(*) FROM audit_events')) == 0
            connection.execute(text("SELECT set_config('tabula.audit_reader_entity','alpha',true)"))
            assert set(connection.execute(text('SELECT entity_id FROM audit_events')).scalars()) == {'alpha'}
    finally:
        with database.engine.begin() as connection:
            connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
            connection.exec_driver_sql(f'DROP ROLE "{role}"')


def test_activation_marks_existing_history_and_verifier_detects_unsupported_sql(empty_database):
    database = empty_database
    with database.engine.begin() as connection:
        command.upgrade(migration_config(connection), '0010_workspace_lifecycle')
    alpha, _ = seed_core(database)
    made = alpha.transactions.create(transaction_id='old', **expense())
    upgrade(database)
    assert not reader(database).query()
    checked = reader(database).verify()
    assert checked['mismatches'] == 0 and checked['historical_unavailable'] > 0
    alpha.transactions.update('old', expected=made, amount='20')
    assert reader(database).verify()['mismatches'] == 0
    assert reader(database).verify()['historical_unavailable'] == checked['historical_unavailable']
    assert reader(database).boundary(target_type='entries', target_key={'id': 'old'})['history_status'] == 'pre_activation_unavailable'
    # Administrator SQL with triggers disabled is outside the historical promise.
    with database.engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(text("UPDATE entries SET description='unsupported edit' WHERE id='old'"))
    assert reader(database).verify()['mismatches'] == 1


@pytest.mark.parametrize('pre_activation', [True, False])
def test_posting_namespaces_keep_distinct_baselines_and_events(empty_database, pre_activation):
    database = empty_database
    if pre_activation:
        with database.engine.begin() as connection:
            command.upgrade(migration_config(connection), '0011_control_plane_join')
    else:
        upgrade(database)
    with database.transaction() as connection:
        for scope in ('alpha', 'beta'):
            entity(connection, scope)
            account(connection, scope)
            category(connection, scope)
            simple_entry(connection, scope)
            connection.execute(text("""INSERT INTO entries(entity_id,id,incarnation_id,txn_date,projection_kind)
                VALUES (:scope,'journal','journal','2026-10-05','journal')"""), {'scope': scope})
            connection.execute(text("""INSERT INTO postings(entity_id,id,entry_id,projection_kind,position,side,amount,account_id)
                VALUES (:scope,'income:1','journal','journal',1,'Debit',2,'cash'),
                       (:scope,'income:2','journal','journal',2,'Credit',2,'cash')"""), {'scope': scope})
    if pre_activation:
        upgrade(database)
    expected = {(kind, f'income:{position}') for kind in ('simple', 'journal') for position in (1, 2)}
    for scope in ('alpha', 'beta'):
        if pre_activation:
            with reader(database, scope).operation() as connection:
                keys = connection.execute(text("""SELECT target_key FROM audit_baseline
                    WHERE entity_id=:scope AND target_type='postings'"""), {'scope': scope}).scalars().all()
        else:
            keys = [event['target_key'] for event in reader(database, scope).query(target_type='postings')]
        assert {(key['projection_kind'], key['id']) for key in keys} == expected
        assert len(keys) == 4
        assert reader(database, scope).verify()['mismatches'] == 0
    beta_before = reader(database, 'beta').query()
    with database.transaction() as connection:
        connection.execute(text("""UPDATE postings SET amount=3
            WHERE entity_id='alpha' AND projection_kind='journal'"""))
    changed = reader(database).query(target_type='postings', action='update')
    assert len(changed) == 2
    assert {event['target_key']['projection_kind'] for event in changed} == {'journal'}
    assert all(event['after_summary']['amount'] == 3 for event in changed)
    assert reader(database).verify()['mismatches'] == 0
    with pytest.raises(DatabaseError):
        with database.transaction() as connection:
            connection.execute(text("""UPDATE postings SET amount=0
                WHERE entity_id='alpha' AND projection_kind='journal'"""))
    assert reader(database).query(target_type='postings', action='update') == changed
    assert reader(database, 'beta').query() == beta_before
    assert reader(database, 'beta').verify()['mismatches'] == 0


@pytest.mark.parametrize('pre_activation', [True, False])
def test_migration_checkpoint_and_mapping_history_tracks_full_keys(empty_database, pre_activation, monkeypatch):
    from tabula_rasa.infrastructure.postgres.audit import AUDITED_KEYS
    from tabula_rasa.infrastructure.postgres.migration import PostgresMigrationRunner
    from tests.test_sheets_migration_source import plan_for, values_with_records

    database = empty_database
    if pre_activation:
        with database.engine.begin() as connection:
            command.upgrade(migration_config(connection), '0011_control_plane_join')
    else:
        upgrade(database)
    plans = [plan_for(values_with_records(journal=True), profile=scope, entity=scope, workbook=f'workbook-{scope}')
             for scope in ('alpha', 'beta')]
    runner = PostgresMigrationRunner(database)
    # Model loading with the prior checkout before deploying the audit revision.
    with monkeypatch.context() as previous_checkout:
        if pre_activation:
            previous_checkout.setattr('tabula_rasa.infrastructure.postgres.migration.head_revision',
                                      lambda: '0011_control_plane_join')
            from tabula_rasa.infrastructure.postgres.migration import ROOT_TABLES
            previous_checkout.setattr('tabula_rasa.infrastructure.postgres.migration.ROOT_TABLES',
                                      tuple(table for table in ROOT_TABLES if table != 'vehicles'))
        for plan in plans:
            runner.execute(plan, dry_run=False)
    if pre_activation:
        upgrade(database)
    for scope in ('alpha', 'beta'):
        for table in ('migration_units', 'migration_id_mappings'):
            with reader(database, scope).operation() as connection:
                columns = ','.join(f"'{key}',{key}" for key in AUDITED_KEYS[table])
                expected = connection.execute(text(
                    f'SELECT jsonb_build_object({columns}) FROM {table} WHERE entity_id=:scope'
                ), {'scope': scope}).scalars().all()
                if pre_activation:
                    captured = connection.execute(text('''SELECT target_key FROM audit_baseline
                        WHERE entity_id=:scope AND target_type=:table'''),
                        {'scope': scope, 'table': table}).scalars().all()
                else:
                    captured = [event['target_key'] for event in reader(database, scope).query(target_type=table, action='create')]
            assert expected and len(captured) == len(expected)
            assert all(key in captured for key in expected)
        assert reader(database, scope).verify()['mismatches'] == 0
    before = reader(database).query()
    # Failed checkpoint mutations produce no history and no changed evidence.
    with pytest.raises(DatabaseError):
        with database.transaction() as connection:
            connection.execute(text("UPDATE migration_units SET status='invalid' WHERE entity_id='alpha'"))
    assert reader(database).query() == before
    beta_before = reader(database, 'beta').query()
    with database.transaction() as connection:
        connection.execute(text("UPDATE migration_units SET status='pending',loaded_at=NULL WHERE entity_id='alpha'"))
        connection.execute(text("UPDATE migration_id_mappings SET mapping_reason='corrected' WHERE entity_id='alpha'"))
    assert reader(database).query(target_type='migration_units', action='update')
    assert reader(database).query(target_type='migration_id_mappings', action='update')
    assert reader(database).verify()['mismatches'] == 0
    assert reader(database, 'beta').query() == beta_before
    # The verifier detects unsupported administrator tampering of each evidence table.
    with database.engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(text("UPDATE migration_units SET unit_kind='tampered' WHERE entity_id='alpha'"))
        connection.execute(text("UPDATE migration_id_mappings SET mapping_reason='tampered' WHERE entity_id='alpha'"))
    assert reader(database).verify()['mismatches'] > 0
    assert reader(database, 'beta').verify()['mismatches'] == 0


def test_activation_drains_inflight_writer_before_baseline_snapshot(empty_database):
    database = empty_database
    with database.engine.begin() as connection:
        command.upgrade(migration_config(connection), '0010_workspace_lifecycle')
    with database.transaction() as connection:
        entity(connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.transaction() as writer:
            account(writer, account_id='inflight')
            migration = pool.submit(upgrade, database)
            deadline = monotonic() + 5
            with database.engine.connect() as observer:
                while not observer.scalar(text("""SELECT EXISTS (SELECT 1 FROM pg_locks
                    WHERE relation='accounts'::regclass AND mode='ShareRowExclusiveLock' AND NOT granted)""")):
                    assert monotonic() < deadline and not migration.done()
                    Event().wait(0.01)
            assert not migration.done()
            committed_after = writer.scalar(text('SELECT clock_timestamp()'))
        migration.result(timeout=10)
    boundary = reader(database).boundary(target_type='accounts', target_key={'id': 'inflight'})
    assert boundary['activated_at'] >= committed_after
    assert boundary['pre_activation_records'] == 1
    assert reader(database).verify()['mismatches'] == 0
    assert reader(database).query() == ()  # Inflight history is a gap marker, not an invented event.


def test_stable_operation_replay_does_not_silently_discard_a_second_mutation(database):
    alpha, _ = seed_core(database)
    context = AuditContext('operator', 'test', 'correction', 'alpha', operation_id='one-logical-operation')
    made = alpha.transactions.create(transaction_id='expense', **expense())
    with audit_context(database, context):
        changed = alpha.transactions.update('expense', expected=made, amount='20')
    before = reader(database).query()
    with pytest.raises(StorageConflictError):
        with audit_context(database, context):
            alpha.transactions.update('expense', expected=changed, amount='30')
    assert alpha.transactions.list()[0]['amount'] == '20.00' and reader(database).query() == before


def test_concurrent_stale_edits_only_one_committed_change_event(database):
    alpha, _ = seed_core(database)
    made = alpha.transactions.create(transaction_id='expense', **expense())
    before = reader(database).query(target_type='entries', action='update')

    def edit(amount):
        try:
            alpha.transactions.update('expense', expected=made, amount=amount)
            return True
        except StorageConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ['20', '30'])) == [False, True]
    assert len(reader(database).query(target_type='entries', action='update')) == len(before) + 1
    assert reader(database).verify()['mismatches'] == 0


def test_mileage_reference_journal_and_import_events_share_atomic_contract(database):
    alpha, _ = seed_core(database)
    account = alpha.accounts.create('Reserve', 'Bank', '0')
    account = alpha.accounts.update(account['id'], expected=account, active=False)
    alpha.accounts.update(account['id'], expected=account, active=True)
    category = alpha.categories.create('Temporary', 'Expense')
    alpha.categories.delete(category['id'])
    made = alpha.mileage.create_trip(**trip())
    changed = alpha.mileage.edit_trip(made.id, expected=made.as_record(), notes='private trip notes')
    alpha.mileage.delete_trip(made.id, expected=changed.as_record())
    journal = alpha.journals.create(entry_id='journal', txn_date='2026-10-05', description='private journal', postings=journal_lines())
    alpha.journals.delete('journal', expected=journal)
    alpha.imports.import_csv([csv()])
    alpha.imports.import_quickbooks([qb(Num='QB-2', Amount='-25')])
    alpha.mileage.import_quickbooks_mileage([source()])
    events = reader(database).query(limit=1000)
    assert {'accounts','categories','mileage_records','entries','postings','import_batches','import_records'} <= {e['target_type'] for e in events}
    assert {e['action'] for e in events if e['target_type'] == 'mileage_records'} == {'create','update','delete'}
    batches = [e for e in events if e['target_type'] == 'import_batches' and e['action'] == 'create']
    assert len(batches) == 3 and all(e['after_summary']['committed_count'] == 1 for e in batches)
    assert all(e['batch_id'] for e in batches)
    before = reader(database).query(limit=1000)
    alpha.imports.import_csv([csv()])
    assert reader(database).query(limit=1000) == before
    assert reader(database).verify()['mismatches'] == 0


def test_http_uses_named_user_and_request_correlation(database):
    from tests.postgres.test_authorization_http import named_client
    with named_client(database) as (client, _, _, identities, _, _, headers):
        response = client.post('/api/v1/entities/alpha/accounts', headers=headers, json={'name': 'Private account', 'type': 'Bank'})
        assert response.status_code == 201, response.text
        event, = reader(database).query(target_type='accounts', action='create')
        assert event['actor_type'] == 'user' and event['actor_id'] == identities[0].id
        assert event['correlation_id'] == response.headers['x-request-id']
        assert event['source'] == 'create_account'
        assert reader(database, 'beta').query() == ()


def test_generated_postings_links_and_merges_preserve_history_and_privacy(database):
    from tests.postgres.secondary_support import asset, schedule
    from tests.postgres.test_document_repository import setup
    from tests.test_application_documents import prepared
    from tabula_rasa.infrastructure.postgres.recurring import PostgresRecurringRepository

    alpha, beta, store = setup(database)
    with audit_context(database, AuditContext('worker', 'trusted-recurring', 'recurring', 'alpha')):
        alpha.recurring.create(schedule_id='monthly', **schedule())
        generated = alpha.recurring.reconcile(as_of='2026-02-01')['generated']
        assert generated
    repo = PostgresRecurringRepository(database, 'alpha', transactions=alpha.transactions, mixed_use=alpha.mixed_use)
    repo.attention('monthly', 'storage')
    assert reader(database).verify()['mismatches'] == 0  # Operational attention is excluded.
    offset = alpha.accounts.create('Depreciation offset', 'Other', classification='Asset')['id']
    made = alpha.assets.create(asset_id='asset', **asset(offset))
    alpha.assets.post_due('asset', '2026-06-30', expected=made)
    payee, target = alpha.payees.create('Private source'), alpha.payees.create('Private target')
    original = alpha.transactions.create(transaction_id='merge', **expense(payee_id=payee['id']))
    alpha.payees.merge(target['id'], [payee['id']], expected=[payee, target])
    assert next(row for row in alpha.transactions.list() if row['id'] == original['id'])['payee_id'] == target['id']
    alpha.receipts.process_recovery({'transaction_id': 'txn', 'receipts': prepared(content=b'private receipt bytes')})
    document, = alpha.receipts.references('txn')
    alpha.receipts.unlink('txn', document.storage_key)
    events = reader(database).query(limit=1000)
    assert {'recurring_occurrences','depreciation_events','document_links'} <= {e['target_type'] for e in events}
    assert {e['action'] for e in events if e['target_type'] == 'document_links'} == {'create','delete'}
    assert store.delete_calls == 0 and beta.transactions.list()[0]['id'] == 'txn'
    assert 'Private source' not in str(events) and 'private receipt bytes' not in str(events)
    assert reader(database).verify()['mismatches'] == 0


def test_lost_commit_response_and_repeated_submission_preserve_one_logical_event(database, monkeypatch):
    alpha, _ = seed_core(database)
    transaction = database.transaction
    lost = False

    @contextmanager
    def lose_once(**kwargs):
        nonlocal lost
        outer = database._operation.get() is None and not lost and not kwargs.get('read_only')
        with transaction(**kwargs) as connection:
            yield connection
        if outer:
            lost = True
            raise DatabaseError('unavailable')

    monkeypatch.setattr(database, 'transaction', lose_once)
    from tabula_rasa.errors import StorageError
    with pytest.raises(StorageError):
        alpha.transactions.create(transaction_id='unknown-outcome', **expense())
    assert alpha.transactions.list()[0]['amount'] == '12.50'
    with pytest.raises(StorageConflictError):
        alpha.transactions.create(transaction_id='unknown-outcome', **expense())
    event, = reader(database).query(target_type='entries', target_id='unknown-outcome')
    assert event['action'] == 'create'
    assert reader(database).verify()['mismatches'] == 0


def test_incomplete_imports_report_committed_chunks_without_phantom_failure_events(database):
    from tabula_rasa.infrastructure.postgres.imports import PostgresTransactionImportRepository
    alpha, _ = seed_core(database)
    repo = PostgresTransactionImportRepository(database, 'alpha')
    first, second = [csv()], [csv(Account='missing', Reference='chunk-2')]
    first_id, second_id = repo.identity('csv', first), repo.identity('csv', second)
    alpha.imports.import_csv(first)
    before = reader(database).query(limit=1000)
    with pytest.raises(StorageValidationError):
        alpha.imports.import_csv(second)
    assert reader(database).query(limit=1000) == before
    status = reader(database).import_status([first_id, second_id])
    assert status == {'status': 'partial', 'complete_chunks': (first_id,),
                      'unconfirmed_chunks': (second_id,), 'committed_count': 1}
    assert reader(database, 'beta').import_status([first_id])['status'] == 'unconfirmed'
    assert reader(database).query(batch_id=first_id, target_type='entries')
    assert reader(database).query(batch_id=second_id) == ()
    assert reader(database).verify()['mismatches'] == 0
    # Removing an imported target must not erase the original accepted count.
    alpha.transactions.delete(alpha.transactions.list()[0]['id'])
    assert reader(database).import_status([first_id])['committed_count'] == 1
