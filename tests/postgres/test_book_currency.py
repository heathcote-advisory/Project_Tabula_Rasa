"""Fixed-currency books, admission and private delivery with synthetic data."""

from io import BytesIO
import json
from uuid import uuid4
from zipfile import ZipFile

import pytest
from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.bookkeeping_export.format import content_digest, validate_artifact
from tabula_rasa.application.entity_export import ExportError
from tabula_rasa.errors import StorageValidationError
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services, create_postgres_export_service
from tabula_rasa.infrastructure.postgres.migrations import upgrade
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError
from tests.postgres._support import entity
from tests.postgres.core_support import seed_core
from tests.postgres.secondary_support import mixed, schedule
from tests.postgres.test_beta_admission import cohort as cohort
from tests.postgres.test_import_repository import csv, qb
from tests.postgres.test_entity_export import representative
from tests.test_transaction_import_files import csv_bytes


def test_currency_forward_migration_preserves_usd_and_refuses_redenomination(empty_database):
    from alembic import command
    from tabula_rasa.infrastructure.postgres.migrations import migration_config
    with empty_database.transaction() as c:
        command.upgrade(migration_config(c), '0019_beta_admission')
    with empty_database.transaction() as c:
        entity(c, 'legacy-usd')
        before = dict(c.execute(text('SELECT * FROM business_entities')).mappings().one())
    upgrade(empty_database)
    with empty_database.transaction(read_only=True) as c:
        assert dict(c.execute(text('SELECT * FROM business_entities')).mappings().one()) == before
    for currency in ('CAD', 'EUR'):
        with pytest.raises(DatabaseError, match='invariant'):
            with empty_database.transaction() as c:
                c.execute(text('UPDATE business_entities SET currency_code=:currency'), {'currency': currency})
    with pytest.raises(DatabaseError, match='invariant'):
        with empty_database.transaction() as c:
            entity(c, 'unsupported', currency='EUR')


def test_currency_settings_upgrade_removes_redundant_admission_unit_only(empty_database):
    from alembic import command
    from tabula_rasa.infrastructure.postgres.migrations import migration_config
    from tabula_rasa.infrastructure.postgres.control_plane import PostgresIdentityRepository
    from tests.postgres.test_identity import verified
    with empty_database.transaction() as c:
        command.upgrade(migration_config(c), '0022_drive_recovery_binding')
    owner = PostgresIdentityRepository(empty_database).resolve_user(verified('pending-cad'))
    with empty_database.transaction() as c:
        c.execute(text("""INSERT INTO beta_admissions(id,user_id,request_id,business_name,country,currency,state)
            VALUES ('pending',:user,'pending-request','Canadian business','CA','CAD','approved')"""), {'user': owner.id})
        before = dict(c.execute(text('SELECT * FROM beta_admissions')).mappings().one())
    upgrade(empty_database)
    with empty_database.transaction(read_only=True) as c:
        after = dict(c.execute(text('SELECT * FROM beta_admissions')).mappings().one())
        assert after == {key: value for key, value in before.items() if key != 'currency'}


def test_cad_approval_atomic_creation_replay_and_no_direct_api_bypass(database, cohort, monkeypatch):
    client, auth, _, person, _, _, beta, command, configure, service, request, _ = cohort
    raw, alice = person('cad-owner')
    configure()
    admission, key = request(alice, 'Canadian', country='CA', currency='CAD')
    assert not auth.sessions.identity.allowed_entities(alice.user_id)
    command('approve', admission_id=admission)
    from tabula_rasa.application.ledger_setup import LedgerSetupService
    original = LedgerSetupService.ensure_defaults
    with monkeypatch.context() as patch:
        patch.setattr(LedgerSetupService, 'ensure_defaults', lambda _: (_ for _ in ()).throw(RuntimeError('synthetic')))
        with pytest.raises(RuntimeError, match='synthetic'):
            service.create_business(alice, key, 'Canadian', country='CA', currency='CAD')
    assert LedgerSetupService.ensure_defaults is original
    assert not auth.sessions.identity.allowed_entities(alice.user_id)
    assert beta.status(alice.user_id)['requests'][0]['state'] == 'approved'
    with database.transaction(read_only=True) as c:
        assert c.scalar(text('SELECT count(*) FROM business_entities')) == 0
        assert c.scalar(text('SELECT count(*) FROM organizations')) == 0
    ready = service.create_business(alice, key, 'Canadian', country='CA', currency='CAD')
    assert service.create_business(alice, key, 'Canadian', country='CA', currency='CAD') == ready
    for name, country, currency in [('Canadian', 'US', 'USD'), ('Changed', 'CA', 'CAD')]:
        with pytest.raises(IdentityError, match='conflict'):
            service.create_business(alice, key, name, country=country, currency=currency)
    client.cookies.set(auth.session_cookie, raw)
    with client:
        session = client.get('/api/v1/session').json()
        assert session['entity']['currency_code'] == 'CAD'
        headers = {'Origin': auth.settings.public_origin, 'X-CSRF-Token': session['csrf_token']}
        for extra in ({'country': 'US', 'currency': 'CAD'}, {'country': 'CA', 'currency': 'USD'},
                      {'country': 'CA', 'currency': 'CAD', 'entity_id': ready['entity_id']}):
            result = client.post('/api/v1/businesses', headers=headers,
                json={'request_id': uuid4().hex, 'business_name': 'Forged', **extra})
            assert result.status_code == 422
    assert beta.status(alice.user_id)['requests'][0]['state'] == 'active'


