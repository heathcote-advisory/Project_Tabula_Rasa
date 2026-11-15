"""Schedules, atomic initial conversions, and independent recurring commit units."""

from collections.abc import Mapping
from datetime import date
from uuid import uuid4

from sqlalchemy import text

from tabula_rasa.application.recurring_contracts import RecurringSnapshot
from tabula_rasa.errors import StorageConflictError, StorageValidationError
from tabula_rasa.ledger_validation import decode_receipt_references
from tabula_rasa.recurring import (
    SCHEDULE_FIELDS, occurrence_data, reject_existing_recurring_metadata, schedule_template,
)
from tabula_rasa.reference_data import clean_text, normalize_payee_name, normalize_tag_name, normalize_tag_ids
from tabula_rasa.transaction import RECURRING_FIELDS, reject_derived_transaction
from .accounting import PostgresTransactionRepository
from .mixed_use import PostgresMixedUseRepository
from .operations import repository_operation
from .repository import EntityRepository, insert, new_object, record, replace
from .snapshots import check_expected_snapshot
from .source_accounting import tag_fields, write_occurrence, write_tags


SCHEDULE_COLUMNS = tuple(field for field in SCHEDULE_FIELDS if field not in {'kind', 'tag_ids'})
SCHEDULE_EDITABLE = tuple(field for field in SCHEDULE_FIELDS if field not in
                          {'created_at', 'updated_at', 'attention_code', 'attention_at'})


