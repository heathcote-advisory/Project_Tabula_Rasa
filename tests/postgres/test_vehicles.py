"""Managed vehicles preserve mileage history and isolate business references."""

import pytest
from alembic import command
from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.errors import StorageConflictError, StorageValidationError, StorageError
from tabula_rasa.infrastructure.postgres.migrations import upgrade, migration_config
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services, create_postgres_export_service
from tests.postgres._support import entity
from tests.postgres.test_identity_http import client_for
from tests.postgres.test_identity import setup, verified


TRIP = dict(trip_date='2026-11-01', miles='12.3', start_location='Office', destination='Client', business_purpose='Meeting')


def test_vehicle_references_rename_deactivation_stale_delete_and_business_isolation(database):
    with database.transaction() as c:
        entity(c, 'alpha')
        entity(c, 'beta')
    alpha, beta = (create_postgres_services(database, scope) for scope in ('alpha', 'beta'))
    van = alpha.vehicles.create('Van')
    assert alpha.vehicles.create('Van')['id'] == van['id']
    foreign = beta.vehicles.create('Van')
    trip = alpha.mileage.create_trip(**TRIP, vehicle_id=van['id'])
    assert trip.vehicle == 'Van' and trip.vehicle_id == van['id']
    renamed = alpha.vehicles.update(van['id'], name='Fleet van', expected={'baseline': van['baseline']})
    assert renamed['id'] == van['id']
    assert alpha.mileage.get_trip(trip.id).vehicle == 'Fleet van'
    with pytest.raises(StorageConflictError, match='changed'):
        alpha.vehicles.update(van['id'], name='Stale', expected={'baseline': van['baseline']})
    with pytest.raises(StorageError):
        alpha.vehicles.remove(van['id'], expected={'baseline': renamed['baseline']})
    inactive = alpha.vehicles.update(van['id'], active=False, expected={'baseline': renamed['baseline']})
    assert alpha.vehicles.list(active_only=True) == []
    assert alpha.mileage.edit_trip(trip.id, notes='Still valid', expected={'baseline': trip.baseline}).vehicle_id == van['id']
    for identity in (van['id'], foreign['id'], 'missing'):
        with pytest.raises(StorageValidationError, match='active vehicle'):
            alpha.mileage.create_round_trip(**TRIP, vehicle_id=identity)
    assert len(alpha.mileage.list_trips()) == 1 and beta.mileage.list_trips() == ()
    artifact = create_postgres_export_service(database, 'alpha').export()
    assert artifact['manifest']['format_version'] == 2
    assert artifact['domains']['mileage_records'][0]['vehicle_id'] == van['id']
    assert artifact['domains']['mileage_records'][0]['vehicle'] == 'Van'
    assert artifact['domains']['vehicles'][0]['name'] == 'Fleet van'
    current = alpha.mileage.get_trip(trip.id)
    cleared = alpha.mileage.edit_trip(trip.id, vehicle_id=None, vehicle='', expected={'baseline': current.baseline})
    assert cleared.vehicle_id is None and cleared.vehicle == '' and cleared.miles == '12.3'
    current = alpha.mileage.get_trip(trip.id)
    alpha.mileage.delete_trip(trip.id, expected={'baseline': current.baseline})
    alpha.vehicles.remove(van['id'], expected={'baseline': inactive['baseline']})
    assert alpha.vehicles.list() == [] and len(beta.vehicles.list()) == 1


def test_forward_vehicle_backfill_is_exact_lossless_and_repeatable(empty_database):
    with empty_database.transaction() as c:
        command.upgrade(migration_config(c), '0023_business_currency')
        entity(c, 'alpha')
        entity(c, 'beta')
        for scope in ('alpha', 'beta'):
            for index, label in enumerate(['Van', 'Van', 'van', ' Van ', '', '   ']):
                c.execute(text('''INSERT INTO mileage_records(entity_id,id,incarnation_id,trip_date,miles,
                    vehicle,start_location,destination,business_purpose)
                    VALUES (:scope,:id,:id,'2026-11-01',12.3,:label,'Office','Client','Meeting')'''),
                    {'scope': scope, 'id': str(index), 'label': label})
        before = c.execute(text('SELECT entity_id,id,vehicle,miles FROM mileage_records ORDER BY entity_id,id')).all()
    upgrade(empty_database)
    with empty_database.transaction(read_only=True) as c:
        assert c.execute(text('SELECT entity_id,id,vehicle,miles FROM mileage_records ORDER BY entity_id,id')).all() == before
        catalog = c.execute(text('SELECT entity_id,id,name FROM vehicles ORDER BY entity_id,name')).all()
        assert len(catalog) == 6
        assert len({row.id for row in catalog}) == 6
        rows = c.execute(text("SELECT id,vehicle_id FROM mileage_records WHERE entity_id='alpha' ORDER BY id")).all()
        assert rows[0].vehicle_id == rows[1].vehicle_id
        assert len({row.vehicle_id for row in rows[:4]}) == 3
        assert rows[4].vehicle_id is rows[5].vehicle_id is None
    upgrade(empty_database)
    with empty_database.transaction(read_only=True) as c:
        assert c.execute(text('SELECT entity_id,id,name FROM vehicles ORDER BY entity_id,name')).all() == catalog
    services = create_postgres_services(empty_database, 'alpha')
    assert {trip.id: trip.vehicle for trip in services.mileage.list_trips()} == {
        str(index): label for index, label in enumerate(['Van', 'Van', 'van', ' Van ', '', '   '])}
    padded = next(row for row in services.vehicles.list() if row['name'] == ' Van ')
    services.vehicles.update(padded['id'], name=padded['name'], active=False, expected={'baseline': padded['baseline']})
    assert next(row for row in services.vehicles.list() if row['id'] == padded['id'])['name'] == ' Van '