def test_usd_cad_session_roles_imports_downloads_and_freezes_are_independent(database, cohort):
    client, auth, factory, person, _, _, _, command, configure, service, request, admit = cohort
    factory.side_effect = lambda context: create_postgres_services(database, context.entity_id)
    alice_raw, alice = person('alice')
    _, bob = person('bob')
    configure()
    _, _, usd = admit(alice, 'US business')
    ca, key = request(bob, 'CA business', country='CA', currency='CAD')
    command('approve', admission_id=ca)
    cad = service.create_business(bob, key, 'CA business', country='CA', currency='CAD')
    command('approve_user', admission_id=ca, user_id=alice.user_id)
    invitation = service.invite(bob, cad['organization_id'], alice.verified_email, 'read_only')
    service.respond(alice, invitation.credential, True)
    books = {code: create_postgres_services(database, result['entity_id']) for code, result in [('USD', usd), ('CAD', cad)]}
    # Identical account/category labels have independent identities and amounts.
    for code, book in books.items():
        account = book.accounts.list()[0]
        category = next(row for row in book.categories.list() if row['kind'] == 'Income')
        book.transactions.create(transaction_id='same-local-id', txn_date='2026-10-05', kind='Income',
            amount='123.45' if code == 'CAD' else '9.01', account_id=account['id'], category_id=category['id'])
    configure(creation_paused=True, invitations_paused=True)
    client.cookies.set(auth.session_cookie, alice_raw)
    with client:
        session = client.get('/api/v1/session').json()
        headers = {'Origin': auth.settings.public_origin, 'X-CSRF-Token': session['csrf_token']}
        choices = client.get('/api/v1/businesses').json()['items']
        assert {(item['currency_code'], item['role']) for item in choices} == {('USD', 'owner'), ('CAD', 'read_only')}
        for result, currency, role in [(cad, 'CAD', 'read_only'), (usd, 'USD', 'owner'), (cad, 'CAD', 'read_only')]:
            selected = client.post('/api/v1/session/entity', headers=headers, json={'entity_id': result['entity_id']})
            assert selected.status_code == 200
            assert selected.json()['entity']['currency_code'] == currency
            assert selected.json()['entity']['role'] == role
            prefix = '/api/v1/entities/' + result['entity_id']
            download = client.get(prefix + '/exports/transactions.csv')
            assert download.status_code == 200
            assert download.headers['x-book-currency'] == currency
            assert currency + '.csv' in download.headers['content-disposition']
            assert ('123.45' in download.text) == (currency == 'CAD')
            assert ('9.01' in download.text) == (currency == 'USD')
            other = usd if currency == 'CAD' else cad
            assert client.get('/api/v1/entities/' + other['entity_id'] + '/exports/transactions.csv').status_code == 403
            exported = client.post(prefix + '/exports/handoff', headers=headers, json={'scope': 'metadata_only'})
            assert exported.status_code == (200 if role == 'owner' else 403)
        assert client.post('/api/v1/session/entity', headers=headers,
                           json={'entity_id': cad['entity_id'], 'currency_code': 'USD'}).status_code == 422
        # The CAD owner retains import and export through an admission freeze.
        bob_raw, _ = person('bob')
        client.cookies.set(auth.session_cookie, bob_raw)
        session = client.get('/api/v1/session').json()
        headers['X-CSRF-Token'] = session['csrf_token']
        prefix = '/api/v1/entities/' + cad['entity_id']
        account = books['CAD'].accounts.list()[0]['name']
        category = next(row['name'] for row in books['CAD'].categories.list() if row['kind'] == 'Expense')
        source = csv(Account=account, Category=category)
        upload_headers = {**headers, 'Content-Type': 'application/octet-stream'}
        for suffix in ('/csv/prepare', '/csv'):
            response = client.post(prefix + '/imports' + suffix, headers=upload_headers,
                                   content=csv_bytes([{**source, 'Currency': 'USD'}]))
            assert response.status_code == 422
        good = client.post(prefix + '/imports/csv', headers=upload_headers, content=csv_bytes([source]))
        assert good.status_code == 200 and good.json()['imported_count'] == 1
        result = client.post(prefix + '/exports/handoff', headers=headers, json={'scope': 'metadata_only'})
        assert result.status_code == 200
        with ZipFile(BytesIO(result.content)) as archive:
            artifact = json.loads(archive.read('bookkeeping.json'))
            assert b'Book currency: CAD.' in archive.read('README.txt')
            validate_artifact(artifact, entity_id=cad['entity_id'])
            assert artifact['domains']['business_entities'][0]['currency_code'] == 'CAD'
            assert {row['amount'] for row in artifact['domains']['postings']} == {'123.45', '12.50'}
    assert len(books['USD'].transactions.list()) == 1