class PostgresRecurringRepository(EntityRepository):
    def __init__(self, database, entity_id, *, transactions, mixed_use):
        super().__init__(database, entity_id)
        self.transaction_service = transactions
        self.mixed_service = mixed_use
        self.transactions = PostgresTransactionRepository(database, entity_id)
        self.mixed = PostgresMixedUseRepository(database, entity_id)

    def project(self, connection, row):
        result = record(row, 'recurring_schedules', SCHEDULE_COLUMNS)
        for field in ('anchor_date', 'start_date', 'end_date'):
            result[field] = row[field].isoformat() if row[field] else ''
        result.update(kind='Expense', recurrence_interval=str(row['recurrence_interval']))
        if row['business_use_percentage'] is not None:
            result['business_use_percentage'] = format(row['business_use_percentage'].normalize(), 'f')
        result.update(tag_fields(connection, self.entity_id, 'schedule_tags', 'schedule_id', row['id']))
        return result

    @staticmethod
    def template(data, references, **options):
        return schedule_template(data, account_lookup=references.accounts, category_lookup=references.categories,
                                 payee_lookup=references.payees, tag_lookup=references.tags, **options)

    def write(self, connection, schedule, *, create=False):
        values = {field: schedule[field] for field in SCHEDULE_COLUMNS if field not in {'id', 'created_at', 'updated_at'}}
        for field in ('end_date', 'payee_id', 'attention_at', 'total_expense', 'business_use_percentage'):
            values[field] = values[field] or None
        if create:
            insert(connection, 'recurring_schedules', new_object(self.entity_id, id=schedule['id'],
                   created_at=schedule['created_at'], **values))
        else:
            replace(connection, 'recurring_schedules', self.entity_id, schedule['id'], values)
        write_tags(connection, self.entity_id, 'schedule_tags', 'schedule_id', schedule['id'], schedule['tag_ids'])
        return self.project(connection, self.get_row(connection, 'recurring_schedules', schedule['id']))

    def snapshot(self, *, fresh=False):
        with repository_operation(self.database, read_only=True, require_outer=True) as connection:
            return RecurringSnapshot(
                [self.project(connection, row) for row in self.rows(connection, 'recurring_schedules')],
                [self.transactions.project(connection, row) for row in self.transactions.entry_rows(connection)],
                [self.mixed.project(connection, row) for row in self.rows(connection, 'mixed_use_sources')],
                self.transactions.references(connection),
            )

    def create(self, schedule_id, *, build):
        with self.operation() as connection:
            existing = connection.execute(text('SELECT * FROM recurring_schedules WHERE entity_id=:scope AND id=:id'),
                                          {'scope': self.entity_id, 'id': schedule_id}).mappings().first()
            refs = self.transactions.references(connection, lock=True,
                   root_keys=[self.key('recurring_schedules', schedule_id)] if existing else [])
            current = self.project(connection, self.get_row(connection, 'recurring_schedules', schedule_id)) if existing else None
            built = dict(build(refs, current))
            prepared = self.template(built, refs, schedule_id=schedule_id, created_at=built.get('created_at'),
                                     require_active_references=True)
            if current:
                check_expected_snapshot(current, {field: prepared[field] for field in SCHEDULE_EDITABLE})
                return current
            return self.write(connection, prepared, create=True)

    def update(self, schedule_id, *, expected=None, build):
        with self.operation() as connection:
            refs = self.transactions.references(connection, lock=True, root_keys=[self.key('recurring_schedules', schedule_id)])
            row = self.get_row(connection, 'recurring_schedules', schedule_id)
            current = self.project(connection, row)
            # Attention and due/display metadata are outside the editable token.
            ignored = {'updated_at', 'attention_code', 'attention_at', 'attention_message',
                       'next_due_date', 'latest_current_occurrence_date'}
            filtered = {key: value for key, value in expected.items() if key not in ignored} if isinstance(expected, Mapping) else expected
            self.check_expected(row, 'recurring_schedules', filtered, current)
            built = dict(build(refs, current))
            if built.get('id') != schedule_id:
                raise StorageValidationError('Schedule identity cannot change.')
            prepared = self.template(built, refs, schedule_id=schedule_id, created_at=current['created_at'])
            return self.write(connection, prepared)

    @staticmethod
    def unused(schedule, occurrences):
        return (len(occurrences) == 1
                and occurrences[0]['occurrence_type'] == 'initial'
                and occurrences[0]['source_kind'] == schedule['source_kind']
                and occurrences[0]['scheduled_date'] == schedule['start_date'])

    def occurrence_rows(self, connection, schedule_id):
        return list(connection.execute(text(
            'SELECT * FROM recurring_occurrences WHERE entity_id=:scope AND schedule_id=:id'
        ), {'scope': self.entity_id, 'id': schedule_id}).mappings())

    def can_delete(self, schedule_id):
        with self.operation(read_only=True) as connection:
            row = self.get_row(connection, 'recurring_schedules', schedule_id)
            return self.unused(row, self.occurrence_rows(connection, schedule_id))

    def delete(self, schedule_id, *, expected=None):
        with self.operation() as connection:
            # Discover occurrence roots, then acquire the same reference/root order
            # used by generation and source editors. Recheck after waiting on locks.
            schedule = self.get_row(connection, 'recurring_schedules', schedule_id)
            occurrences = self.occurrence_rows(connection, schedule_id)
            if not self.unused(schedule, occurrences):
                raise StorageConflictError('Only a schedule with its sole original occurrence can be deleted. End it to retain its history.', code='recurring_in_use')
            roots = [self.key('recurring_schedules', schedule_id)]
            for occurrence in occurrences:
                if occurrence['entry_id']:
                    roots.append(self.key('entries', occurrence['entry_id']))
                if occurrence['mixed_use_source_id']:
                    source = self.get_row(connection, 'mixed_use_sources', occurrence['mixed_use_source_id'])
                    roots.extend([self.key('mixed_use_sources', source['id']),
                                  self.key('entries', source['expected_projection_entry_id'])])
            self.transactions.references(connection, lock=True, root_keys=roots)
            row = self.get_row(connection, 'recurring_schedules', schedule_id)
            current = self.project(connection, row)
            ignored = {'updated_at', 'attention_code', 'attention_at', 'attention_message',
                       'next_due_date', 'latest_current_occurrence_date'}
            filtered = {key: value for key, value in expected.items() if key not in ignored} if isinstance(expected, Mapping) else expected
            self.check_expected(row, 'recurring_schedules', filtered, current)
            if not self.unused(row, self.occurrence_rows(connection, schedule_id)):
                raise StorageConflictError('Only a schedule with its sole original occurrence can be deleted. End it to retain its history.', code='recurring_in_use')
            # Provenance is stored only in this association. Version triggers
            # invalidate loaded source/entry baselines; accounting rows stay intact.
            connection.execute(text('DELETE FROM recurring_occurrences WHERE entity_id=:scope AND schedule_id=:id'),
                               {'scope': self.entity_id, 'id': schedule_id})
            self.delete_row(connection, 'recurring_schedules', schedule_id)

    def reconcile(self, process):
        # The read snapshot closes before application generation starts. Each
        # occurrence and attention write opens its own supported commit unit.
        return process(self.snapshot(fresh=True))

    def attention(self, schedule_id, code):
        with repository_operation(self.database, require_outer=True) as connection:
            self.get_row(connection, 'recurring_schedules', schedule_id, lock=True)
            connection.execute(text(
                "UPDATE recurring_schedules SET attention_code=:code, "
                "attention_at=CASE WHEN :code='' THEN NULL ELSE CURRENT_TIMESTAMP END WHERE entity_id=:scope AND id=:id"
            ), {'scope': self.entity_id, 'id': schedule_id, 'code': code})

    def existing_schedule_keys(self, connection, identity):
        if identity and connection.execute(text('SELECT 1 FROM recurring_schedules WHERE entity_id=:scope AND id=:id'),
                                           {'scope': self.entity_id, 'id': identity}).first():
            return [self.key('recurring_schedules', identity)]
        return []

    def prepare_initial(self, connection, data, *, mixed, schedule_locked, current=None, projection=None):
        identity = clean_text(data.get('schedule_id'), 'Schedule ID') or str(uuid4())
        if identity in {data.get('transaction_id'), data.get('source_id'), data.get('linked_transaction_id')}:
            raise StorageValidationError('Schedule identity must be distinct from its occurrence identities.')
        refs = self.transactions.references(connection, lock=True)
        template_data = {**data, 'receipt_references': []}
        # Preserve preflight validation and then resolve inline references via
        # the same application services, inside this atomic initial operation.
        self.template({**template_data, 'source_kind': 'mixed_use' if mixed else 'transaction'}, refs,
                      schedule_id=identity, require_active_references=True)
        if mixed:
            source, prepared = self.mixed_service.prepare_pair(
                data, refs, source_id=data['source_id'], linked_transaction_id=data['linked_transaction_id'],
                created_at=current['created_at'] if current else None, original_transaction=projection,
            )
            resolved = {**template_data, **source, 'amount': source['allocated_amount'], 'receipt_references': []}
        else:
            prepared = self.transaction_service.prepare_source_projection(
                data, refs, transaction_id=data['transaction_id'], created_at=current['created_at'] if current else None,
                original_transaction=current,
            )
            source = None
            resolved = {**template_data, **prepared, 'receipt_references': []}
        refs = self.transactions.inline_references(connection, refs, prepared)
        schedule = self.template({**resolved, 'source_kind': 'mixed_use' if mixed else 'transaction'}, refs,
                                 schedule_id=identity, require_active_references=True)
        existing = connection.execute(text('SELECT * FROM recurring_schedules WHERE entity_id=:scope AND id=:id'),
                                      {'scope': self.entity_id, 'id': identity}).mappings().first()
        if existing:
            if not schedule_locked:
                raise StorageConflictError('The schedule appeared after lock discovery; reload before trying again.')
            stored = self.project(connection, existing)
            check_expected_snapshot(stored, {field: schedule[field] for field in SCHEDULE_EDITABLE})
            schedule = stored
        else:
            schedule = self.write(connection, schedule, create=True)
        provenance = dict(recurring_schedule_id=identity, recurring_scheduled_date=schedule['start_date'],
                          recurring_source_kind=schedule['source_kind'], recurring_occurrence_type='initial')
        prepared.update(provenance)
        if source is not None:
            source.update(provenance)
        return schedule, source, prepared, refs

    def initial_result(self, connection, schedule_id, *, entry_id=None, source_id=None):
        schedule = self.project(connection, self.get_row(connection, 'recurring_schedules', schedule_id))
        result = {'schedule': schedule}
        if source_id:
            source, projection = self.mixed.complete_pair(connection, self.get_row(connection, 'mixed_use_sources', source_id))
            result.update(source=source, transaction=projection)
        else:
            result['transaction'] = self.transactions.project(connection, self.transactions.entry(connection, entry_id))
        return result

    def create_expense(self, data, *, mixed=False):
        reject_existing_recurring_metadata(data, 'Expense')
        if decode_receipt_references(data.get('receipt_references')):
            raise StorageValidationError('Receipts cannot be part of a recurring template.')
        identity = clean_text(data.get('schedule_id'), 'Schedule ID') or str(uuid4())
        # Derive stable initial identities with the same domain owner as future occurrences.
        payload = {**data, 'schedule_id': identity, 'source_kind': 'mixed_use' if mixed else 'transaction'}
        with self.operation() as connection:
            schedule_keys = self.existing_schedule_keys(connection, identity)
            refs = self.transactions.references(connection, lock=True, root_keys=schedule_keys)
            template = self.template(payload, refs, schedule_id=identity)
            initial = occurrence_data(template, date.fromisoformat(template['start_date']), role='initial')
            payload = {**initial, **payload}
            if mixed:
                payload['source_id'] = data.get('source_id') or initial['source_id']
                payload['linked_transaction_id'] = data.get('linked_transaction_id') or data.get('transaction_id') or initial['linked_transaction_id']
            else:
                payload['transaction_id'] = data.get('transaction_id') or initial['transaction_id']
            for field in RECURRING_FIELDS:
                payload.pop(field, None)
            schedule, source, prepared, refs = self.prepare_initial(connection, payload, mixed=mixed, schedule_locked=bool(schedule_keys))
            existing = connection.execute(text('SELECT * FROM recurring_occurrences WHERE entity_id=:scope AND schedule_id=:id AND scheduled_date=:date'),
                                          {'scope': self.entity_id, 'id': identity, 'date': schedule['start_date']}).mappings().first()
            if existing:
                result = self.initial_result(connection, identity, entry_id=existing['entry_id'], source_id=existing['mixed_use_source_id'])
                requested = source if mixed else prepared
                stored = result['source'] if mixed else result['transaction']
                check_expected_snapshot(stored, {key: value for key, value in requested.items() if key not in
                    {'id', 'linked_transaction_id', 'created_at', 'baseline', 'recurring_occurrence_type'}})
                return result
            if mixed:
                prepared = self.mixed.validate_pair(connection, source, prepared, refs)
                self.mixed.write_pair(connection, source, prepared, create=True, recurring=True)
            else:
                prepared = self.transactions.prepare(prepared, refs, prepared['id'], recurring=True)
                self.transactions.write(connection, prepared['id'], prepared, create=True)
                write_occurrence(connection, self.entity_id, prepared, entry_id=prepared['id'])
            return self.initial_result(connection, identity, entry_id=prepared['id'], source_id=source['id'] if mixed else None)

    def confirm_conversion(self, connection, current, projection, changes, refs, *, mixed):
        # A stable schedule identity can confirm an already committed conversion
        # without another mutation, including after an unconfirmed response.
        identity = changes.get('schedule_id')
        if (not identity or identity != current['recurring_schedule_id']
                or current['recurring_occurrence_type'] != 'initial'):
            reject_existing_recurring_metadata(current, 'Expense')
        schedule = self.project(connection, self.get_row(connection, 'recurring_schedules', identity))
        data = {**{key: value for key, value in current.items() if key != 'payee'}, **changes}
        for name_field, lookup, normalize, selected in (
            ('new_payee_name', refs.payees, normalize_payee_name, [schedule['payee_id']]),
            ('new_tag_name', refs.tags, normalize_tag_name, normalize_tag_ids(schedule['tag_ids'])),
        ):
            if data.get(name_field):
                resolved = [item for item in selected if item in lookup
                            and normalize(lookup[item]['name']) == normalize(data[name_field])]
                if not resolved:
                    raise StorageConflictError('The recurring conversion differs from the requested data.')
                # A full HTTP draft still contains the pre-create association
                # fields. Reuse the already confirmed inline references while
                # comparing the replay; do not create or reactivate references.
                if name_field == 'new_payee_name':
                    if data.get('payee_id') and data['payee_id'] != resolved[0]:
                        raise StorageConflictError('The recurring conversion differs from the requested data.')
                    data['payee_id'] = resolved[0]
                else:
                    data['tag_ids'] = sorted(set(normalize_tag_ids(data.get('tag_ids', []))) | set(resolved))
            data[name_field] = ''
        requested = self.template({**data, 'source_kind': 'mixed_use' if mixed else 'transaction',
                                  'receipt_references': []}, refs, schedule_id=identity)
        check_expected_snapshot(schedule, {field: requested[field] for field in SCHEDULE_EDITABLE})
        if current['recurring_scheduled_date'] != schedule['start_date']:
            raise StorageConflictError('The initial occurrence does not match the schedule.')
        if mixed:
            source, prepared = self.mixed_service.prepare_pair(
                data, refs, source_id=current['id'], linked_transaction_id=projection['id'],
                created_at=current['created_at'], original_transaction=projection,
            )
            check_expected_snapshot(current, source)
            check_expected_snapshot(projection, prepared)
        else:
            prepared = self.transaction_service.prepare_source_projection(
                data, refs, transaction_id=current['id'], created_at=current['created_at'], original_transaction=current,
            )
            check_expected_snapshot(current, prepared)
        return self.initial_result(connection, identity, entry_id=current['id'], source_id=current['id'] if mixed else None)

    def convert_expense(self, identity, changes, *, expected=None, mixed=False):
        reject_existing_recurring_metadata(changes, 'Expense changes')
        with self.operation() as connection:
            schedule_keys = self.existing_schedule_keys(connection, changes.get('schedule_id'))
            if mixed:
                row, current, projection, refs = self.mixed.locked_pair(connection, identity, references=True, root_keys=schedule_keys)
                table = 'mixed_use_sources'
            else:
                refs = self.transactions.references(connection, lock=True, entry_ids=[identity], root_keys=schedule_keys)
                row = self.transactions.entry(connection, identity)
                current = self.transactions.project(connection, row)
                reject_derived_transaction(current)
                table = 'entries'
                projection = None
            if current['recurring_schedule_id']:
                return self.confirm_conversion(connection, current, projection, changes, refs, mixed=mixed)
            self.check_expected(row, table, expected, current)
            reject_existing_recurring_metadata(current, 'Expense')
            if current['kind'] != 'Expense':
                raise StorageValidationError('Only an existing Expense can be made recurring.')
            data = {**{key: value for key, value in current.items() if key != 'payee'}, **changes,
                    'source_id' if mixed else 'transaction_id': identity}
            schedule, source, prepared, refs = self.prepare_initial(connection, data, mixed=mixed, schedule_locked=bool(schedule_keys),
                                                                    current=current, projection=projection)
            if mixed:
                prepared = self.mixed.validate_pair(connection, source, prepared, refs, current=projection)
                self.mixed.write_pair(connection, source, prepared, recurring=True)
            else:
                self.transactions.write(connection, identity, prepared)
                write_occurrence(connection, self.entity_id, prepared, entry_id=identity)
            return self.initial_result(connection, schedule['id'], entry_id=identity, source_id=identity if mixed else None)
