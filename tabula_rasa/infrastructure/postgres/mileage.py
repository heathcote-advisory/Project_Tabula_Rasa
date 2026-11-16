"""Entity-scoped mileage persistence; routing remains outside the adapter."""

from dataclasses import replace as replace_trip
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from tabula_rasa.application.mileage import MileageTag, MileageTrip, prepare_mileage_trip
from tabula_rasa.errors import StorageConflictError, StorageValidationError
from tabula_rasa.quickbooks_mileage_import import mileage_fingerprint
from .provenance import MAX_IMPORT_ROWS, complete_import, completed_import, import_identity
from .concurrency import baseline_for
from .reference_data import PostgresTagRepository, ensure_vehicle_for_label
from .repository import EntityRepository, insert, new_object, record, replace, serialized


TRIP_FIELDS = (
    'id', 'vehicle', 'start_location', 'destination', 'business_purpose', 'notes', 'created_at',
)


class PostgresMileageRepository(EntityRepository):
    def tags(self, connection, *, lock=False):
        return tuple(MileageTag(row['id'], row['name'], row['active'], serialized(row['created_at']))
                     for row in self.rows(connection, 'tags', lock=lock))

    def list_tags(self, *, fresh=False):
        with self.operation(read_only=True) as connection:
            return self.tags(connection)

    def project(self, connection, row):
        values = record(row, 'mileage_records', TRIP_FIELDS)
        if row['vehicle_id']:
            values['vehicle'] = connection.scalar(text('SELECT name FROM vehicles WHERE entity_id=:scope AND id=:id'),
                {'scope': self.entity_id, 'id': row['vehicle_id']})
        labels = connection.execute(text(
            'SELECT t.id,t.name FROM mileage_tags m JOIN tags t '
            'ON t.entity_id=m.entity_id AND t.id=m.tag_id '
            'WHERE m.entity_id=:scope AND m.mileage_id=:id ORDER BY t.id'
        ), {'scope': self.entity_id, 'id': row['id']}).all()
        return MileageTrip(
            **values, vehicle_id=row['vehicle_id'], original_vehicle=row['vehicle'],
            trip_date=row['trip_date'].isoformat(), miles=format(row['miles'], '.1f'),
            tag_ids=tuple(item.id for item in labels), tags=tuple(item.name for item in labels),
        )

    def trips(self, connection):
        rows = connection.execute(text(
            'SELECT * FROM mileage_records WHERE entity_id=:scope '
            'ORDER BY trip_date DESC,created_at DESC,insertion_order DESC'
        ), {'scope': self.entity_id}).mappings()
        return tuple(self.project(connection, row) for row in rows)

    def list_trips(self, *, fresh=False):
        with self.operation(read_only=True) as connection:
            return self.trips(connection)

    def create_tag(self, name):
        item = PostgresTagRepository(self.database, self.entity_id).create(name)
        return MileageTag(item['id'], item['name'], item['active'] == 'TRUE', item['created_at'])

    def validate(self, connection, trip, *, current=None, tags=None):
        if not isinstance(trip, MileageTrip) or not trip.id.strip():
            raise StorageValidationError('A prepared mileage trip requires a stable identity.')
        if current and (trip.id != current.id or trip.created_at != current.created_at):
            raise StorageValidationError('Mileage identity and creation instant cannot change.')
        # Callback-created/reactivated tags are visible in this shared operation.
        prepared = prepare_mileage_trip(
            trip.as_record(), self.tags(connection, lock=True) if tags is None else tags,
            trip_id=trip.id, created_at=trip.created_at,
            original_tag_ids=current.tag_ids if current else (),
        )
        vehicle_id = prepared.vehicle_id
        if not vehicle_id and prepared.vehicle:
            vehicle_id = ensure_vehicle_for_label(connection, self.entity_id, prepared.vehicle)['id']
        if vehicle_id:
            vehicle = connection.execute(text('SELECT name,active FROM vehicles WHERE entity_id=:scope AND id=:id FOR SHARE'),
                {'scope': self.entity_id, 'id': vehicle_id}).mappings().first()
            if vehicle is None or not vehicle['active'] and (current is None or current.vehicle_id != vehicle_id):
                raise StorageValidationError('Select an active vehicle from this business.')
            prepared = replace_trip(prepared, vehicle_id=vehicle_id, vehicle=vehicle['name'])
        return prepared

    def write(self, connection, trip, *, create=False, current=None):
        values = {field: getattr(trip, field) for field in TRIP_FIELDS if field not in {'id', 'created_at'}}
        values.update(trip_date=date.fromisoformat(trip.trip_date), miles=Decimal(trip.miles))
        values['vehicle_id'] = trip.vehicle_id
        if not create:
            # Renames/reassignments retain source text. Explicitly clearing a
            # previously selected vehicle clears that trip's label, too.
            if current is not None and current.vehicle_id and not trip.vehicle_id:
                values['vehicle'] = ''
            else:
                values.pop('vehicle')
        if create:
            insert(connection, 'mileage_records', new_object(
                self.entity_id, id=trip.id,
                created_at=datetime.fromisoformat(trip.created_at.replace('Z', '+00:00')), **values,
            ))
        else:
            replace(connection, 'mileage_records', self.entity_id, trip.id, values)
        connection.execute(text('DELETE FROM mileage_tags WHERE entity_id=:scope AND mileage_id=:id'),
                           {'scope': self.entity_id, 'id': trip.id})
        for tag_id in trip.tag_ids:
            insert(connection, 'mileage_tags', {'entity_id': self.entity_id, 'mileage_id': trip.id, 'tag_id': tag_id})
        return self.project(connection, self.get_row(connection, 'mileage_records', trip.id))

    def add_trips(self, prepare):
        with self.operation() as connection:
            self.lock_entity(connection, key_share=True)
            trips = tuple(prepare(self.tags(connection, lock=True), self.create_tag))
            return self.add_prepared(connection, trips)

    def add_prepared(self, connection, trips, *, import_order=False):
        ids = [trip.id for trip in trips]
        if len(ids) != len(set(ids)):
            raise StorageConflictError('Prepared mileage identities are duplicated.')
        tags = self.tags(connection, lock=True)
        normalized = tuple(self.validate(connection, trip, tags=tags) for trip in trips)
        ordered = reversed(normalized) if import_order else normalized
        written = {trip.id: self.write(connection, trip, create=True) for trip in ordered}
        return tuple(written[trip.id] for trip in trips)

    def update_trip(self, trip_id, prepare, *, expected=None):
        with self.operation() as connection:
            self.lock_entity(connection, key_share=True)
            row = self.get_row(connection, 'mileage_records', trip_id, lock=True)
            current = self.project(connection, row)
            self.check_expected(row, 'mileage_records', expected, current.as_record())
            trip = prepare(current, self.tags(connection, lock=True), self.create_tag)
            return self.write(connection, self.validate(connection, trip, current=current), current=current)

    def delete_trip(self, trip_id, *, expected=None):
        with self.operation() as connection:
            self.lock_entity(connection, key_share=True)
            row = self.get_row(connection, 'mileage_records', trip_id, lock=True)
            self.check_expected(row, 'mileage_records', expected, self.project(connection, row).as_record())
            self.delete_row(connection, 'mileage_records', trip_id)

    def import_trips(self, prepare):
        with self.operation() as connection:
            # Existing writers take KEY SHARE first. This fresh duplicate selection
            # cannot race a supported trip create/edit/delete or another import.
            self.lock_entity(connection)
            trips = tuple(prepare(self.trips(connection), self.tags(connection, lock=True)))
            if len(trips) > MAX_IMPORT_ROWS:
                raise StorageValidationError('Import at most 1,000 accepted mileage trips per batch.')
            known = set(connection.execute(text(
                "SELECT fingerprint FROM import_records WHERE entity_id=:scope AND record_kind='mileage'"
            ), {'scope': self.entity_id}).scalars())
            unique = []
            for trip in trips:
                identity = mileage_fingerprint(trip.as_record())
                if identity not in known:
                    known.add(identity)
                    unique.append(trip)
            trips = tuple(unique)
            if not trips:
                return ()
            fingerprints = [mileage_fingerprint(trip.as_record()) for trip in trips]
            batch_id = import_identity('quickbooks-mileage', fingerprints)
            if completed_import(connection, self.entity_id, batch_id):
                return ()
            imported = self.add_prepared(connection, trips, import_order=True)
            complete_import(connection, self.entity_id, batch_id, 'quickbooks', 'mileage-prepared-v1',
                            [(position, 'mileage', trip.id, identity)
                             for position, (trip, identity) in enumerate(zip(imported, fingerprints), 1)])
            # Provenance details also advance mileage baselines.
            return tuple(replace_trip(trip, baseline=baseline_for(
                self.key('mileage_records', trip.id), self.get_row(connection, 'mileage_records', trip.id),
            )) for trip in imported)
