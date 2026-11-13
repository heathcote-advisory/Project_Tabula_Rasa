from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tabula_rasa.deployment import automatic_release, release, release_checkpoint, schema_migration
from tests.tooling.test_schema_window import CANDIDATE, ENV, IMAGE, NOW, PRIOR, PRIOR_IMAGE, SERVICE


@pytest.mark.parametrize(("utc", "hour"), [("2026-03-08", 7), ("2026-11-01", 8)])
def test_production_cutoff_follows_eastern_dst(utc, hour):
    start = datetime.fromisoformat(f"{utc}T{hour:02}:00:00+00:00")
    assert automatic_release.production_cutoff(ENV, start - timedelta(seconds=1)) is None
    assert automatic_release.production_cutoff(ENV, start + timedelta(minutes=5)) == start
    assert automatic_release.production_cutoff(ENV, start + timedelta(minutes=15)) is None
    assert automatic_release.production_cutoff({**ENV, "GITHUB_RUN_ATTEMPT": "2"}, start) is None


def test_selection_uses_only_the_live_release_artifact_and_defers_late_staging():
    name = f"staging-release-{CANDIDATE['release']}"
    artifact = {"name": name, "expired": False, "workflow_run": {"id": 123}}
    cutoff = NOW - timedelta(minutes=5)
    result = {"total_count": 1, "artifacts": [{**artifact, "created_at": NOW.isoformat()}]}
    github = Mock(return_value=result)
    environ = {**ENV, "GH_TOKEN": "fixture"}
    assert automatic_release.staging_run(CANDIDATE, environ, cutoff, github=github) is None
    assert automatic_release.staging_run(CANDIDATE, environ, cutoff + timedelta(days=1), github=github) == "123"
    assert f"name={name}" in github.call_args.args[0]


def test_production_enablement_has_no_provider_side_effects():
    provider = Mock()
    result = automatic_release.run({**ENV, "TABULA_RASA_ENVIRONMENT": "production"},
                                   read=provider, github=provider, now=lambda: NOW)
    assert result["status"] == "skipped"
    provider.assert_not_called()


@pytest.mark.parametrize(("mode", "parent", "dependencies"), [
    (None, "source", None), ("destructive", "source", None),
    ("maintenance", ("source", "other"), None), ("maintenance", "source", "other"),
])
def test_unreviewed_and_nonlinear_schema_paths_fail_closed(mode, parent, dependencies):
    contract = {"mode": mode, "data": "synthetic", "recovery": "forward_only", "grants": ()} if mode else None
    scripts = Mock()
    scripts.get_revision.side_effect = lambda revision: SimpleNamespace(revision=revision)
    scripts.iterate_revisions.return_value = [
        SimpleNamespace(revision="target", down_revision=parent, dependencies=dependencies,
                        module=SimpleNamespace(rollout=contract)),
    ]
    with pytest.raises(schema_migration.ContractError, match="reviewed linear"):
        schema_migration.rollout_path("source", "target", scripts=scripts)


def test_production_attempt_is_recorded_and_a_failed_digest_requires_rearming():
    environ = {**ENV, "GH_TOKEN": "fixture", "GITHUB_RUN_ID": "123"}
    github = Mock(side_effect=[[], {"id": 9, "sha": CANDIDATE["release"]}, {}, {}])
    mark = automatic_release.journal(IMAGE, CANDIDATE, environ, github=github)
    mark("failure")
    assert github.call_args_list[2].kwargs["body"]["state"] == "in_progress"
    assert github.call_args.kwargs["body"]["state"] == "failure"
    github = Mock(side_effect=[[{"id": 9}], [{"state": "failure"}]])
    with pytest.raises(release.ReleaseError, match="already attempted"):
        automatic_release.journal(IMAGE, CANDIDATE, environ, github=github)
    assert all("body" not in call.kwargs for call in github.call_args_list)


