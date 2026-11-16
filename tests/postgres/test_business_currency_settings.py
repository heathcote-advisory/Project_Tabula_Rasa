"""Explicit label edits preserve canonical numbers, admission and tenant boundaries."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from zipfile import ZipFile

import pytest
from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.application.workspaces import WorkspaceService
from tabula_rasa.bookkeeping_export.format import validate_artifact
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services, create_postgres_export_service
from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository
from tabula_rasa.errors import StorageValidationError
from tests.postgres.test_workspaces import context, create
from tests.postgres.test_identity_http import client_for
from tests.postgres.test_entity_export import representative
from tests.postgres.test_customer_lifecycle import lifecycle as lifecycle
from tests.postgres.test_beta_admission import cohort as cohort


def setup(database):
    sessions, raw, owner = context(database)
    repository = PostgresWorkspaceRepository(database)
    service = WorkspaceService(repository)
    first, second = create(service, owner), create(service, owner, 'Second')
    sessions.select(raw, first['entity_id'])
    return sessions, raw, owner, service, first, second


@pytest.mark.parametrize('role', ['owner', 'admin', 'editor', 'accountant', 'read_only'])
def test_currency_http_roles_confirmation_csrf_stale_and_isolation(database, role):
    sessions, raw, owner, service, first, second = setup(database)
    _, member_raw, member = context(database, 'member')
    sessions.identity.set_membership(first['organization_id'], member.user_id, role)
    client, auth, _ = client_for(database)
    client.cookies.set(auth.session_cookie, member_raw)
    route = '/api/v1/businesses/' + first['entity_id'] + '/settings'
    with client:
        session = client.get('/api/v1/session').json()
        headers = {'Origin': auth.settings.public_origin, 'X-CSRF-Token': session['csrf_token']}
        body = {'currency_code': 'CAD', 'version': 1, 'confirm_label_change': True}
        assert client.patch(route + '/currency', json=body).status_code == 403
        allowed = role in {'owner', 'admin'}
        assert client.get(route).status_code == (200 if allowed else 403)
        result = client.patch(route + '/currency', json=body, headers=headers)
        assert result.status_code == (200 if allowed else 403)
        foreign = '/api/v1/businesses/' + second['entity_id'] + '/settings'
        assert client.get(foreign).status_code == 403
        assert client.patch(foreign + '/currency', json=body, headers=headers).status_code == 403
        if allowed:
            assert result.json() == {'currency_code': 'CAD', 'version': 2}
            # Both stale replay and stale reverse edits fail, including ABA.
            assert client.patch(route + '/currency', json=body, headers=headers).status_code == 409
            for override in ({'currency_code': 'EUR'}, {'confirm_label_change': False}, {'country': 'CA'}):
                assert client.patch(route + '/currency', json={**body, **override}, headers=headers).status_code == 422
            sessions.identity.set_membership(first['organization_id'], member.user_id, 'read_only')
            assert client.patch(route + '/currency', json={**body, 'version': 2, 'currency_code': 'USD'}, headers=headers).status_code == 403
    assert service.business_settings(owner, second['entity_id']) == {'currency_code': 'USD', 'version': 1}
    assert sessions.validate(raw).selected.currency_code == ('CAD' if allowed else 'USD')


def test_round_trip_current_exports_imports_and_all_financial_domains_unchanged(database):
    # Seed every portable domain, including receipts, mixed use, mileage,
    # recurrence and financial postings; compare entire rows, not just totals.
    alpha, _ = representative(database)
    sessions, raw, owner = context(database)
    org = sessions.identity.create_organization(owner.user_id, 'Private')
    sessions.identity.bind_entity(org, 'alpha')
    other_org = sessions.identity.create_organization(owner.user_id, 'Other')
    sessions.identity.bind_entity(other_org, 'beta')
    sessions.select(raw, 'alpha')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    before = create_postgres_export_service(database, 'alpha').export()['domains']
    other = create_postgres_export_service(database, 'beta').export()['domains']
    old_session = sessions.validate(raw)
    client, auth, factory = client_for(database)
    factory.side_effect = lambda ctx: create_postgres_services(database, ctx.entity_id)
    client.cookies.set(auth.session_cookie, raw)
    with client:
        initial = client.get('/api/v1/session').json()
        headers = {'Origin': auth.settings.public_origin, 'X-CSRF-Token': initial['csrf_token']}
        for version, currency in [(1, 'CAD'), (2, 'USD')]:
            changed = service.change_currency(owner, 'alpha', currency, version, True)
            assert changed == {'currency_code': currency, 'version': version + 1}
            current = client.get('/api/v1/session').json()
            assert current['entity']['currency_code'] == currency
            assert current['cache_scope'] != initial['cache_scope']
            assert old_session.selected.currency_code == 'USD'
            choices = {item['id']: item['currency_code'] for item in client.get('/api/v1/businesses').json()['items']}
            assert choices == {'alpha': currency, 'beta': 'USD'}
            exported = create_postgres_export_service(database, 'alpha').export()
            validate_artifact(exported, entity_id='alpha')
            after = exported['domains']
            assert after['business_entities'][0]['currency_code'] == currency
            assert after['business_entities'][0]['version'] == version + 1
            assert {k: v for k, v in after.items() if k != 'business_entities'} == {k: v for k, v in before.items() if k != 'business_entities'}
            assert create_postgres_export_service(database, 'beta').export()['domains'] == other
            for name in ('transactions', 'journal-postings'):
                response = client.get('/api/v1/entities/alpha/exports/' + name + '.csv')
                assert response.status_code == 200
                assert response.headers['x-book-currency'] == currency
            handoff = client.post('/api/v1/entities/alpha/exports/handoff', json={'scope': 'metadata_only'}, headers=headers)
            assert handoff.status_code == 200
            with ZipFile(BytesIO(handoff.content)) as archive:
                artifact = json.loads(archive.read('bookkeeping.json'))
                assert artifact['domains']['business_entities'][0]['currency_code'] == currency
                assert ('Book currency: ' + currency).encode() in archive.read('README.txt')
            # Existing service graphs read fresh metadata, including import validation.
            assert alpha.ledger.get_info().currency_code == currency
            for scope, expected_currency in [('beta', 'USD'), ('alpha', currency)]:
                selected = client.post('/api/v1/session/entity', json={'entity_id': scope}, headers=headers)
                assert selected.status_code == 200
                assert selected.json()['entity']['currency_code'] == expected_currency
            from tests.postgres.test_import_repository import csv
            with pytest.raises(StorageValidationError, match='Source currency'):
                alpha.imports.import_csv([{**csv(), 'Currency': 'USD' if currency == 'CAD' else 'CAD'}])
        assert service.business_settings(owner, 'alpha')['version'] == 3
        with pytest.raises(IdentityError, match='stale'):
            service.change_currency(owner, 'alpha', 'CAD', 1, True)
    with database.transaction(read_only=True) as c:
        events = c.execute(text("SELECT * FROM audit_events WHERE source='business.currency' ORDER BY sequence")).mappings().all()
        assert len(events) == 2
        assert [(e['before_summary']['currency_code'], e['after_summary']['currency_code']) for e in events] == [('USD', 'CAD'), ('CAD', 'USD')]
        assert all(e['actor_id'] == owner.user_id and e['entity_id'] == 'alpha' and e['occurred_at'] for e in events)


def test_concurrent_currency_edits_and_noop_are_version_guarded(database):
    _, _, owner, service, first, _ = setup(database)
    scope = first['entity_id']
    assert service.change_currency(owner, scope, 'USD', 1, True)['version'] == 1
    def edit(_):
        try:
            return service.change_currency(owner, scope, 'CAD', 1, True)['currency_code']
        except IdentityError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, range(2))) == ['CAD', 'stale']
    assert service.business_settings(owner, scope) == {'currency_code': 'CAD', 'version': 2}


def test_currency_change_waits_for_closure_before_identity_locks(database, lifecycle):
    _, _, _, _, _, _, _, _, _, session, closures = lifecycle
    request = closures.request(session, 'currency-closure-request', 'business_closure', 'alpha')
    service = WorkspaceService(PostgresWorkspaceRepository(database))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.transaction() as c:
            c.execute(text("SELECT pg_advisory_xact_lock(346,hashtext('alpha'))"))
            pending = pool.submit(service.change_currency, session, 'alpha', 'CAD', 1, True)
            with pytest.raises(TimeoutError):
                pending.result(timeout=.1)
            closures.change(session, request['id'], 'confirm', 1, export_saved=True)
        with pytest.raises(IdentityError, match='forbidden'):
            pending.result(timeout=5)


def test_currency_edit_does_not_reinterpret_canadian_admission(database, cohort):
    _, _, _, person, _, _, _, command, configure, service, request, _ = cohort
    _, owner = person('cad-currency-owner')
    configure()
    admission, key = request(owner, 'Canadian business', country='CA', currency='CAD')
    command('approve', admission_id=admission)
    ready = service.create_business(owner, key, 'Canadian business', country='CA', currency='CAD')
    with database.transaction(read_only=True) as c:
        before = dict(c.execute(text('SELECT * FROM beta_admissions WHERE id=:id'), {'id': admission}).mappings().one())
        assert 'currency' not in before
    assert service.change_currency(owner, ready['entity_id'], 'USD', 1, True)['currency_code'] == 'USD'
    with database.transaction(read_only=True) as c:
        assert dict(c.execute(text('SELECT * FROM beta_admissions WHERE id=:id'), {'id': admission}).mappings().one()) == before
    with pytest.raises(IdentityError, match='conflict'):
        service.create_business(owner, key, 'Canadian business', country='US', currency='USD')
