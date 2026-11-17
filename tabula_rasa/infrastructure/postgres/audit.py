"""Private audit context and authorized read contract; no general export hook."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json

from sqlalchemy import text

from tabula_rasa.application.authorization import authorize_action


@dataclass(frozen=True)
class AuditContext:
    actor_type: str
    actor_id: str
    source: str
    entity_id: str = ''
    operation_id: str = ''  # Empty means a new identity for each committed UoW.
    correlation_id: str = ''
    batch_id: str = ''

    def __post_init__(self):
        if (any(not isinstance(v, str) or len(v) > 200 for v in asdict(self).values())
                or self.actor_type not in {'user', 'system', 'worker', 'operator'}
                or not self.actor_id.strip() or not self.source.strip()
                or self.actor_type in {'user', 'worker'} and not self.entity_id.strip()):
            raise ValueError('Invalid trusted audit context.')


INTERNAL_AUDIT = AuditContext('system', 'internal-application', 'internal')


@contextmanager
def audit_context(database, context):
    """Server composition only. Request bodies/headers cannot supply attribution.

    Changing context inside an existing transaction is forbidden: it could
    relabel earlier deferred events. One transaction has one trusted identity.
    """
    if not isinstance(context, AuditContext) or database._operation.get() is not None:
        raise ValueError('Audit context requires an independent operation.')
    token = database._audit_context.set(context)
    try:
        yield
    finally:
        database._audit_context.reset(token)


def configure_audit(connection, context, operation_id):
    value = {**asdict(context), 'operation_id': context.operation_id or operation_id}
    connection.execute(text("SELECT set_config('tabula.audit_context',:value,true)"),
                       {'value': json.dumps(value)})


# Key inventories match the audited canonical tables, never caller SQL names.
AUDITED_KEYS = {
    'customer_export_actions': ('id',),
    'support_actions': ('id',),
    'accounting_period_policy': ('entity_id',), 'accounting_periods': ('period_start',),
    'entry_corrections': ('id',), 'recurring_generation_policy': ('entity_id',),
    'accounting_period_actions': ('id',),
    **{name: ('id',) for name in ('accounts', 'categories', 'payees', 'tags', 'entries',
        'mixed_use_sources', 'recurring_schedules', 'fixed_assets', 'mileage_records',
        'documents', 'import_batches', 'migration_runs')},
    'postings': ('projection_kind', 'id'),
    'entry_tags': ('entry_id', 'tag_id'), 'mixed_use_tags': ('source_id', 'tag_id'),
    'schedule_tags': ('schedule_id', 'tag_id'), 'asset_tags': ('asset_id', 'tag_id'),
    'mileage_tags': ('mileage_id', 'tag_id'), 'document_links': ('document_id',),
    'recurring_occurrences': ('schedule_id', 'scheduled_date'),
    'asset_period_overrides': ('asset_id', 'sequence'), 'depreciation_events': ('asset_id', 'sequence'),
    'import_records': ('batch_id', 'source_position'),
    'migration_units': ('run_id', 'unit_key'),
    'migration_id_mappings': ('run_id', 'source_record_kind', 'source_id'),
}


def audited_keys(revision):
    """Known capture inventories for explicit archived-revision inspection.

    Missing current tables must still fail, never silently shrink coverage.
    This changes no startup/migration or normal write behavior.
    """
    if revision in {'0013_accounting_controls', '0014_document_locations'}:
        return {table: keys for table, keys in AUDITED_KEYS.items() if table not in {'support_actions', 'customer_export_actions'}}
    if revision in {'0015_support_controls', '0016_business_tenancy'}:
        return {table: keys for table, keys in AUDITED_KEYS.items() if table != 'customer_export_actions'}
    return {**AUDITED_KEYS, "vehicles": ("id",)} if revision in {"0024_mileage_vehicles", "0025_enrollment_drive_setup", "0026_customer_policies"} else AUDITED_KEYS


class AuditReader:
    """Privileged inspection and authorized export require server authorization.

    Capabilities come from trusted operator provisioning, never tenant input.
    Ordinary bookkeeping:read/export deliberately do not grant audit access.
    Every query is explicitly bound to one authorized entity, even for DB owners.
    """

    def __init__(self, database, entity_id, *, capabilities):
        authorize_action(capabilities, 'audit_inspect')
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError('An authorized entity is required.')
        self.database = database
        self.entity_id = entity_id

    @contextmanager
    def operation(self):
        with self.database.transaction(read_only=True) as connection:
            connection.execute(text("SELECT set_config('tabula.audit_reader_entity',:scope,true)"),
                               {'scope': self.entity_id})
            yield connection

    def query(self, *, since=None, until=None, organization_id=None, actor_id=None, action=None,
              source=None, target_type=None, target_id=None, batch_id=None, operation_id=None,
              after_sequence=0, through_sequence=None, limit=100):
        if not isinstance(limit, int) or not 1 <= limit <= 1000 or after_sequence < 0:
            raise ValueError('Invalid audit page.')
        if through_sequence is not None and (type(through_sequence) is not int or not 0 <= through_sequence <= 9223372036854775807):
            raise ValueError('Invalid audit snapshot boundary.')
        clauses = ['entity_id=:scope', 'sequence>:cursor']
        params = {'scope': self.entity_id, 'cursor': after_sequence, 'limit': limit}
        if through_sequence is not None:
            clauses.append('sequence<=:through')
            params['through'] = through_sequence
        for field, value in locals().copy().items():
            if field in {'organization_id', 'actor_id', 'action', 'source', 'target_type', 'batch_id', 'operation_id'} and value is not None:
                clauses.append(f'{field}=:{field}')
                params[field] = value
        if since is not None:
            clauses.append('occurred_at>=:since')
            params['since'] = since
        if until is not None:
            clauses.append('occurred_at<:until')
            params['until'] = until
        if target_id is not None:
            clauses.append('(' + ' OR '.join(f"target_key->>'{field}'=:target" for field in
                ('id', 'entry_id', 'document_id', 'source_id', 'schedule_id', 'asset_id', 'mileage_id', 'tag_id')) + ')')
            params['target'] = target_id
        with self.operation() as connection:
            return tuple(dict(row) for row in connection.execute(text(
                'SELECT * FROM audit_events WHERE ' + ' AND '.join(clauses) + ' ORDER BY sequence LIMIT :limit'
            ), params).mappings())

    def verify(self):
        """Targeted current-state ↔ last-event check with explicit legacy gaps.

        Runs in one read snapshot. No ledger contents leave this diagnostic.
        This detects missing/stale evidence, not malicious administrator tampering
        with both history and bookkeeping, and does not reconstruct lost PITR tails.
        """
        mismatches = 0
        historical = 0
        checked = 0
        with self.operation() as connection:
            from .migrations import current_revision
            activation = connection.scalar(text('SELECT activated_at FROM audit_activation'))
            for table, keys in audited_keys(current_revision(connection)).items():
                key_sql = ','.join(f"'{key}',to_jsonb(t)->'{key}'" for key in keys)
                current = {json.dumps(row['key'], sort_keys=True): row['digest'] for row in connection.execute(text(
                    f"SELECT jsonb_build_object({key_sql}) AS key, audit_row_digest(to_jsonb(t),'{table}') AS digest "
                    f'FROM {table} t WHERE entity_id=:scope'
                ), {'scope': self.entity_id}).mappings()}
                baseline = {json.dumps(row['target_key'], sort_keys=True): row['row_digest'] for row in connection.execute(text(
                    'SELECT target_key,row_digest FROM audit_baseline WHERE entity_id=:scope AND target_type=:table'
                ), {'scope': self.entity_id, 'table': table}).mappings()}
                events = {json.dumps(row['target_key'], sort_keys=True): row['after_digest'] for row in connection.execute(text(
                    'SELECT DISTINCT ON (target_key) target_key,after_digest FROM audit_events '
                    'WHERE entity_id=:scope AND target_type=:table ORDER BY target_key,sequence DESC'
                ), {'scope': self.entity_id, 'table': table}).mappings()}
                for key in current.keys() | baseline.keys() | events.keys():
                    checked += 1
                    expected = events.get(key) if key in events else baseline.get(key)
                    if key in baseline:
                        historical += 1
                    if expected != current.get(key) or key not in events and key not in baseline:
                        mismatches += 1
        return {'checked': checked, 'mismatches': mismatches, 'historical_unavailable': historical,
                'activated_at': activation, 'scope': 'current_state_only', 'status': 'matched' if not mismatches else 'mismatch'}

    def boundary(self, *, target_type=None, target_key=None):
        """An explicit history gap survives later edits, imports and restores."""
        clauses = ['entity_id=:scope']
        params = {'scope': self.entity_id}
        if target_type is not None:
            clauses.append('target_type=:table')
            params['table'] = target_type
        if target_key is not None:
            clauses.append('target_key @> CAST(:key AS jsonb)')
            params['key'] = json.dumps(target_key)
        with self.operation() as connection:
            activation = connection.scalar(text('SELECT activated_at FROM audit_activation'))
            count = connection.scalar(text('SELECT count(*) FROM audit_baseline WHERE ' + ' AND '.join(clauses)), params)
        return {'activated_at': activation, 'pre_activation_records': count,
                'history_status': 'pre_activation_unavailable' if count else 'since_activation_only'}

    def import_status(self, batch_ids):
        """Evidence for explicitly expected, independently committed chunks.

        Missing evidence means unconfirmed, never an invented failed mutation.
        The caller retains its chunk IDs; no file contents or implicit parent
        transaction are reconstructed from current bookkeeping records.
        """
        identities = tuple(dict.fromkeys(batch_ids))
        if not identities or len(identities) > 1000 or any(not isinstance(v, str) or not v.strip() for v in identities):
            raise ValueError('Supply expected stable import chunk identities.')
        with self.operation() as connection:
            evidence = {row['batch_id']: row['after_summary'] for row in connection.execute(text('''
                SELECT DISTINCT ON (batch_id) batch_id,after_summary FROM audit_events
                WHERE entity_id=:scope AND target_type='import_batches' AND action='create' AND batch_id=ANY(:ids)
                ORDER BY batch_id,sequence DESC'''), {'scope': self.entity_id, 'ids': list(identities)}).mappings()}
        complete = tuple(identity for identity in identities if (evidence.get(identity) or {}).get('status') == 'complete')
        missing = tuple(identity for identity in identities if identity not in complete)
        return {'status': 'complete' if not missing else 'partial' if complete else 'unconfirmed',
                'complete_chunks': complete, 'unconfirmed_chunks': missing,
                'committed_count': sum(evidence[i].get('committed_count', 0) for i in complete)}