def test_cad_uses_aggregate_cap_freeze_and_expiry_even_for_an_internal_owner(database, cohort):
    _, auth, _, person, _, _, _, command, configure, service, request, admit = cohort
    _, alice = person('internal-owner')
    from tests.postgres.test_workspaces import approve_internal
    approve_internal(database, alice.user_id)
    configure()
    # USD internal use stays outside the beta program; CAD has no exemption.
    internal = service.create_business(alice, uuid4().hex, 'Internal USD')
    assert internal['state'] == 'ready'
    ca, key = request(alice, 'Approved CAD', country='CA', currency='CAD')
    command('approve', admission_id=ca)
    configure(creation_paused=True)
    with pytest.raises(IdentityError, match='beta_paused'):
        service.create_business(alice, key, 'Approved CAD', country='CA', currency='CAD')
    configure()
    with database.transaction() as c:
        c.execute(text('UPDATE beta_admissions SET expires_at=clock_timestamp() WHERE id=:id'), {'id': ca})
    assert service.create_business(alice, key, 'Approved CAD', country='CA', currency='CAD')['state'] == 'expired'
    command('approve', admission_id=ca)
    assert service.create_business(alice, key, 'Approved CAD', country='CA', currency='CAD')['state'] == 'ready'
    _, outsider = person('external-owner')
    admit(outsider, 'External USD')
    third, _ = request(outsider, 'Third CAD', country='CA', currency='CAD')
    command('approve', admission_id=third)
    fourth, _ = request(outsider, 'Fourth CAD', country='CA', currency='CAD')
    with pytest.raises(IdentityError, match='beta_capacity'):
        command('approve', admission_id=fourth)
    assert len(auth.sessions.identity.allowed_entities(alice.user_id)) == 2


@pytest.mark.parametrize('approved', [False, True])
def test_internal_usd_exemption_cannot_reinterpret_an_existing_cad_request(database, cohort, approved):
    _, auth, _, person, _, _, beta, command, configure, service, request, _ = cohort
    from tests.postgres.test_workspaces import approve_internal

    _, alice = person('internal-cad-request')
    approve_internal(database, alice.user_id)
    configure()
    internal = service.create_business(alice, uuid4().hex, 'Existing USD')
    ca, key = request(alice, 'Bound CAD', country='CA', currency='CAD')
    if approved:
        command('approve', admission_id=ca)
    expected_state = 'approved' if approved else 'pending'
    for name, country, currency in [('Bound CAD', 'US', 'USD'), ('Changed', 'US', 'USD'), ('Changed', 'CA', 'CAD')]:
        with pytest.raises(IdentityError, match='conflict'):
            service.create_business(alice, key, name, country=country, currency=currency)
    with database.transaction(read_only=True) as c:
        assert c.scalar(text('SELECT count(*) FROM business_entities')) == 1
        assert c.scalar(text('SELECT currency_code FROM business_entities WHERE id=:id'),
                        {'id': internal['entity_id']}) == 'USD'
        row = c.execute(text('SELECT state,country,organization_id FROM beta_admissions WHERE id=:id'),
                        {'id': ca}).mappings().one()
        assert dict(row) == dict(state=expected_state, country='CA', organization_id=None)
    assert beta.status(alice.user_id)['requests'][0]['state'] == expected_state
    assert len(auth.sessions.identity.allowed_entities(alice.user_id)) == 1
    if not approved:
        assert service.create_business(alice, key, 'Bound CAD', country='CA', currency='CAD')['state'] == 'pending'
        command('approve', admission_id=ca)
    ready = service.create_business(alice, key, 'Bound CAD', country='CA', currency='CAD')
    assert ready['state'] == 'ready'
    assert service.create_business(alice, key, 'Bound CAD', country='CA', currency='CAD') == ready
    currencies = {item.entity_id: item.currency_code for item in auth.sessions.identity.allowed_entities(alice.user_id)}
    assert currencies == {internal['entity_id']: 'USD', ready['entity_id']: 'CAD'}


