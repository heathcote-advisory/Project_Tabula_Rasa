"""Mileage and provider-neutral receipts through HTTP and native PostgreSQL."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from tabula_rasa.application.documents import DocumentError
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tests.postgres.test_http_baselines import commercial as commercial, HEADERS, PREFIX, snapshot
from tests.test_application_documents import FakeDocumentStore

TRIP = dict(trip_date='2026-10-08', miles='12.36', vehicle='Van', start_location='Office',
            destination='Site', business_purpose='Inspection', notes='Reviewed distance')


@pytest.fixture
def document_store():
    return FakeDocumentStore()


@pytest.fixture
def document_clients(database, commercial, document_store):
    _, _, clients, _ = commercial
    for client in clients:
        client.app.state.runtime.service_factory = lambda context: create_postgres_services(
            database, context.entity_id, document_store=document_store, document_store_kind='fake')
    return clients


def upload(client, *, identity='a' * 32, filename='receipt.pdf', content=b'%PDF synthetic receipt'):
    return client.put(PREFIX + 'documents/' + identity + '/content', params={'filename': filename},
                      headers={**HEADERS, 'Content-Type': 'application/octet-stream'}, content=content)


def test_overlapping_upload_is_rejected_then_matching_retry_reuses_bytes(document_clients, document_store):
    client, _ = document_clients
    entered, release = Event(), Event()
    original = document_store.upload

    def delayed(profile, prepared):
        if prepared.document_id == 'a' * 32:
            entered.set()
            assert release.wait(10)
        return original(profile, prepared)

    with patch.object(document_store, 'upload', side_effect=delayed), ThreadPoolExecutor() as pool:
        first = pool.submit(upload, client)
        try:
            assert entered.wait(10)
            overlapping = upload(client)
            assert overlapping.status_code == 409
            assert overlapping.json()['error']['code'] == 'concurrent_write'
            # Distinct identities and business entities do not share a guard.
            assert upload(client, identity='b' * 32).status_code == 200
            with client.app.state.runtime.document_upload_operation(SimpleNamespace(entity_id='beta'), 'a' * 32):
                pass
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200
    assert upload(client).status_code == 200
    assert document_store.upload_calls == 2  # One upload for each distinct ID.


def test_mileage_create_round_trip_detail_filter_and_entity_isolation(database, commercial):
    _, beta, (client, _), _ = commercial
    before = snapshot(database)
    invalid = client.post(PREFIX + 'mileage', headers=HEADERS, json={**TRIP, 'round_trip': True, 'return_miles': '0', 'new_tag_name': 'Must not persist'})
    assert invalid.status_code == 422 and invalid.json()['error']['code'] == 'validation'
    assert snapshot(database) == before
    created = client.post(PREFIX + 'mileage', headers=HEADERS, json={**TRIP, 'round_trip': True, 'return_miles': '14.2', 'new_tag_name': 'Inspection'})
    assert created.status_code == 201, created.text
    outbound, returned = created.json()['items']
    assert outbound['id'] != returned['id'] and outbound['baseline'] != returned['baseline']
    assert outbound['miles'] == '12.4' and returned['miles'] == '14.2'
    assert (returned['start_location'], returned['destination']) == ('Site', 'Office')
    assert outbound['tag_ids'] == returned['tag_ids'] and len(outbound['tag_ids']) == 1
    detail = client.get(PREFIX + 'mileage/' + outbound['id'])
    assert detail.json() == outbound
    rows = client.get(PREFIX + 'mileage?start=2026-10-08&end=2026-10-08').json()['items']
    assert [row['id'] for row in rows] == [returned['id'], outbound['id']]
    assert client.get(PREFIX + 'mileage?start=invalid').status_code == 422
    assert client.get(PREFIX + 'mileage/missing').status_code == 404
    assert client.get(PREFIX.replace('alpha', 'beta') + 'mileage/' + outbound['id']).status_code == 403
    assert beta.mileage.list_trips() == ()
    assert snapshot(database)['entries'] == before['entries']  # Mileage never posts accounting entries.


def test_managed_vehicle_can_be_cleared_without_legacy_free_text(commercial):
    _, _, (client, _), _ = commercial
    vehicle = client.post(PREFIX + 'vehicles', headers=HEADERS, json={'name': 'Van'}).json()
    fields = {key: value for key, value in TRIP.items() if key != 'vehicle'}
    created = client.post(PREFIX + 'mileage', headers=HEADERS,
                          json={**fields, 'vehicle_id': vehicle['id']}).json()['items'][0]
    route = PREFIX + 'mileage/' + created['id']
    retained = client.patch(route, headers=HEADERS,
                            json={'baseline': created['baseline'], 'changes': {**fields, 'notes': 'Keep selection'}})
    assert retained.status_code == 200 and retained.json()['vehicle_id'] == vehicle['id']
    cleared = client.patch(route, headers=HEADERS,
                           json={'baseline': retained.json()['baseline'], 'changes': {**fields, 'vehicle_id': None}})
    assert cleared.status_code == 200
    assert cleared.json()['vehicle_id'] is None and cleared.json()['vehicle'] == ''
    assert cleared.json()['miles'] == created['miles']


def test_mileage_create_rejects_other_entity_tag_and_keeps_inactive_history(database, commercial):
    alpha, beta, (client, _), ids = commercial
    foreign = beta.tags.create('Other business')
    before = snapshot(database)
    assert client.post(PREFIX + 'mileage', headers=HEADERS, json={**TRIP, 'tag_ids': [foreign['id']]}).status_code == 422
    assert snapshot(database) == before
    alpha.tags.update(ids['tags'], active=False)
    assert client.post(PREFIX + 'mileage', headers=HEADERS, json={**TRIP, 'tag_ids': [ids['tags']]}).status_code == 422
    loaded = client.get(PREFIX + 'mileage/' + ids['mileage']).json()
    changed = client.patch(PREFIX + 'mileage/' + ids['mileage'], headers=HEADERS,
        json={'baseline': loaded['baseline'], 'changes': {**TRIP, 'tag_ids': loaded['tag_ids']}})
    assert changed.status_code == 200 and changed.json()['tag_ids'] == loaded['tag_ids']


def test_documents_upload_link_download_unlink_relink_without_provider_fields(database, commercial, document_clients, document_store):
    alpha, beta, _, ids = commercial
    client, _ = document_clients
    path = PREFIX + 'transactions/' + ids['transactions'] + '/receipts'
    assert client.get(path).json()['items'] == []
    uploaded = upload(client)
    assert uploaded.status_code == 200, uploaded.text
    doc = uploaded.json()
    assert doc['id'] == 'a' * 32 and doc['byte_size'] == len(b'%PDF synthetic receipt')
    assert not {'storage_key', 'open_url', 'profile_key', 'store_kind'} & doc.keys()
    assert client.get(PREFIX + 'documents').json()['items'] == [doc]
    assert upload(client).json() == doc
    assert upload(client, content=b'%PDF conflicting bytes').status_code == 422
    assert upload(client, filename='different.pdf').status_code == 422
    assert document_store.upload_calls == 1
    before_baseline = alpha.transactions.list()[0]['baseline']
    assert client.post(path, headers=HEADERS, json={'document_id': doc['id']}).status_code == 200
    assert client.get(path).json()['items'] == [doc]
    assert upload(client, content=b'%PDF conflicting linked bytes').status_code == 422
    assert client.get(PREFIX + 'documents').json()['items'] == []
    response = client.get(PREFIX + 'documents/' + doc['id'] + '/content')
    assert response.content == b'%PDF synthetic receipt' and response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    # Existing transaction editors become stale after a receipt relationship changes.
    assert alpha.transactions.list()[0]['baseline'] != before_baseline
    assert client.delete(path + '/' + doc['id'], headers=HEADERS).status_code == 200
    assert client.get(path).json()['items'] == []
    assert client.get(PREFIX + 'documents').json()['items'] == [doc]
    assert client.post(path, headers=HEADERS, json={'document_id': doc['id']}).status_code == 200
    assert document_store.delete_calls == 0 and beta.receipts is None
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM documents')) == 1
        assert connection.scalar(text('SELECT count(*) FROM document_links')) == 1


def test_documents_entity_scope_permissions_validation_and_safe_errors(database, commercial, document_clients, document_store, caplog):
    client, _ = document_clients
    _, _, _, ids = commercial
    path = PREFIX + 'transactions/' + ids['transactions'] + '/receipts'
    before = snapshot(database)
    assert upload(client, filename='script.html').status_code == 422
    assert upload(client, content=b'').status_code == 422
    assert upload(client, identity='invalid').status_code == 422
    assert document_store.upload_calls == 0 and snapshot(database) == before
    assert upload(client).status_code == 200
    assert client.get(PREFIX.replace('alpha', 'beta') + 'documents').status_code == 403
    assert client.post(path, headers={}, json={'document_id': 'a' * 32}).status_code == 403
    runtime = client.app.state.runtime
    original = runtime.resolve_context
    runtime.resolve_context = lambda request: replace(original(request), entity_id='beta')
    beta_path = path.replace('/alpha/', '/beta/')
    assert client.post(beta_path, headers=HEADERS, json={'document_id': 'a' * 32}).status_code == 404
    assert client.get(PREFIX.replace('alpha', 'beta') + 'documents/' + 'a' * 32 + '/content').status_code == 404
    runtime.resolve_context = lambda request: replace(original(request), capabilities=frozenset({'bookkeeping:read'}))
    assert upload(client, identity='b' * 32).status_code == 403
    assert client.post(path, headers=HEADERS, json={'document_id': 'a' * 32}).status_code == 403
    runtime.resolve_context = original
    with patch.object(document_store, 'upload', side_effect=DocumentError('PRIVATE provider token')), caplog.at_level('INFO', logger='tabula_rasa.http'):
        failure = upload(client, identity='b' * 32)
    assert failure.status_code == 503 and failure.json()['error']['code'] == 'unavailable'
    assert 'PRIVATE' not in failure.text + caplog.text
    assert snapshot(database) == before


def test_failed_link_retains_bytes_failed_unlink_retains_relationship(database, commercial, document_clients, document_store):
    client, _ = document_clients
    _, _, _, ids = commercial
    doc = upload(client).json()
    missing = PREFIX + 'transactions/missing/receipts'
    assert client.post(missing, headers=HEADERS, json={'document_id': doc['id']}).status_code == 404
    assert client.get(PREFIX + 'documents').json()['items'] == [doc]
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM documents')) == 0  # Failed link is atomic.
    path = PREFIX + 'transactions/' + ids['transactions'] + '/receipts'
    assert client.post(path, headers=HEADERS, json={'document_id': doc['id']}).status_code == 200
    assert document_store.upload_calls == 1
    before = snapshot(database)
    from tabula_rasa.infrastructure.postgres.documents import PostgresReceiptRepository
    with patch.object(PostgresReceiptRepository, 'unlink', side_effect=DocumentError('PRIVATE remove failure')):
        response = client.delete(path + '/' + doc['id'], headers=HEADERS)
    assert response.status_code == 503 and snapshot(database) == before
    assert client.get(path).json()['items'] == [doc] and document_store.delete_calls == 0