@pytest.mark.parametrize("outcome", ["promote", "unchanged", "staging_changed", "failed_smoke"])
def test_scheduled_production_rechecks_staging_and_records_actual_outcome(monkeypatch, outcome):
    from tabula_rasa.deployment import production, schema_window

    candidate = {**CANDIDATE, "schema": PRIOR["schema"]}
    previous = (IMAGE, candidate) if outcome == "unchanged" else (PRIOR_IMAGE, PRIOR)
    environ = {**ENV, "TABULA_RASA_ENVIRONMENT": "production", "COMMERCIAL_AUTO_PRODUCTION_ENABLED": "true",
               "STAGING_RENDER_SERVICE_ID": "srv-stageother", "STAGING_RENDER_API_KEY": "fixture"}
    monkeypatch.setattr(release, "live_image", Mock(side_effect=[(IMAGE, candidate), previous, previous]))
    monkeypatch.setattr(schema_window, "service_state", Mock(return_value={
        "suspended": "not_suspended", "autoDeployTrigger": "off",
        "serviceDetails": {"healthCheckPath": "/health/ready", "runtime": "image"},
    }))
    monkeypatch.setattr(production, "verify_render_origin", Mock())
    monkeypatch.setattr(production, "verify_render_database", Mock(return_value="runtime"))
    monkeypatch.setattr(production, "render_setting", lambda service, token, key, **_: {
        "ENVIRONMENT": "production", "ACCESS_MODE": "named_user", "PUBLIC_ORIGIN": ENV["TABULA_RASA_PUBLIC_ORIGIN"],
    }[key])
    monkeypatch.setattr(production, "production_target", Mock())
    monkeypatch.setattr(release, "verify_prior_release", Mock())
    monkeypatch.setattr(automatic_release, "staging_run", Mock(return_value="123"))
    proof = {"staging_image": IMAGE, "staging_run": "123"}
    recheck = release.ReleaseError("Staging was superseded.") if outcome == "staging_changed" else proof
    evidence = Mock(side_effect=[proof, recheck])
    monkeypatch.setattr(production, "staging_evidence", evidence)
    monkeypatch.setattr(automatic_release, "verify_checkpoint", Mock(return_value={"checkpoint_lsn": "0/ABC"}))
    monkeypatch.setattr(automatic_release, "attempt_status", Mock(return_value="success"))
    status = Mock()
    record = Mock(return_value=status)
    monkeypatch.setattr(automatic_release, "journal", record)
    deploy = Mock(return_value=("dep-new", NOW.isoformat()))
    monkeypatch.setattr(release, "deploy_verified", deploy)
    monkeypatch.setattr(release, "active_deployment", Mock(side_effect=[
        {"id": "dep-prior", "image": {"ref": PRIOR_IMAGE, "sha": PRIOR["manifest_digest"]}},
        {"id": "dep-new"},
    ]))
    smoke = Mock(side_effect=[None, release.ReleaseError("New image smoke failed.")]
                 if outcome == "failed_smoke" else None)

    def invoke():
        return automatic_release.run(environ, read=Mock(), verify=Mock(), database=Mock(),
                                     smoke=smoke, now=lambda: NOW)

    if outcome in {"staging_changed", "failed_smoke"}:
        with pytest.raises(release.ReleaseError):
            invoke()
        if outcome == "staging_changed":
            record.assert_not_called()
            deploy.assert_not_called()
        else:
            status.assert_called_once_with("failure")
        return
    result = invoke()
    if outcome == "unchanged":
        assert result["reason"] == "already_live_and_ready"
        record.assert_not_called()
        deploy.assert_not_called()
    else:
        assert result["image"] == IMAGE and result["restarted"]
        assert deploy.call_args.args[2] == IMAGE and evidence.call_count == 2
        assert smoke.call_args_list == [call(ENV["TABULA_RASA_PUBLIC_ORIGIN"], PRIOR["release"]),
                                        call(ENV["TABULA_RASA_PUBLIC_ORIGIN"], candidate["release"])]
        status.assert_called_once_with("success")


def test_immediate_staging_uses_shared_maintenance_and_free_fails_before_mutation(staging_release):
    stage, invoke = staging_release
    stage.details["plan"] = "free"
    with pytest.raises(release.ReleaseError, match="Render Free"):
        invoke()
    assert stage.actions == []
    stage.details["plan"] = "starter"
    result = invoke()
    assert result["restarted"] is True and result["image"] == IMAGE
    assert stage.actions.index("upgrade") < stage.actions.index("resume") < stage.actions.index("smoke_new")


@pytest.fixture
def staging_release(monkeypatch):
    from tests.tooling.test_schema_window import Staging

    stage = Staging()
    monkeypatch.setattr(release.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    monkeypatch.setattr(automatic_release, "verify_checkpoint", lambda point, *_: point)
    original = automatic_release.schema_window.run

    def maintenance(*args, **kwargs):
        return original(*args, **kwargs, check_closed=stage.closed, check_reopened=stage.reopened)

    def database(*args, apply, checkpoint=False):
        if checkpoint:
            return {"checkpoint_at": NOW.isoformat()}
        return stage.db(*args, apply=apply)

    def invoke():
        return automatic_release.run(
            {**ENV, "IMAGE": IMAGE, "RELEASE_COMMIT": CANDIDATE["release"], "WINDOW_EXCLUSIVE_RENDER_WRITER": "true"},
            read=stage.read, metadata=lambda image, _: CANDIDATE if image == IMAGE else PRIOR,
            verify=Mock(), database=database, maintenance=maintenance, smoke=stage.smoke,
            now=lambda: NOW + timedelta(hours=12),
        )

    return stage, invoke


def test_recovery_checkpoint_requires_matching_neon_history():
    environ = {**ENV, "RENDER_SERVICE_ID": SERVICE, "NEON_PROJECT_ID": "project", "NEON_BRANCH_ID": "br-root",
               "NEON_ENDPOINT_ID": "ep-stage", "NEON_RELEASE_API_KEY": "fixture",
               "WINDOW_MIGRATION_DATABASE_URL": "postgresql://owner:fixture@ep-stage.neon.tech/ledger?sslmode=require"}
    point = {"lsn": "0/ABC", "at": NOW.isoformat()}
    endpoint = {"endpoint": {"id": "ep-stage", "host": "ep-stage.neon.tech", "branch_id": "br-root",
                             "type": "read_write", "disabled": False}}
    project = {"project": {"id": "project", "history_retention_seconds": 3600}}
    branch = {"branch": {"id": "br-root"}}
    read = Mock(side_effect=[endpoint, project, branch, {"sql": "CREATE TABLE public.alembic_version ();"}])
    result = release_checkpoint.verify_checkpoint(point, environ, NOW, read=read)
    assert result["checkpoint_lsn"] == "0/ABC" and len(result["recovery_sha256"]) == 64
    assert "lsn=0%2FABC" in read.call_args.args[0]
    with pytest.raises(release.ReleaseError, match="retained history"):
        release_checkpoint.verify_checkpoint(point, environ, NOW, read=Mock(side_effect=[endpoint, project, branch, {}]))
    with pytest.raises(release.ReleaseError, match="retained history"):
        release_checkpoint.verify_checkpoint(point, {**environ, "TABULA_RASA_DEPLOY_DATABASE_SHA256": "a" * 64},
                                             NOW, read=Mock())