def test_cad_import_currency_replay_recurring_and_mixed_use(database):
    cad, usd = seed_core(database, currencies=('CAD', 'USD'))
    assert cad.ledger.get_info().currency_code == 'CAD'
    assert usd.ledger.get_info().currency_code == 'USD'
    for source in ('csv', 'quickbooks'):
        rows = [csv()] if source == 'csv' else [qb(Num='QB')]
        commit = getattr(cad.imports, 'import_' + source)
        assert len(commit(iter(rows))) == 1
        # Even a batch with a completed currency-free identity must be checked.
        for column in ('Currency', 'Currency Code', 'currency_code'):
            with pytest.raises(StorageValidationError, match='Source currency'):
                commit(iter([{**rows[0], column: 'USD'}]))
        assert commit([{**rows[0], 'Currency': 'CAD'}]) == []
    with pytest.raises(StorageValidationError, match='Source currency'):
        cad.imports.preview_quickbooks([qb(Currency='EUR')])
    pair = cad.mixed_use.create(source_id='mixed', **mixed())
    assert pair['allocated_amount'] == '33.33'
    cad.recurring.create(schedule_id='monthly', **schedule(amount='8.75'))
    result = cad.recurring.reconcile(as_of='2026-01-01')
    assert len(result['generated']) == 1
    assert any(row['amount'] == '8.75' for row in cad.transactions.list())
    assert usd.transactions.list() == usd.mixed_use.list() == usd.recurring.list() == []


def test_cad_complete_canonical_export_preserves_all_domains_and_rejects_unknown_currency(database):
    cad, _ = representative(database, currencies=('CAD', 'USD'))
    artifact = create_postgres_export_service(database, 'alpha').export()
    validate_artifact(artifact, entity_id='alpha')
    assert artifact['manifest']['format_version'] == 2
    assert artifact['domains']['business_entities'][0]['currency_code'] == 'CAD'
    assert artifact['domains']['mixed_use_sources'][0]['business_use_percentage'] == '33.33333'
    assert artifact['domains']['mileage_records'][0]['miles'] == '1.2'
    snapshot = cad.reporting.read_ledger()
    assert cad.reporting.period_report(snapshot).totals == {'income': 100.0, 'expenses': 38.0, 'net': 62.0}
    assert cad.reporting.balance_sheet(snapshot)['difference'] == 0
    assert create_postgres_export_service(database, 'beta').export()['domains']['business_entities'][0]['currency_code'] == 'USD'
    artifact['domains']['business_entities'][0]['currency_code'] = 'EUR'
    artifact['manifest']['content_sha256'] = content_digest(artifact['domains'])
    with pytest.raises(ExportError, match='unsupported_currency'):
        validate_artifact(artifact, entity_id='alpha')


def test_retained_usd_sheets_source_cannot_load_into_cad(database):
    from tabula_rasa.infrastructure.postgres.migration import PostgresMigrationRunner
    from tabula_rasa.infrastructure.postgres.reference_data import PostgresLedgerSetupRepository
    from tabula_rasa.migration.model import MigrationError
    from tests.test_sheets_migration_source import plan_for, values_with_records

    plan = plan_for(values_with_records())
    scope = plan.snapshot.binding.entity_id
    PostgresLedgerSetupRepository.create_entity(database, scope,
        plan.snapshot.metadata.get('entity_name', scope), currency='CAD')
    with pytest.raises(MigrationError, match='identity/defaults differ'):
        PostgresMigrationRunner(database).execute(plan, dry_run=False)
    with database.transaction(read_only=True) as c:
        assert c.scalar(text('SELECT currency_code FROM business_entities WHERE id=:scope'), {'scope': scope}) == 'CAD'
        assert c.scalar(text('SELECT count(*) FROM accounts')) == 0
        assert c.scalar(text('SELECT count(*) FROM migration_runs')) == 0
