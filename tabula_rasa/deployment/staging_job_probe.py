"""One explicitly approved staging ledger-read probe; never a scheduler."""
import argparse
import json
import os
import re
from datetime import datetime, timezone

import requests
from sqlalchemy import text

from tabula_rasa.deployment import release
from tabula_rasa.deployment.smoke import access_mode
from tabula_rasa.deployment.support_maintenance import SERVICES
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.hosted import open_alpha_database
from tabula_rasa.infrastructure.postgres.job_cli import operator_registry
from tabula_rasa.infrastructure.postgres.jobs import JobQueue
from tabula_rasa.infrastructure.postgres.migrations import head_revision
from tabula_rasa.telemetry import Telemetry, TelemetrySettings, configure_console


# This one-off probe is immediately eligible regardless of runner/database clock skew.
PROBE_DUE_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)


def validate(environ, *, runtime=True):
    service, origin = SERVICES['staging']
    if (environ.get('GITHUB_REPOSITORY') != 'heathcote-advisory/Project_Tabula_Rasa'
            or environ.get('GITHUB_REF') != 'refs/heads/main'
            or environ.get('TABULA_RASA_ENVIRONMENT') != 'staging'
            or environ.get('RENDER_SERVICE_ID') != service
            or environ.get('TABULA_RASA_PUBLIC_ORIGIN') != origin
            or not re.fullmatch('[0-9a-f]{40}', environ.get('GITHUB_SHA', ''))
            or environ.get('TABULA_RASA_RELEASE_ID') != environ['GITHUB_SHA']
            or not re.fullmatch('[0-9a-f]{32}', environ.get('STAGING_JOB_PROBE_ENTITY_ID', ''))
            or not re.fullmatch('[1-9][0-9]{0,19}', environ.get('GITHUB_RUN_ID', ''))
            or access_mode(environ, require_alpha_credentials=False) != 'named_user'):
        raise ValueError('Staging probe scope mismatch.')
    operation = environ.get('JOB_PROBE_OPERATION')
    if operation not in {'inspect', 'probe'} or (operation == 'probe' and
            environ.get('JOB_PROBE_APPROVAL') != 'one_probe_no_other_job_operators'):
        raise ValueError('Explicit isolated probe approval required.')
    if runtime:
        settings = DatabaseSettings.from_environment(environ, required=True)
        if settings.url.username != 'tabula_staging_runtime':
            raise ValueError('Use only the existing staging runtime role, never migration credentials.')
    return operation


def preflight(environ):
    validate(environ, runtime=False)
    repository = release.commercial_image_repository(environ['GITHUB_REPOSITORY'])
    candidate = release.image_metadata(environ.get('IMAGE', ''), repository)
    if candidate['release'] != environ['GITHUB_SHA'] or candidate['schema'] != head_revision():
        raise ValueError('Use this exact workflow revision image.')
    release.validated_main(candidate['release'], environ)
    deployed_release(environ)
    return {'status': 'tested_deployed_revision_verified', 'release': candidate['release']}


def deployed_release(environ):
    with requests.Session() as session:
        session.trust_env = False
        for path, expected in (('/version', {'release': environ['GITHUB_SHA']}),
                               ('/health/ready', {'status': 'ready'})):
            with session.get(environ['TABULA_RASA_PUBLIC_ORIGIN'] + path,
                             timeout=(5, 30), allow_redirects=False) as response:
                if response.status_code != 200 or response.json() != expected:
                    raise ValueError('Deploy and verify this exact tested revision before the probe.')


def inspect_queue(database, entity, key):
    """Only queue metadata; no ledger rows, selectors or credentials in output."""
    with database.transaction(read_only=True) as connection:
        if not connection.scalar(text('SELECT EXISTS (SELECT 1 FROM business_entities WHERE id=:scope)'),
                                 {'scope': entity}):
            raise ValueError('Approved staging probe entity does not exist.')
        definitions = connection.scalar(text('SELECT count(*) FROM job_definitions WHERE entity_id=:scope'),
                                        {'scope': entity})
        others = connection.scalar(text("""SELECT count(*) FROM jobs WHERE entity_id=:scope
            AND state IN ('queued','running') AND idempotency_key<>:key"""), {'scope': entity, 'key': key})
        existing = connection.execute(text("""SELECT id,job_type,state FROM jobs
            WHERE entity_id=:scope AND idempotency_key=:key"""), {'scope': entity, 'key': key}).mappings().one_or_none()
    if definitions or others or (existing and (existing['job_type'] != 'ledger.probe'
                                              or existing['state'] not in {'queued', 'succeeded'})):
        raise ValueError('Probe isolation not established; inspect privately without running a worker.')
    return dict(existing) if existing else None


def execute(environ):
    operation = validate(environ)
    deployed_release(environ)
    settings = None
    if operation == 'probe':
        settings = TelemetrySettings(*(environ.get('TABULA_RASA_TELEMETRY_' + field, '')
                                       for field in ('ENDPOINT', 'TOKEN', 'SCOPE_KEY')))
        if not all((settings.endpoint, settings.token, settings.scope_key)):
            raise ValueError('Protected staging telemetry settings are required.')
    database, telemetry = open_alpha_database(environ), None
    try:
        if database.health() != {'connected': True, 'revision': head_revision()}:
            raise ValueError('Staging schema is not current.')
        with database.transaction(read_only=True) as connection:
            if connection.scalar(text('SELECT current_user')) != 'tabula_staging_runtime':
                raise ValueError('Unexpected effective database role.')
        entity = environ['STAGING_JOB_PROBE_ENTITY_ID']
        key = 'observability-probe-' + environ['GITHUB_RUN_ID']
        existing = inspect_queue(database, entity, key)
        result = {'environment': 'staging', 'release': environ['GITHUB_SHA']}
        if operation == 'inspect':
            return {**result, 'status': 'isolated_probe_target_inspected'}
        if existing and existing['state'] == 'succeeded':
            return {**result, 'status': 'already_succeeded_no_reexecution'}
        configure_console()
        telemetry = Telemetry('staging', environ['GITHUB_SHA'], settings,
                              failure_codes={'job_poison', 'job_transient', 'job_lease_expired'})
        queue = JobQueue(database, entity, operator_registry(), service_id='staging-observability-probe',
                         telemetry=telemetry)
        job = queue.enqueue('ledger.probe', key, max_attempts=1, due_at=PROBE_DUE_AT)
        inspect_queue(database, entity, key)
        claim = queue.claim()
        if not claim or str(claim[0]) != job or not queue.execute(*claim):
            raise ValueError('Probe outcome unconfirmed; inspect durable history before retrying.')
        confirmed = inspect_queue(database, entity, key)
        if not confirmed or confirmed['state'] != 'succeeded':
            raise ValueError('Probe success not confirmed.')
        return {**result, 'status': 'probe_succeeded_delivery_requires_collector_readback'}
    finally:
        if telemetry is not None:
            telemetry.close()
        database.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['preflight', 'execute'])
    args = parser.parse_args()
    try:
        result = (preflight if args.command == 'preflight' else execute)(dict(os.environ))
    except Exception:
        raise SystemExit('Staging probe stopped. Inspect protected scope, release, role and durable history; do not retry blindly.') from None
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
