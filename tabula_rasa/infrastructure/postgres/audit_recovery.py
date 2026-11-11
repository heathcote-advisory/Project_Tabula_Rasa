"""Private audit recovery oracle, separate from customer bookkeeping exports.

Read-only, one committed snapshot per capture. Operators freeze source writers
while capturing the oracle and backup. Hashes detect accidental differences;
they do not establish provider lineage or resistance to a malicious DBA.
"""

import hashlib
import json
import re

from sqlalchemy import text

from tabula_rasa.application.entity_export import ExportError
from tabula_rasa.bookkeeping_export.format import build_artifact, canonical_json
from .audit import audited_keys, AuditReader
from .cutover import database_identity
from .entity_export import PostgresEntityExportRepository
from .migrations import MIGRATION_ROOT, current_revision

FORMAT = 'tabula-rasa-audit-recovery-v1'
SUPPORTED_REVISIONS = {'0013_accounting_controls', '0014_document_locations', '0015_support_controls', '0016_business_tenancy', '0017_customer_exports', '0018_drive_oauth', '0019_beta_admission', '0020_book_currency', '0021_customer_lifecycle', '0022_drive_recovery_binding'}
AUDIT_TABLES = ('audit_events', 'audit_baseline', 'audit_activation')
RECOVERY_FUNCTIONS = frozenset({'audit_immutable', 'audit_insert_guard', 'audit_summary',
                             'audit_row_digest', 'capture_bookkeeping_audit',
                             'capture_period_action', 'require_period_authority', 'guard_period_control',
                             'closed_accounting_date', 'guard_accounting_period', 'guard_entry_correction'})
PERIOD_GUARD_TABLES = ('entries', 'postings', 'entry_tags', 'mixed_use_sources', 'mixed_use_tags',
                       'depreciation_events', 'recurring_occurrences', 'fixed_assets', 'asset_tags',
                       'asset_period_overrides', 'accounts', 'categories')


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _rows(connection, query, params=None):
    return [dict(row) for row in connection.execute(text(query), params or {}).mappings()]


def _validate_function_sources(functions):
    """Use frozen migration SQL as the owner of required function definitions.

    All supported recovery revisions include 0013's minimizer replacement.
    This parses only our known declarations, not arbitrary SQL or restore input.
    It neither executes migrations nor opens a temporary database.
    """
    expected = {}
    declaration = re.compile(
        r'CREATE (?:OR REPLACE )?FUNCTION (\w+)\(([^)]*)\)\s+RETURNS (\w+)\s+'
        r'LANGUAGE (\w+)(.*?)\bAS \$\$(.*?)\$\$;', re.DOTALL | re.IGNORECASE,
    )
    for filename in ('0012_operations.sql', '0013_accounting_controls.sql'):
        for name, args, result_type, language, modifiers, body in declaration.findall(
                (MIGRATION_ROOT / 'versions' / filename).read_text(encoding='utf-8')):
            if name in RECOVERY_FUNCTIONS:
                expected[name] = {
                    'args': args, 'source': body.strip(), 'result_type': result_type, 'language': language,
                    'security_definer': 'SECURITY DEFINER' in modifiers.upper(),
                    'volatility': 'i' if 'IMMUTABLE' in modifiers.upper() else 'v',
                    'config': ['search_path=public, pg_temp'] if 'search_path' in modifiers else None,
                    'strict': False, 'leakproof': False,
                }
    if set(expected) != RECOVERY_FUNCTIONS:
        raise ExportError('audit_schema_unsupported')
    for name, definition in expected.items():
        actual = [row for row in functions if row['proname'] == name]
        if len(actual) != 1 or any(
            (actual[0][field].strip() if field == 'source' else actual[0][field]) != value
            for field, value in definition.items()
        ):
            raise ExportError('audit_schema_incomplete')


