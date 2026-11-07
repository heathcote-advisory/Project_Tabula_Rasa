from unittest.mock import MagicMock, Mock

import pytest

from tabula_rasa.deployment import staging_job_probe as probe


def environment(operation='probe'):
    return {'GITHUB_REPOSITORY': 'heathcote-advisory/Project_Tabula_Rasa', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_SHA': 'a' * 40, 'TABULA_RASA_RELEASE_ID': 'a' * 40, 'GITHUB_RUN_ID': '123',
            'TABULA_RASA_ENVIRONMENT': 'staging', 'TABULA_RASA_ACCESS_MODE': 'named_user',
            'RENDER_SERVICE_ID': probe.SERVICES['staging'][0],
            'TABULA_RASA_PUBLIC_ORIGIN': probe.SERVICES['staging'][1],
            'STAGING_JOB_PROBE_ENTITY_ID': 'b' * 32,
            'JOB_PROBE_OPERATION': operation, 'JOB_PROBE_APPROVAL': 'one_probe_no_other_job_operators',
            'TABULA_RASA_DATABASE_URL': 'postgresql://tabula_staging_runtime@localhost/synthetic',
            'TABULA_RASA_TELEMETRY_ENDPOINT': 'https://synthetic.betterstackdata.com',
            'TABULA_RASA_TELEMETRY_TOKEN': 'synthetic-token-for-tests',
            'TABULA_RASA_TELEMETRY_SCOPE_KEY': 'k' * 32}


@pytest.mark.parametrize('change', [
    {'GITHUB_REPOSITORY': 'other/repo'}, {'GITHUB_REPOSITORY': 'Roklion/Project_Tabula_Rasa'},
    {'GITHUB_REF': 'refs/heads/other'},
    {'GITHUB_SHA': ''}, {'TABULA_RASA_RELEASE_ID': 'c' * 40}, {'GITHUB_RUN_ID': '../bad'},
    {'TABULA_RASA_ENVIRONMENT': 'production'}, {'TABULA_RASA_ACCESS_MODE': 'restricted_alpha'},
    {'RENDER_SERVICE_ID': probe.SERVICES['development'][0]},
    {'TABULA_RASA_PUBLIC_ORIGIN': probe.SERVICES['development'][1]},
    {'STAGING_JOB_PROBE_ENTITY_ID': ''}, {'JOB_PROBE_OPERATION': 'tick'},
    {'JOB_PROBE_APPROVAL': 'not_approved'},
    {'TABULA_RASA_DATABASE_URL': 'postgresql://neondb_owner@localhost/synthetic'},
])
def test_invalid_scope_or_owner_credential_stops_before_io(change, monkeypatch):
    opened, deployed = Mock(), Mock()
    monkeypatch.setattr(probe, 'open_alpha_database', opened)
    monkeypatch.setattr(probe, 'deployed_release', deployed)
    with pytest.raises(ValueError):
        probe.execute({**environment(), **change})
    opened.assert_not_called()
    deployed.assert_not_called()


def test_inspection_requires_no_mutation_approval_or_exporter():
    env = environment('inspect')
    env['JOB_PROBE_APPROVAL'] = 'not_approved'
    env.pop('TABULA_RASA_TELEMETRY_TOKEN')
    assert probe.validate(env) == 'inspect'


def test_exact_tested_image_required_before_public_probe(monkeypatch):
    public, verified = Mock(), Mock()
    monkeypatch.setattr(probe, 'deployed_release', public)
    monkeypatch.setattr(probe.release, 'validated_main', verified)
    monkeypatch.setattr(probe.release, 'image_metadata', lambda *_: {'release': 'c' * 40, 'schema': probe.head_revision()})
    with pytest.raises(ValueError):
        probe.preflight(environment())
    public.assert_not_called()
    verified.assert_not_called()


@pytest.mark.parametrize('image_repository,accepted', [
    ('ghcr.io/heathcote-advisory/project_tabula_rasa/commercial-private', True),
    ('ghcr.io/heathcote-advisory/project_tabula_rasa/commercial', False),
    ('ghcr.io/roklion/project_tabula_rasa/commercial', False),
    ('ghcr.io/other/project_tabula_rasa/commercial', False),
])
def test_organization_image_preflight_preserves_repository_boundary(image_repository, accepted, monkeypatch):
    env = {**environment('inspect'), 'IMAGE': image_repository + '@sha256:' + 'd' * 64}
    public, verified = Mock(), Mock()

    def inspect(image, repository):
        # Substitute registry I/O, retaining the real immutable-reference guard.
        probe.release.immutable_digest(image, repository)
        return {'release': env['GITHUB_SHA'], 'schema': probe.head_revision()}

    metadata = Mock(side_effect=inspect)
    monkeypatch.setattr(probe.release, 'image_metadata', metadata)
    monkeypatch.setattr(probe.release, 'validated_main', verified)
    monkeypatch.setattr(probe, 'deployed_release', public)
    if accepted:
        assert probe.preflight(env) == {
            'status': 'tested_deployed_revision_verified', 'release': env['GITHUB_SHA'],
        }
        verified.assert_called_once_with(env['GITHUB_SHA'], env)
        public.assert_called_once_with(env)
    else:
        with pytest.raises(probe.release.ReleaseError, match='immutable digest'):
            probe.preflight(env)
        verified.assert_not_called()
        public.assert_not_called()
    metadata.assert_called_once_with(env['IMAGE'], 'ghcr.io/heathcote-advisory/project_tabula_rasa/commercial-private')


