"""Two independently loaded commercial clients, through the actual HTTP boundary."""

from contextlib import ExitStack
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import inspect, text

from tabula_rasa.http.app import create_app
from tabula_rasa.http.composition import PostgresRuntime
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.context import RequestContext
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.runtime import Database
from tests.postgres.core_support import expense, journal_lines, seed_core

HEADERS = {'Origin': 'http://testserver', 'X-CSRF-Token': 'csrf'}
PREFIX = '/api/v1/entities/alpha/'
CHANGES = {
    'accounts': dict(name='Changed account', type='Cash', classification='Asset',
                     opening_balance='8.00', active=True, parent_id=None),
    'categories': dict(name='Changed category', kind='Expense', parent_id=None),
    'payees': dict(name='Changed payee', active=True),
    'tags': dict(name='Changed tag', active=True),
    'transactions': expense(amount='21.50', description='Changed transaction'),
    'journals': dict(txn_date='2026-10-06', description='Changed journal', postings=journal_lines()),
    'mileage': dict(trip_date='2026-10-06', miles='16.2', vehicle='Car', start_location='Office',
                    destination='Client', business_purpose='Changed purpose', notes='Changed trip'),
}


def snapshot(database):
    # Includes all persisted facts, versions, postings and associations, rather
    # than checking only the top-level HTTP projection after a failed write.
    tables = sorted(inspect(database.engine).get_table_names())
    with database.transaction(read_only=True) as connection:
        return {table: sorted(map(repr, connection.execute(text(f'SELECT * FROM "{table}"')).all()))
                for table in tables}


@pytest.fixture
def commercial(database):
    alpha, beta = seed_core(database)
    payee = alpha.payees.create('Vendor')
    tag = alpha.tags.create('Job')
    transaction = alpha.transactions.create(transaction_id='expense', **expense(payee_id=payee['id'], tag_ids=[tag['id']]))
    journal = alpha.journals.create(entry_id='journal', txn_date='2026-10-05', description='Loaded', postings=journal_lines())
    trip = alpha.mileage.create_trip(trip_date='2026-10-05', miles='12.3', vehicle='Car',
        start_location='Office', destination='Client', business_purpose='Meeting', tag_ids=[tag['id']])
    ids = dict(accounts='cash', categories='office', payees=payee['id'], tags=tag['id'],
               transactions=transaction['id'], journals=journal['id'], mileage=trip.id)
    other = Database(DatabaseSettings(database.engine.url))
    context = RequestContext('actor', 'org', 'alpha',
        frozenset({'bookkeeping:read', 'bookkeeping:write', 'bookkeeping:import', 'asset:depreciate'}), 'scope', 'csrf')
    with ExitStack() as stack:
        clients = []
        for db in (database, other):
            runtime = PostgresRuntime(db, lambda request: context,
                service_factory=lambda context, db=db: create_postgres_services(db, context.entity_id))
            # Fixture owns pools, not these test applications.
            runtime.close = lambda: None
            clients.append(stack.enter_context(TestClient(create_app(settings=HttpSettings('test', 'http://testserver'),
                runtime_factory=lambda settings, runtime=runtime: runtime))))
        try:
            yield alpha, beta, clients, ids
        finally:
            other.dispose()


def loaded(client, resource, identity):
    response = client.get(PREFIX + resource)
    assert response.status_code == 200, response.text
    return next(row for row in response.json()['items'] if row['id'] == identity)


@pytest.mark.parametrize('resource', CHANGES)
def test_two_loaded_clients_stale_update_delete_missing_and_atomicity(database, commercial, resource):
    alpha, beta, (a, b), ids = commercial
    identity = ids[resource]
    first = loaded(a, resource, identity)
    second = deepcopy(loaded(b, resource, identity))
    assert first == second and second['baseline'].startswith('v1.')
    path = PREFIX + resource + '/' + identity
    changes = CHANGES[resource]
    committed = a.patch(path, headers=HEADERS, json={'baseline': first['baseline'], 'changes': changes})
    assert committed.status_code == 200, committed.text
    assert committed.json()['baseline'] != second['baseline']
    before = snapshot(database)
    later_changes = {**changes, **({'new_payee_name': 'Must not create', 'new_tag_name': 'Must not create'}
                                  if resource == 'transactions' else {'new_tag_name': 'Must not create'}
                                  if resource == 'mileage' else {})}
    stale = b.patch(path, headers=HEADERS, json={'baseline': second['baseline'], 'changes': later_changes})
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'stale'
    delete_path = path + '/deactivate' if resource == 'accounts' else path
    method = 'POST' if resource == 'accounts' else 'DELETE'
    stale_delete = b.request(method, delete_path, headers=HEADERS, json={'baseline': second['baseline']})
    assert stale_delete.status_code == 409 and stale_delete.json()['error']['code'] == 'stale'
    for token in (None, ''):
        body = {'changes': later_changes} if token is None else {'baseline': token, 'changes': later_changes}
        missing = b.patch(path, headers=HEADERS, json=body)
        assert missing.status_code == 422 and missing.json()['error']['code'] == 'baseline_required'
    missing_delete = b.request(method, delete_path, headers=HEADERS, json={})
    assert missing_delete.status_code == 422 and missing_delete.json()['error']['code'] == 'baseline_required'
    invalid = b.patch(path, headers=HEADERS, json={'baseline': 'private-invalid-token', 'changes': changes})
    assert invalid.status_code == 422 and invalid.json()['error']['code'] == 'baseline_invalid'
    assert 'private-invalid-token' not in invalid.text
    assert snapshot(database) == before
    assert loaded(b, resource, identity) == committed.json()
    assert b.patch(path.replace('/alpha/', '/beta/'), headers=HEADERS,
                   json={'baseline': second['baseline'], 'changes': changes}).status_code == 403
    # Explicit reload supplies the new baseline and permits the supported command.
    fresh = b.request(method, delete_path, headers=HEADERS, json={'baseline': committed.json()['baseline']})
    # Referenced categories/payees/tags retain the existing fresh reference guard.
    assert fresh.status_code == (422 if resource in {'categories', 'payees', 'tags'} else 200), fresh.text
    assert beta.transactions.list() == [] and beta.mileage.list_trips() == ()