def _inventory(connection, revision):
    """Definitions, enabled protections, owners and ACLs; no ledger contents."""
    keys = audited_keys(revision)
    tables = (*AUDIT_TABLES, *keys)
    params = {'tables': list(tables)}
    inventory = {
        'tables': _rows(connection, """SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
            pg_get_userbyid(c.relowner) AS owner,
            ARRAY(SELECT a::text FROM unnest(COALESCE(c.relacl,acldefault('r',c.relowner))) a ORDER BY a::text)::text AS acl
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=ANY(:tables) ORDER BY c.relname""", params),
        'columns': _rows(connection, """SELECT c.relname,a.attname,a.attnum,
            format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,a.attidentity,
            pg_get_expr(d.adbin,d.adrelid) AS default_value
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relname=ANY(:tables) ORDER BY c.relname,a.attnum""", params),
        'constraints': _rows(connection, """SELECT c.relname,k.conname,k.convalidated,
            pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k
            JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=ANY(:tables) ORDER BY c.relname,k.conname""", params),
        'indexes': _rows(connection, """SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename=ANY(:tables) ORDER BY tablename,indexname""", params),
        'triggers': _rows(connection, """SELECT c.relname,t.tgname,t.tgenabled,t.tgtype,
            t.tgdeferrable,t.tginitdeferred,p.proname,pn.nspname AS function_schema,
            encode(t.tgargs,'hex') AS arguments,
            pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace
            WHERE n.nspname='public' AND c.relname=ANY(:tables) AND NOT t.tgisinternal
            ORDER BY c.relname,t.tgname""", params),
        'policies': _rows(connection, """SELECT tablename,policyname,permissive,roles::text,
            cmd,qual,with_check FROM pg_policies WHERE schemaname='public'
            AND tablename=ANY(:tables) ORDER BY tablename,policyname""", params),
        # Include dependencies of audit capture/period gates, not just names with
        # an audit prefix. Function ACL/owner/search_path are part of the oracle.
        'functions': _rows(connection, """SELECT p.proname,pg_get_function_identity_arguments(p.oid) AS args,
            pg_get_functiondef(p.oid) AS definition,pg_get_userbyid(p.proowner) AS owner,
            p.prosrc AS source,format_type(p.prorettype,NULL) AS result_type,l.lanname AS language,
            p.prosecdef AS security_definer,p.provolatile AS volatility,p.proconfig AS config,
            p.proisstrict AS strict,p.proleakproof AS leakproof,
            ARRAY(SELECT a::text FROM unnest(COALESCE(p.proacl,acldefault('f',p.proowner))) a ORDER BY a::text)::text AS acl
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_language l ON l.oid=p.prolang
            WHERE n.nspname='public' AND p.prokind IN ('f','p','w') ORDER BY p.proname,args"""),
        'sequences': _rows(connection, """SELECT s.schemaname,s.sequencename,s.sequenceowner,s.data_type::text,
            s.start_value,s.min_value,s.max_value,s.increment_by,s.cycle,s.cache_size,
            ARRAY(SELECT a::text FROM unnest(COALESCE(c.relacl,acldefault('s',c.relowner))) a ORDER BY a::text)::text AS acl
            FROM pg_sequences s JOIN pg_namespace n ON n.nspname=s.schemaname
            JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=s.sequencename
            WHERE s.schemaname='public' AND s.sequencename='audit_events_sequence_seq'"""),
    }
    _validate_function_sources(inventory['functions'])
    triggers = {(r['relname'], r['tgname']): r for r in inventory['triggers']}
    # Validate supported capture/immutability semantics before trusting a source
    # oracle; a drifted trigger with the right name is not a valid protection.
    required = {(table, 'audit_capture'): (
        5 if table in {'support_actions', 'customer_export_actions'} else 29, 'capture_bookkeeping_audit',
        True, True, ('\0'.join(fields)+'\0').encode().hex(),
    ) for table, fields in keys.items()}
    required.update({
        ('audit_events', 'immutable'): (27, 'audit_immutable', False, False, ''),
        ('audit_events', 'immutable_truncate'): (34, 'audit_immutable', False, False, ''),
        ('audit_events', 'capture_only'): (7, 'audit_insert_guard', False, False, ''),
        ('audit_baseline', 'immutable'): (27, 'audit_immutable', False, False, ''),
        ('audit_baseline', 'immutable_truncate'): (34, 'audit_immutable', False, False, ''),
        ('audit_baseline', 'immutable_insert'): (7, 'audit_immutable', False, False, ''),
        ('audit_activation', 'immutable'): (58, 'audit_immutable', False, False, ''),
        ('accounting_period_actions', 'immutable'): (27, 'audit_immutable', False, False, ''),
        ('accounting_period_actions', 'immutable_truncate'): (34, 'audit_immutable', False, False, ''),
        ('accounting_period_actions', 'capture_only'): (7, 'audit_insert_guard', False, False, ''),
        ('entry_corrections', 'immutable'): (27, 'audit_immutable', False, False, ''),
        ('entry_corrections', 'correction_authority'): (7, 'guard_entry_correction', False, False, ''),
    })
    required.update({(table, 'accounting_period_guard'):
        (31, 'guard_accounting_period', False, False, '') for table in PERIOD_GUARD_TABLES})
    for table in ('accounting_period_policy', 'accounting_periods'):
        required[(table, 'period_control')] = (31, 'guard_period_control', False, False, '')
        required[(table, 'action_history')] = (21, 'capture_period_action', False, False, '')
    for table in ('accounting_period_policy', 'accounting_periods', 'entry_corrections'):
        required[(table, 'immutable_truncate')] = (34, 'audit_immutable', False, False, '')
    if revision in {'0015_support_controls', '0016_business_tenancy', '0017_customer_exports', '0018_drive_oauth', '0019_beta_admission', '0020_book_currency', '0021_customer_lifecycle', '0022_drive_recovery_binding'}:
        required[('support_actions', 'immutable')] = (58, 'audit_immutable', False, False, '')
    if revision in {'0017_customer_exports', '0018_drive_oauth', '0019_beta_admission', '0020_book_currency', '0021_customer_lifecycle', '0022_drive_recovery_binding'}:
        required[('customer_export_actions', 'immutable')] = (58, 'audit_immutable', False, False, '')
    valid_triggers = all(
        key in triggers and triggers[key]['tgenabled'] == 'O' and triggers[key]['function_schema'] == 'public'
        and tuple(triggers[key][field] for field in ('tgtype','proname','tgdeferrable','tginitdeferred','arguments')) == expected
        for key, expected in required.items()
    )
    scoped_tables = {'audit_events', 'audit_baseline', 'accounting_period_actions'}
    expected_policies = [{
        'tablename': table, 'policyname': name, 'permissive': 'PERMISSIVE', 'roles': '{public}', 'cmd': 'SELECT',
        'qual': "(entity_id = current_setting('tabula.audit_reader_entity'::text, true))", 'with_check': None,
    } for table, name in [('accounting_period_actions','scoped_period_action_read'),
                         ('audit_baseline','scoped_baseline_read'), ('audit_events','scoped_audit_read')]]
    if revision in {'0015_support_controls', '0016_business_tenancy', '0017_customer_exports', '0018_drive_oauth', '0019_beta_admission', '0020_book_currency', '0021_customer_lifecycle', '0022_drive_recovery_binding'}:
        scoped_tables.add('support_actions')
        context = "(NULLIF(current_setting('tabula.audit_context'::text, true), ''::text))::jsonb"
        expected_policies.extend([
            {'tablename': 'support_actions', 'policyname': 'scoped_support_insert',
             'permissive': 'PERMISSIVE', 'roles': '{public}', 'cmd': 'INSERT', 'qual': None,
             'with_check': f"((entity_id = ({context} ->> 'entity_id'::text)) "
                 f"AND (({context} ->> 'actor_type'::text) = 'operator'::text) "
                 f"AND (({context} ->> 'source'::text) = 'support'::text) "
                 "AND (operation = ANY (ARRAY['diagnostics'::text, 'audit'::text, 'preview'::text, "
                 "'revoke_sessions'::text, 'suspend_member'::text, 'suspend_organization'::text])))"},
            {'tablename': 'support_actions', 'policyname': 'scoped_support_read',
             'permissive': 'PERMISSIVE', 'roles': '{public}', 'cmd': 'SELECT',
             'qual': "(entity_id = current_setting('tabula.audit_reader_entity'::text, true))", 'with_check': None},
        ])
    scoped_policies = [row for row in inventory['policies'] if row['tablename'] in scoped_tables]
    secured = {r['relname'] for r in inventory['tables'] if r['relrowsecurity']}
    if (not valid_triggers or not scoped_tables <= secured
            or scoped_policies != expected_policies
            or len(inventory['tables']) != len(tables) or len(inventory['sequences']) != 1):
        raise ExportError('audit_schema_incomplete')
    return inventory


