"""Unused schedule deletion retains original records and serializes with generation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import text

from tabula_rasa.errors import StorageConflictError, StorageNotFoundError
from tabula_rasa.infrastructure.postgres.accounting import PostgresTransactionRepository
from tests.postgres.core_support import seed_core
from tests.postgres.secondary_support import schedule, mixed, document
from tests.postgres.test_http_baselines import HEADERS, PREFIX, commercial as commercial


@pytest.mark.parametrize('is_mixed', [False, True])
@pytest.mark.parametrize('status', ['active', 'paused', 'ended'])
def test_delete_retains_original_values_receipts_and_editability_in_one_business(database, is_mixed, status):
    alpha, beta = seed_core(database)
    with database.transaction() as connection:
        receipt = document(connection, 'alpha')
    if is_mixed:
        source = alpha.mixed_use.create(source_id='original', linked_transaction_id='projection', **mixed(), receipt_references=[receipt])
        original = alpha.recurring.from_mixed_use('original', expected_source=source, schedule_id='same', name='Converted')
    else:
        entry = alpha.transactions.create(transaction_id='original', **schedule(), receipt_references=[receipt])
        original = alpha.recurring.from_transaction(entry['id'], expected_transaction=entry, schedule_id='same', name='Converted')
    beta.recurring.create_expense(schedule_id='same', **schedule())
    if status != 'active':
        getattr(alpha.recurring, 'pause' if status == 'paused' else 'end')('same')
    loaded = alpha.recurring.get('same')
    assert alpha.recurring.detail('same')['can_delete']
    alpha.recurring.delete('same', expected_schedule=loaded)
    assert not alpha.recurring.list()
    transaction = next(row for row in alpha.transactions.list(fresh=True) if row['id'] == original['transaction']['id'])
    assert not transaction['recurring_schedule_id']
    for field in ('id', 'amount', 'txn_date', 'description', 'created_at', 'receipt_references'):
        assert transaction[field] == original['transaction'][field]
    if is_mixed:
        source = alpha.mixed_use.get(original['source']['id'])
        assert source['recurring_schedule_id'] == source['recurring_occurrence_type'] == ''
        assert source['linked_transaction_id'] == transaction['id']
        assert source['baseline'] != original['source']['baseline']
        alpha.mixed_use.update(source['id'], expected_source=source, total_expense='110')
    else:
        assert transaction['baseline'] != original['transaction']['baseline']
        alpha.transactions.update(transaction['id'], expected=transaction, amount='20')
    assert len(beta.recurring.detail('same')['transactions']) == 1
    assert not alpha.recurring.reconcile(as_of='2026-03-01')['generated']
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT count(*) FROM recurring_occurrences WHERE entity_id='alpha'")) == 0


@pytest.mark.parametrize('is_mixed', [False, True])
def test_generation_since_load_blocks_delete_but_end_preserves_history(database, is_mixed):
    alpha, _ = seed_core(database)
    create = alpha.recurring.create_mixed_expense if is_mixed else alpha.recurring.create_expense
    create(schedule_id='used', **{**schedule(), **(mixed() if is_mixed else {})})
    loaded = alpha.recurring.get('used')
    alpha.recurring.reconcile(as_of='2026-02-01')
    assert not alpha.recurring.detail('used')['can_delete']
    with pytest.raises(StorageConflictError, match='sole original occurrence'):
        alpha.recurring.delete('used', expected_schedule=loaded)
    alpha.recurring.end('used', expected_schedule=loaded)
    detail = alpha.recurring.detail('used')
    assert detail['schedule']['status'] == 'ended' and len(detail['transactions']) == 2
    assert not alpha.recurring.reconcile(as_of='2026-04-01')['generated']


def test_no_original_and_stale_schedule_are_rejected(database):
    alpha, _ = seed_core(database)
    alpha.recurring.create(schedule_id='blank', **schedule())
    assert not alpha.recurring.detail('blank')['can_delete']
    with pytest.raises(StorageConflictError, match='sole original occurrence'):
        alpha.recurring.delete('blank')
    alpha.recurring.create_expense(schedule_id='stale', **schedule())
    loaded = alpha.recurring.get('stale')
    alpha.recurring.pause('stale', expected_schedule=loaded)
    with pytest.raises(StorageConflictError):
        alpha.recurring.delete('stale', expected_schedule=loaded)


def test_generation_wins_between_discovery_and_delete_locks(database, monkeypatch):
    alpha, _ = seed_core(database)
    alpha.recurring.create_expense(schedule_id='racing', **schedule())
    loaded = alpha.recurring.get('racing')
    discovered, release = Event(), Event()
    original = PostgresTransactionRepository.references
    def wait_before_lock(self, connection, **options):
        if options.get('root_keys') and not discovered.is_set():
            discovered.set()
            assert release.wait(10)
        return original(self, connection, **options)
    monkeypatch.setattr(PostgresTransactionRepository, 'references', wait_before_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(alpha.recurring.delete, 'racing', expected_schedule=loaded)
        try:
            assert discovered.wait(10)
            alpha.recurring.reconcile(as_of='2026-02-01')
        finally:
            release.set()
        with pytest.raises(StorageConflictError, match='sole original occurrence'):
            pending.result(timeout=10)
    assert len(alpha.recurring.detail('racing')['transactions']) == 2


def test_delete_wins_and_a_prepared_generator_cannot_recreate_schedule(database):
    alpha, _ = seed_core(database)
    alpha.recurring.create_expense(schedule_id='gone', **schedule())
    loaded = alpha.recurring.get('gone')
    alpha.recurring.delete('gone', expected_schedule=loaded)
    with pytest.raises(StorageNotFoundError):
        alpha.transactions.create_occurrence(transaction_id='late', txn_date='2026-02-01', kind='Expense', amount='12.50',
            account_id='cash', category_id='office', recurring_schedule_id='gone', recurring_scheduled_date='2026-02-01',
            recurring_source_kind='transaction', recurring_occurrence_type='generated', expected_schedule=loaded)
    assert len(alpha.transactions.list()) == 1


def test_delete_http_requires_authority_baseline_and_retains_entry(commercial):
    alpha, beta, (client, _), _ = commercial
    created = alpha.recurring.create_expense(schedule_id='http', **schedule())
    path = PREFIX + 'recurring/http'
    detail = client.get(path).json()
    assert detail['can_delete']
    assert client.request('DELETE', path, headers=HEADERS, json={}).status_code == 422
    assert client.request('DELETE', path, json={'baseline': detail['schedule']['baseline']}).status_code == 403
    response = client.request('DELETE', path, headers=HEADERS, json={'baseline': detail['schedule']['baseline']})
    assert response.status_code == 200 and response.json()['status'] == 'fully_applied'
    assert client.get(path).status_code == 404
    assert not next(row for row in alpha.transactions.list(fresh=True) if row['id'] == created['transaction']['id'])['recurring_schedule_id']
    assert not beta.recurring.list()


def test_http_revalidates_generated_occurrences_and_foreign_scope(commercial):
    alpha, beta, (client, _), _ = commercial
    alpha.recurring.create_expense(schedule_id='used-http', **schedule())
    detail = client.get(PREFIX + 'recurring/used-http').json()
    assert detail['can_delete']
    alpha.recurring.reconcile(as_of='2026-02-01')
    response = client.request('DELETE', PREFIX + 'recurring/used-http', headers=HEADERS, json={'baseline': detail['schedule']['baseline']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'recurring_in_use'
    beta.recurring.create_expense(schedule_id='foreign', **schedule())
    response = client.request('DELETE', PREFIX + 'recurring/foreign', headers=HEADERS, json={'baseline': detail['schedule']['baseline']})
    assert response.status_code == 404
    assert len(beta.recurring.detail('foreign')['transactions']) == 1