@pytest.mark.parametrize('role', ['owner', 'admin', 'editor', 'accountant', 'read_only'])
def test_vehicle_http_role_matrix_and_protected_baselines(database, role):
    repo, alice, _, org, _ = setup(database)
    client, auth, factory = client_for(database)
    factory.side_effect = lambda ctx: create_postgres_services(database, ctx.entity_id)
    raw, _ = auth.sessions.issue(verified())
    if role != 'owner':
        # Retain another owner so last-owner protection is not the test trigger.
        other = repo.resolve_user(verified('other-owner'))
        repo.set_membership(org, other.id, 'owner')
        repo.set_membership(org, alice.id, role)
    client.cookies.set(auth.session_cookie, raw)
    with client:
        session = client.get('/api/v1/session').json()
        headers = {'Origin': auth.settings.public_origin, 'X-CSRF-Token': session['csrf_token']}
        route = '/api/v1/entities/alpha/vehicles'
        assert client.get(route).status_code == 200
        assert client.get('/api/v1/entities/beta/vehicles').status_code == 403
        created = client.post(route, json={'name': 'Van'}, headers=headers)
        assert created.status_code == (403 if role == 'read_only' else 201)
        if role != 'read_only':
            vehicle = created.json()
            update = {'baseline': vehicle['baseline'], 'changes': {'name': 'New van', 'active': False}}
            assert client.patch(route + '/' + vehicle['id'], json=update, headers=headers).status_code == 200
            assert client.patch(route + '/' + vehicle['id'], json=update, headers=headers).status_code == 409
            assert client.patch(route + '/' + vehicle['id'], json={'changes': update['changes']}, headers=headers).status_code == 422


def test_mileage_import_keeps_source_identity_after_rename_and_does_not_reactivate(database):
    from tests.postgres.core_support import seed_core
    from tests.postgres.test_mileage_repository import source
    alpha, beta = seed_core(database)
    imported = alpha.mileage.import_quickbooks_mileage([source()])
    assert len(imported) == 1
    vehicle = alpha.vehicles.list()[0]
    alpha.vehicles.update(vehicle['id'], name='Renamed car', expected={'baseline': vehicle['baseline']})
    assert alpha.mileage.preview_quickbooks_mileage([source()]).summary['duplicates'] == 1
    assert alpha.mileage.preview_quickbooks_mileage([source(Vehicle='Renamed car')]).summary['duplicates'] == 1
    assert alpha.mileage.import_quickbooks_mileage([source()]) == ()
    vehicle = alpha.vehicles.list()[0]
    alpha.vehicles.update(vehicle['id'], active=False, expected={'baseline': vehicle['baseline']})
    with pytest.raises(StorageValidationError, match='active vehicle'):
        alpha.mileage.import_quickbooks_mileage([source(Vehicle='Renamed car', Date='10/06/2026')])
    assert alpha.vehicles.list()[0]['active'] == 'FALSE'
    assert len(alpha.mileage.list_trips()) == 1
    assert beta.vehicles.list() == [] and beta.mileage.list_trips() == ()


def test_vehicle_upgrade_preserves_closed_books_and_restores_write_guards(empty_database):
    from tabula_rasa.infrastructure.postgres.customer_lifecycle import PostgresCustomerLifecycle
    from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository
    with empty_database.transaction() as c:
        command.upgrade(migration_config(c), '0023_business_currency')
    setup(empty_database)
    sessions = PostgresSessionRepository(empty_database, 'test', 'a' * 64)
    sessions.configure()
    _, owner = sessions.issue(verified())
    with empty_database.transaction() as c:
        c.execute(text("""INSERT INTO mileage_records(entity_id,id,incarnation_id,trip_date,miles,
            vehicle,start_location,destination,business_purpose)
            VALUES ('alpha','closed-trip','trip','2026-11-01',12.3,'Van','Office','Client','Meeting')"""))
    closures = PostgresCustomerLifecycle(empty_database)
    request = closures.request(owner, 'closed-vehicle-migration', 'business_closure', 'alpha')
    closures.change(owner, request['id'], 'confirm', 1, export_saved=True)
    upgrade(empty_database)
    services = create_postgres_services(empty_database, 'alpha')
    trip = services.mileage.get_trip('closed-trip')
    assert trip.vehicle == 'Van' and trip.miles == '12.3' and trip.vehicle_id is not None
    with pytest.raises(IdentityError, match='forbidden'):
        services.vehicles.create('Blocked vehicle')
    with pytest.raises(IdentityError, match='forbidden'):
        services.mileage.create_trip(**TRIP)
    assert len(services.vehicles.list()) == 1