def _history(connection, table, entity_id):
    # Server cursors avoid retaining the history in application memory. Full
    # ordered rows include stable IDs, attribution, summaries and ordering keys.
    order = 'sequence' if table == 'audit_events' else 'target_type,target_key'
    result = connection.execution_options(stream_results=True).execute(text(
        f'SELECT to_jsonb(t)::text AS value FROM {table} t WHERE entity_id=:scope ORDER BY {order}'
    ), {'scope': entity_id})
    digest = hashlib.sha256()
    count = 0
    for row in result:
        # PostgreSQL's canonical jsonb text retains exact numeric precision;
        # decoding JSON through Python floats could hide financial differences.
        digest.update((row.value + '\n').encode('utf-8'))
        count += 1
    result.close()
    connection.execution_options(stream_results=False)
    return {'count': count, 'sha256': digest.hexdigest()}


def capture_audit_recovery(database, entity_id, *, capabilities):
    """Capture private evidence; never export events or treat it as a backup."""
    reader = AuditReader(database, entity_id, capabilities=capabilities)
    with database.transaction(read_only=True, require_outer=True) as connection:
        revision = current_revision(connection)
        if revision not in SUPPORTED_REVISIONS:
            raise ExportError('audit_history_unavailable' if revision in {
                '0001_initial', '0004_mileage_order', '0005_bookkeeping_cutover', '0006_identity_control_plane',
                '0007_identity_sessions', '0008_owner_lock_scope', '0009_external_connections',
                '0010_onboarding_routes', '0010_workspace_lifecycle', '0011_control_plane_join',
            } else 'audit_schema_unsupported')
        inventory = _inventory(connection, revision)
        with reader.operation():
            integrity = reader.verify()
            if integrity['status'] != 'matched':
                raise ExportError('audit_ledger_mismatch')
            activation = _rows(connection, 'SELECT singleton,activated_at::text FROM audit_activation')
            if len(activation) != 1 or activation[0]['singleton'] is not True:
                raise ExportError('audit_activation_missing')
            sequence = dict(connection.execute(text(
                'SELECT last_value,is_called FROM audit_events_sequence_seq'
            )).mappings().one())
            maximum = connection.scalar(text('SELECT COALESCE(max(sequence),0) FROM audit_events'))
            if sequence['last_value'] < maximum or (maximum and not sequence['is_called']):
                raise ExportError('audit_sequence_invalid')
            # Read canonical bookkeeping in this same snapshot. Period/correction
            # records outside v1 export are included in the audit integrity scan.
            ledger = build_artifact(PostgresEntityExportRepository(database, entity_id).read_snapshot(connection))
            state = {
                'database_schema_version': revision, 'inventory_sha256': _digest(inventory),
                'activation': activation[0]['activated_at'], 'sequence': sequence,
                'events': _history(connection, 'audit_events', entity_id),
                'baseline': _history(connection, 'audit_baseline', entity_id),
                'ledger_sha256': ledger['manifest']['content_sha256'],
                'historical_unavailable': integrity['historical_unavailable'],
            }
    body = {'format': FORMAT, 'entity_id': entity_id, 'source_database_sha256': database_identity(database), 'state': state}
    return {**body, 'sha256': _digest(body)}