def test_public_release_mismatch_does_not_open_database(monkeypatch):
    opened = Mock()
    monkeypatch.setattr(probe, 'open_alpha_database', opened)
    monkeypatch.setattr(probe, 'deployed_release', Mock(side_effect=ValueError('mismatch')))
    with pytest.raises(ValueError):
        probe.execute(environment())
    opened.assert_not_called()


def test_exporter_required_before_database_or_job_mutation(monkeypatch):
    opened = Mock()
    monkeypatch.setattr(probe, 'open_alpha_database', opened)
    monkeypatch.setattr(probe, 'deployed_release', Mock())
    env = environment()
    env.pop('TABULA_RASA_TELEMETRY_SCOPE_KEY')
    with pytest.raises(ValueError):
        probe.execute(env)
    opened.assert_not_called()


def configured_database(monkeypatch):
    database = MagicMock()
    database.health.return_value = {'connected': True, 'revision': probe.head_revision()}
    database.transaction.return_value.__enter__.return_value.scalar.return_value = 'tabula_staging_runtime'
    monkeypatch.setattr(probe, 'open_alpha_database', Mock(return_value=database))
    monkeypatch.setattr(probe, 'deployed_release', Mock())
    return database


@pytest.mark.parametrize('operation,existing,status', [
    ('inspect', None, 'isolated_probe_target_inspected'),
    ('probe', {'state': 'succeeded'}, 'already_succeeded_no_reexecution'),
])
def test_read_only_or_completed_probe_never_composes_worker(operation, existing, status, monkeypatch):
    database = configured_database(monkeypatch)
    worker, exporter = Mock(), Mock()
    monkeypatch.setattr(probe, 'inspect_queue', Mock(return_value=existing))
    monkeypatch.setattr(probe, 'JobQueue', worker)
    monkeypatch.setattr(probe, 'Telemetry', exporter)
    assert probe.execute(environment(operation))['status'] == status
    worker.assert_not_called()
    exporter.assert_not_called()
    database.dispose.assert_called_once()


@pytest.mark.parametrize('succeeds', [True, False])
def test_one_probe_has_no_scheduler_or_retry_and_always_closes(succeeds, monkeypatch):
    database = configured_database(monkeypatch)
    worker, telemetry = Mock(), Mock()
    worker.enqueue.return_value = 'probe-job'
    worker.claim.return_value = ('probe-job', 'lease')
    worker.execute.return_value = succeeds
    monkeypatch.setattr(probe, 'inspect_queue', Mock(side_effect=[None, {'state': 'queued'}, {'state': 'succeeded'}]))
    monkeypatch.setattr(probe, 'JobQueue', Mock(return_value=worker))
    monkeypatch.setattr(probe, 'Telemetry', Mock(return_value=telemetry))
    if succeeds:
        assert probe.execute(environment())['status'] == 'probe_succeeded_delivery_requires_collector_readback'
    else:
        with pytest.raises(ValueError):
            probe.execute(environment())
    worker.enqueue.assert_called_once_with('ledger.probe', 'observability-probe-123', max_attempts=1,
                                           due_at=probe.PROBE_DUE_AT)
    worker.execute.assert_called_once_with('probe-job', 'lease')
    worker.tick.assert_not_called()
    worker.schedule_due.assert_not_called()
    telemetry.close.assert_called_once()
    database.dispose.assert_called_once()


def test_effective_owner_role_rejected_and_connection_closed(monkeypatch):
    database = configured_database(monkeypatch)
    database.transaction.return_value.__enter__.return_value.scalar.return_value = 'neondb_owner'
    inspection = Mock()
    monkeypatch.setattr(probe, 'inspect_queue', inspection)
    with pytest.raises(ValueError):
        probe.execute(environment('inspect'))
    inspection.assert_not_called()
    database.dispose.assert_called_once()


def test_workflow_has_no_timer_owner_credential_or_freeform_entity():
    from pathlib import Path
    import yaml
    workflow = yaml.safe_load(Path('.github/workflows/commercial-staging-job-probe.yml').read_text())
    trigger = workflow.get('on', workflow.get(True))
    assert set(trigger) == {'workflow_dispatch'}
    assert set(trigger['workflow_dispatch']['inputs']) == {'image', 'operation', 'approval'}
    job = workflow['jobs']['staging-probe']
    assert job['environment'] == 'staging'
    assert job['concurrency']['group'] == 'commercial-deploy-staging'
    serialized = str(job)
    assert 'TABULA_RASA_MIGRATION_DATABASE_URL' not in serialized
    assert 'TABULA_RASA_RUNTIME_DATABASE_URL' in serialized
    installs = [step['run'] for step in job['steps'] if 'pip install' in step.get('run', '')]
    assert installs == ['python -m pip install -r requirements-postgres.txt requests==2.34.2']