@pytest.mark.parametrize('resource', ['payees', 'tags'])
@pytest.mark.parametrize('changed_owner', ['source', 'target'])
def test_merge_requires_complete_original_baselines_and_never_partially_rewrites_owners(database, commercial, resource, changed_owner):
    alpha, _, (a, b), ids = commercial
    service = getattr(alpha, resource)
    target = service.create('Target')
    source = service.create('Source')
    original = {row['id']: row['baseline'] for row in b.get(PREFIX + resource).json()['items']}
    path = PREFIX + resource + '/' + target['id'] + '/merge'
    changed = source if changed_owner == 'source' else target
    a.patch(PREFIX + resource + '/' + changed['id'], headers=HEADERS,
            json={'baseline': original[changed['id']], 'changes': {'name': 'Changed reference', 'active': True}}).raise_for_status()
    before = snapshot(database)
    roots = [ids[resource], source['id']]
    tokens = {identity: original[identity] for identity in [target['id'], *roots]}
    stale = b.post(path, headers=HEADERS, json={'source_ids': roots, 'baselines': tokens})
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'stale'
    incomplete = b.post(path, headers=HEADERS, json={'source_ids': roots, 'baselines': {target['id']: tokens[target['id']]}})
    assert incomplete.status_code == 422 and incomplete.json()['error']['code'] == 'baseline_required'
    extra = b.post(path, headers=HEADERS, json={'source_ids': roots, 'baselines': {**tokens, 'extra': original[target['id']]}})
    assert extra.status_code == 422 and extra.json()['error']['code'] == 'baseline_invalid'
    assert snapshot(database) == before
    current = {row['id']: row['baseline'] for row in b.get(PREFIX + resource).json()['items']}
    merged = b.post(path, headers=HEADERS, json={'source_ids': roots,
        'baselines': {identity: current[identity] for identity in [target['id'], *roots]}})
    assert merged.status_code == 200 and merged.json()['count'] == '2'
    assert {row['id'] for row in service.list()} == {target['id']}


def test_internal_omission_remains_compatible_but_http_has_no_bypass(commercial):
    alpha, _, (_, b), ids = commercial
    alpha.accounts.update('cash', name='Internal account')
    alpha.categories.update('office', name='Internal category')
    alpha.payees.rename(ids['payees'], 'Internal payee')
    alpha.tags.rename(ids['tags'], 'Internal tag')
    alpha.transactions.update(ids['transactions'], amount='25.00')
    alpha.mileage.edit_trip(ids['mileage'], notes='Internal note')
    for resource, changes in CHANGES.items():
        response = b.patch(PREFIX + resource + '/' + ids[resource], headers=HEADERS,
                           json={'changes': changes, 'legacy': True})
        assert response.status_code == 422


def test_mileage_domain_validation_has_safe_typed_error(commercial):
    _, _, (_, b), ids = commercial
    row = loaded(b, 'mileage', ids['mileage'])
    response = b.patch(PREFIX + 'mileage/' + row['id'], headers=HEADERS,
        json={'baseline': row['baseline'], 'changes': {**CHANGES['mileage'], 'business_purpose': ''}})
    assert response.status_code == 422 and response.json()['error']['code'] == 'validation'


def test_transport_defaults_do_not_clear_unsupplied_parent_or_associations(commercial):
    alpha, _, (_, b), ids = commercial
    alpha.accounts.update('cash', parent_id='bank')
    parent = alpha.categories.create('Parent', 'Expense')
    alpha.categories.update('office', parent_id=parent['id'])
    alpha.transactions.update(ids['transactions'], description='Retain memo', reference='Retain reference')
    alpha.mileage.edit_trip(ids['mileage'], notes='Retain notes')
    omitted = {'accounts': {'parent_id'}, 'categories': {'parent_id'}, 'transactions':
               {'payee_id', 'tag_ids', 'description', 'reference'}, 'mileage': {'tag_ids', 'notes'}}
    for resource, fields in omitted.items():
        row = loaded(b, resource, ids[resource])
        changes = {key: value for key, value in CHANGES[resource].items() if key not in fields}
        response = b.patch(PREFIX + resource + '/' + row['id'], headers=HEADERS,
                           json={'baseline': row['baseline'], 'changes': changes})
        assert response.status_code == 200, response.text
        assert {field: response.json()[field] for field in fields} == {field: row[field] for field in fields}
