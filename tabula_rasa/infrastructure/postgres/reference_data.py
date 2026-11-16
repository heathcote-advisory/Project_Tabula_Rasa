"""Reference-data ports on canonical, entity-qualified PostgreSQL records."""

from collections.abc import Mapping
from uuid import uuid4

from sqlalchemy import text

from tabula_rasa.errors import StorageConflictError, StorageValidationError
from tabula_rasa.reference_data import (
    ACCOUNT_TYPES, as_bool, balance_amount, clean_text, normalize_merge_ids,
    normalize_payee_name, normalize_tag_name, validate_account_classification,
    validate_hierarchy_records,
    vehicle_name,
)
from .repository import EntityRepository, insert, new_object, record, replace
from .concurrency import lock_aggregates


ACCOUNT_FIELDS = ('id', 'name', 'type', 'classification', 'opening_balance', 'active', 'created_at', 'parent_id')
CATEGORY_FIELDS = ('id', 'name', 'kind', 'parent_id')
MANAGED_FIELDS = ('id', 'name', 'active', 'created_at')


def reference_record(row, table):
    fields = ACCOUNT_FIELDS if table == 'accounts' else CATEGORY_FIELDS if table == 'categories' else MANAGED_FIELDS
    result = record(row, table, fields)
    if table in {'payees', 'tags', 'vehicles'}:
        result['active'] = 'TRUE' if row['active'] else 'FALSE'
    return result


class PostgresLedgerSetupRepository(EntityRepository):
    @classmethod
    def create_entity(cls, database, entity_id, name, *, currency="USD"):
        if currency not in {"USD", "CAD"}:
            raise StorageValidationError("Use USD or CAD for a new book.")
        repository = cls(database, entity_id)
        with repository.operation() as connection:
            insert(connection, 'business_entities', {
                'id': repository.entity_id, 'name': clean_text(name, 'Ledger name', required=True),
                'incarnation_id': str(uuid4()), 'currency_code': currency,
            })
        return repository

    @property
    def ledger_name(self):
        with self.operation(read_only=True) as connection:
            row = connection.execute(text('SELECT name FROM business_entities WHERE id = :scope'),
                                     {'scope': self.entity_id}).first()
            if row is None:
                from tabula_rasa.errors import StorageNotFoundError
                raise StorageNotFoundError('The selected business entity does not exist.')
            return row.name

    @property
    def ledger_currency(self):
        with self.operation(read_only=True) as connection:
            currency = connection.scalar(text('SELECT currency_code FROM business_entities WHERE id=:scope'),
                                         {'scope': self.entity_id})
            if currency is None:
                from tabula_rasa.errors import StorageNotFoundError
                raise StorageNotFoundError('The selected business entity does not exist.')
            return currency

    def ensure_defaults(self):
        from tabula_rasa.ledger_defaults import DEFAULT_ACCOUNTS, DEFAULT_CATEGORIES
        accounts = PostgresAccountRepository(self.database, self.entity_id)
        categories = PostgresCategoryRepository(self.database, self.entity_id)
        counts = {'accounts': 0, 'categories': 0}
        with self.operation() as connection:
            self.lock_entity(connection)
            if accounts.list(active_only=False) or categories.list():
                return counts
            for name, account_type in DEFAULT_ACCOUNTS:
                accounts.create(name, account_type, '0.00', classification='Asset', parent_id='')
                counts['accounts'] += 1
            for kind, names in DEFAULT_CATEGORIES.items():
                for name in names:
                    categories.create(name, kind, parent_id='')
                    counts['categories'] += 1
        return counts