def validate_audit_manifest(value, entity_id):
    """All errors are fixed codes, never manifest values or provider messages."""
    if not isinstance(value, dict) or set(value) != {'format', 'entity_id', 'source_database_sha256', 'state', 'sha256'}:
        raise ExportError('audit_manifest_invalid')
    if value['format'] != FORMAT or value['entity_id'] != entity_id:
        raise ExportError('audit_manifest_scope')
    if not isinstance(value['source_database_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['source_database_sha256']):
        raise ExportError('audit_manifest_invalid')
    try:
        valid = _digest({k: v for k, v in value.items() if k != 'sha256'}) == value['sha256']
    except (TypeError, ValueError, UnicodeError):
        valid = False
    if not valid or not isinstance(value['state'], dict):
        raise ExportError('audit_manifest_invalid')
    return value


def read_audit_manifest(path, entity_id):
    try:
        with open(path, encoding='utf-8') as stream:
            value = json.load(stream)
    except (OSError, ValueError, UnicodeError):
        value = None
    return validate_audit_manifest(value, entity_id)


def verify_audit_recovery(database, entity_id, expected, *, source_database_sha256,
                          target_database_sha256, capabilities, later=None):
    """Reject partial history even when latest-event/current-state checks pass."""
    validate_audit_manifest(expected, entity_id)
    if (any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v)
            for v in (source_database_sha256, target_database_sha256))
            or expected['source_database_sha256'] != source_database_sha256
            or source_database_sha256 == target_database_sha256
            or database_identity(database) != target_database_sha256):
        raise ExportError('recovery_target')
    if later is not None:
        validate_audit_manifest(later, entity_id)
        if (later['source_database_sha256'] != source_database_sha256
                or any(later['state'].get(key) != expected['state'].get(key)
                       for key in ('database_schema_version','inventory_sha256','activation','baseline'))
                or later['state']['events']['count'] <= expected['state']['events']['count']
                or later['state']['sequence']['last_value'] < expected['state']['sequence']['last_value']):
            raise ExportError('audit_pitr_control_unchanged')
    restored = capture_audit_recovery(database, entity_id, capabilities=capabilities)
    # Sequences are nontransactional: failed writes can consume values, and a
    # logical dump may advance cached state. Gaps are valid; regression is not.
    actual, wanted = restored['state'], expected['state']
    if (any(actual.get(key) != wanted.get(key) for key in actual if key != 'sequence')
            or set(actual) != set(wanted)
            or actual['sequence']['last_value'] < wanted['sequence']['last_value']
            or wanted['sequence']['is_called'] and not actual['sequence']['is_called']):
        raise ExportError('audit_recovery_mismatch')
    return {'status': 'matched', 'scope': 'entity_bookkeeping_and_audit',
            'database_schema_version': restored['state']['database_schema_version'],
            'event_count': restored['state']['events']['count'],
            'baseline_count': restored['state']['baseline']['count'],
            'history_status': 'pre_activation_unavailable' if restored['state']['baseline']['count'] else 'since_activation_only',
            'later_state_distinct': later is not None}