class PostgresAccountRepository(EntityRepository):
    def list(self, *, active_only=True):
        with self.operation(read_only=True) as connection:
            return sorted(
                [reference_record(row, 'accounts') for row in self.rows(connection, 'accounts')
                 if not active_only or row['active']], key=lambda row: row['name'].casefold(),
            )

    @staticmethod
    def values(data):
        name = clean_text(data['name'], 'Account name', required=True)
        account_type = clean_text(data['type'], 'Account type', required=True)
        if account_type not in ACCOUNT_TYPES:
            raise StorageValidationError('Unknown account type')
        classification = clean_text(data['classification'], 'Classification', required=True).title()
        validate_account_classification(account_type, classification)
        return {'name': name, 'name_key': name.casefold(), 'type': account_type,
                'classification': classification, 'opening_balance': balance_amount(data['opening_balance']),
                'active': as_bool(data['active']), 'parent_id': clean_text(data.get('parent_id'), 'Parent') or None}

    def create(self, name, account_type, opening_balance, *, classification, parent_id):
        values = self.values(dict(name=name, type=account_type, opening_balance=opening_balance,
                                 classification=classification, parent_id=parent_id, active=True))
        with self.operation() as connection:
            self.lock_entity(connection)
            created = new_object(self.entity_id, **values)
            validate_hierarchy_records([*self.rows(connection, 'accounts'), created],
                                       compatibility_field='classification', item_label='Account')
            return reference_record(insert(connection, 'accounts', created), 'accounts')

    def update(self, account_id, *, changes, expected=None):
        if set(changes) - set(ACCOUNT_FIELDS[1:]) or 'created_at' in changes:
            raise StorageValidationError('Unknown account update field')
        with self.operation() as connection:
            self.lock_entity(connection)
            current = self.get_row(connection, 'accounts', account_id, lock=True)
            self.check_expected(current, 'accounts', expected, reference_record(current, 'accounts'),
                                required_fields=ACCOUNT_FIELDS if expected is not None else ())
            values = self.values({**current, **changes})
            if (values['type'], values['classification']) != ('Other', 'Asset') and connection.execute(text(
                'SELECT 1 FROM fixed_assets WHERE entity_id=:scope AND accumulated_depreciation_account_id=:id LIMIT 1'
            ), {'scope': self.entity_id, 'id': account_id}).first():
                raise StorageConflictError('An accumulated depreciation account must remain Other / Asset.')
            validate_hierarchy_records(
                [{**row, **values} if row['id'] == account_id else row for row in self.rows(connection, 'accounts')],
                compatibility_field='classification', item_label='Account',
            )
            return reference_record(replace(connection, 'accounts', self.entity_id, account_id, values), 'accounts')


class PostgresCategoryRepository(EntityRepository):
    def list(self, kind=None):
        if kind is not None and kind not in {'Income', 'Expense'}:
            raise StorageValidationError('Unknown category kind')
        with self.operation(read_only=True) as connection:
            return sorted([reference_record(row, 'categories') for row in self.rows(connection, 'categories')
                           if kind is None or row['kind'] == kind], key=lambda row: (row['kind'], row['name'].casefold()))

    @staticmethod
    def values(data):
        name = clean_text(data['name'], 'Category name', required=True)
        kind = clean_text(data['kind'], 'Category kind', required=True).title()
        if kind not in {'Income', 'Expense'}:
            raise StorageValidationError('Category kind must be Income or Expense')
        return {'name': name, 'name_key': name.casefold(), 'kind': kind,
                'parent_id': clean_text(data.get('parent_id'), 'Parent') or None}

    def create(self, name, kind, *, parent_id):
        values = self.values(dict(name=name, kind=kind, parent_id=parent_id))
        with self.operation() as connection:
            self.lock_entity(connection)
            created = new_object(self.entity_id, **values)
            validate_hierarchy_records([*self.rows(connection, 'categories'), created],
                                       compatibility_field='kind', item_label='Category')
            return reference_record(insert(connection, 'categories', created), 'categories')

    def update(self, category_id, *, changes, expected=None):
        if set(changes) - {'name', 'kind', 'parent_id'}:
            raise StorageValidationError('Unknown category update field')
        with self.operation() as connection:
            self.lock_entity(connection)
            current = self.get_row(connection, 'categories', category_id, lock=True)
            self.check_expected(current, 'categories', expected, reference_record(current, 'categories'),
                                required_fields=CATEGORY_FIELDS if expected is not None else ())
            values = self.values({**current, **changes})
            if values['kind'] != current['kind']:
                for table, column in [('postings', 'category_id'), ('mixed_use_sources', 'category_id'),
                                      ('recurring_schedules', 'category_id'), ('fixed_assets', 'expense_category_id')]:
                    if connection.execute(text(
                        f'SELECT 1 FROM {table} WHERE entity_id=:scope AND {column}=:id LIMIT 1'
                    ), {'scope': self.entity_id, 'id': category_id}).first():
                        raise StorageConflictError('A referenced category cannot change kind.')
            validate_hierarchy_records(
                [{**row, **values} if row['id'] == category_id else row for row in self.rows(connection, 'categories')],
                compatibility_field='kind', item_label='Category',
            )
            return reference_record(replace(connection, 'categories', self.entity_id, category_id, values), 'categories')

    def delete(self, category_id, *, expected=None):
        with self.operation() as connection:
            self.lock_entity(connection)
            current = self.get_row(connection, 'categories', category_id, lock=True)
            self.check_expected(current, 'categories', expected, reference_record(current, 'categories'),
                                required_fields=CATEGORY_FIELDS if expected is not None else ())
            self.delete_row(connection, 'categories', category_id)


# All canonical references participate, even before their service slice exists.
PAYEE_OWNERS = ('entries', 'mixed_use_sources', 'recurring_schedules', 'fixed_assets')
TAG_OWNERS = (
    ('entry_tags', 'entry_id', 'entries'), ('mixed_use_tags', 'source_id', 'mixed_use_sources'),
    ('schedule_tags', 'schedule_id', 'recurring_schedules'), ('asset_tags', 'asset_id', 'fixed_assets'),
    ('mileage_tags', 'mileage_id', 'mileage_records'),
)


class _ManagedRepository(EntityRepository):
    table = ''
    normalize = staticmethod(normalize_payee_name)

    @staticmethod
    def clean_name(value):
        return clean_text(value, 'Reference name', required=True)

    def list(self, *, active_only=False):
        with self.operation(read_only=True) as connection:
            return sorted([reference_record(row, self.table) for row in self.rows(connection, self.table)
                           if not active_only or row['active']], key=lambda row: row['name'].casefold())

    def create(self, name, *, active=True):
        name = self.clean_name(name)
        active = as_bool(active)
        values = new_object(self.entity_id, name=name, name_key=self.normalize(name), active=active)
        with self.operation() as connection:
            # ON CONFLICT protects concurrent name creation; reuse/reactivate keeps
            # the existing identity, name and creation instant.
            connection.execute(text(
                f'INSERT INTO {self.table}(entity_id,id,incarnation_id,name,name_key,active) '
                'VALUES (:entity_id,:id,:incarnation_id,:name,:name_key,:active) '
                'ON CONFLICT (entity_id,name_key) DO NOTHING'
            ), values)
            row = dict(connection.execute(text(
                f'SELECT * FROM {self.table} WHERE entity_id=:scope AND name_key=:key FOR UPDATE'
            ), {'scope': self.entity_id, 'key': values['name_key']}).mappings().one())
            if active and not row['active']:
                row = replace(connection, self.table, self.entity_id, row['id'], {'active': True})
            return reference_record(row, self.table)

    def update(self, identity, *, changes, expected=None):
        if set(changes) - {'name', 'active'}:
            raise StorageValidationError('Unknown reference update field')
        with self.operation() as connection:
            current = self.get_row(connection, self.table, identity, lock=True)
            self.check_expected(current, self.table, expected, reference_record(current, self.table))
            name = self.clean_name(changes.get('name', current['name']))
            values = {'name': name, 'name_key': self.normalize(name), 'active': as_bool(changes.get('active', current['active']))}
            return reference_record(replace(connection, self.table, self.entity_id, identity, values), self.table)

    def remove(self, identity, *, expected=None):
        with self.operation() as connection:
            current = self.get_row(connection, self.table, identity, lock=True)
            self.check_expected(current, self.table, expected, reference_record(current, self.table))
            # Entity-qualified NO ACTION FKs refuse every remaining association.
            self.delete_row(connection, self.table, identity)
        return {'status': 'fully_applied'}

    def merge(self, target_id, source_ids, *, expected=None):
        target_id, source_ids = normalize_merge_ids(target_id, source_ids, 'reference')
        expected_by_id = {}
        if expected is not None:
            values = [(expected.get('id'), expected)] if isinstance(expected, Mapping) and 'id' in expected else (
                expected.items() if isinstance(expected, Mapping) else ((None, item) for item in expected)
            )
            for mapping_id, item in values:
                identity = clean_text(item.get('id', mapping_id), 'Loaded reference ID', required=True)
                if mapping_id is not None and str(mapping_id) != identity:
                    raise StorageValidationError('Loaded reference identities do not match.')
                expected_by_id[identity] = item
        with self.operation() as connection:
            self.lock_entity(connection, key_share=True)
            owners = self.merge_owners(connection, source_ids)
            keys = {self.key(self.table, identity) for identity in {target_id, *source_ids, *expected_by_id}}
            locked = lock_aggregates(connection, keys | owners)
            # A concurrent create may have committed while these locks waited.
            # Stop rather than discover an earlier-order root after locking.
            if self.merge_owners(connection, source_ids) - owners:
                raise StorageConflictError('References changed during the merge. Reload before trying again.')
            for identity in sorted({target_id, *source_ids, *expected_by_id}):
                current = locked[self.key(self.table, identity)]
                self.check_expected(current, self.table, expected_by_id.get(identity), reference_record(current, self.table))
                if identity == target_id and not current['active']:
                    raise StorageValidationError('The merge target must be active.')
            if self.table == 'payees':
                self._merge_payee(connection, source_ids, target_id)
            else:
                self._merge_tag(connection, source_ids, target_id)
            for source_id in source_ids:
                self.delete_row(connection, self.table, source_id)
        return {'status': 'fully_applied', 'count': str(len(source_ids))}

    def merge_owners(self, connection, source_ids):
        params = {'scope': self.entity_id, 'sources': source_ids}
        keys = set()
        if self.table == 'payees':
            for table in PAYEE_OWNERS:
                extra = (' OR id IN (SELECT entry_id FROM postings WHERE entity_id=:scope AND payee_id=ANY(:sources))'
                         if table == 'entries' else '')
                ids = connection.execute(text(
                    f'SELECT id FROM {table} WHERE entity_id=:scope AND (payee_id=ANY(:sources){extra})'
                ), params).scalars()
                keys.update(self.key(table, identity) for identity in ids)
        else:
            for links, column, owner in TAG_OWNERS:
                ids = connection.execute(text(
                    f'SELECT {column} FROM {links} WHERE entity_id=:scope AND tag_id=ANY(:sources)'
                ), params).scalars()
                keys.update(self.key(owner, identity) for identity in ids)
        entry_ids = [key.record_id for key in keys if key.kind == 'entry']
        if entry_ids:
            for row in connection.execute(text(
                'SELECT mixed_use_source_id,depreciation_asset_id FROM entries WHERE entity_id=:scope AND id=ANY(:ids)'
            ), {'scope': self.entity_id, 'ids': entry_ids}).mappings():
                if row['mixed_use_source_id']:
                    keys.add(self.key('mixed_use_sources', row['mixed_use_source_id']))
                if row['depreciation_asset_id']:
                    keys.add(self.key('fixed_assets', row['depreciation_asset_id']))
        return keys

    def _merge_payee(self, connection, source_ids, target_id):
        params = {'scope': self.entity_id, 'sources': source_ids, 'target': target_id}
        # Lock affected aggregates in a fixed table/identity order before detail
        # writes; their baselines advance when persisted references change.
        for table in PAYEE_OWNERS:
            extra = (' OR id IN (SELECT entry_id FROM postings WHERE entity_id=:scope AND payee_id=ANY(:sources))'
                     if table == 'entries' else '')
            ids = connection.execute(text(
                f'SELECT id FROM {table} WHERE entity_id=:scope AND (payee_id=ANY(:sources){extra}) ORDER BY id FOR UPDATE'
            ), params).scalars().all()
            for identity in ids:
                connection.execute(text(
                    f'UPDATE {table} SET payee_id=CASE WHEN payee_id=ANY(:sources) THEN :target ELSE payee_id END '
                    'WHERE entity_id=:scope AND id=:id'
                ), {**params, 'id': identity})
        connection.execute(text('UPDATE postings SET payee_id=:target WHERE entity_id=:scope AND payee_id=ANY(:sources)'), params)

    def _merge_tag(self, connection, source_ids, target_id):
        params = {'scope': self.entity_id, 'sources': source_ids, 'target': target_id}
        for links, column, owner in TAG_OWNERS:
            connection.execute(text(
                f'INSERT INTO {links}(entity_id,{column},tag_id) '
                f'SELECT DISTINCT entity_id,{column},:target FROM {links} WHERE entity_id=:scope AND tag_id=ANY(:sources) ON CONFLICT DO NOTHING'
            ), params)
            connection.execute(text(f'DELETE FROM {links} WHERE entity_id=:scope AND tag_id=ANY(:sources)'), params)


class PostgresPayeeRepository(_ManagedRepository):
    table = 'payees'


class PostgresTagRepository(_ManagedRepository):
    table = 'tags'
    normalize = staticmethod(normalize_tag_name)


class PostgresVehicleRepository(_ManagedRepository):
    """Exact names preserve distinguishable historical vehicle labels."""
    table = 'vehicles'
    normalize = staticmethod(str)
    clean_name = staticmethod(vehicle_name)


def ensure_vehicle_for_label(connection, entity_id, label):
    """Map an accepted source label exactly; never reactivate an inactive vehicle."""
    if not label.strip():
        return None
    values = new_object(entity_id, name=label, name_key=label)
    connection.execute(text('''INSERT INTO vehicles(entity_id,id,incarnation_id,name,name_key)
        VALUES (:entity_id,:id,:incarnation_id,:name,:name_key)
        ON CONFLICT (entity_id,name_key) DO NOTHING'''), values)
    return connection.execute(text('SELECT * FROM vehicles WHERE entity_id=:scope AND name_key=:name FOR UPDATE'),
        {'scope': entity_id, 'name': label}).mappings().one()
