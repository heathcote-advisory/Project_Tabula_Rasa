from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import yaml

from tabula_rasa.deployment import database, production, release, smoke as smoke_module
from tabula_rasa.migration.model import digest
from tabula_rasa.infrastructure.postgres import hosted

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "ghcr.io/heathcote-advisory/project_tabula_rasa/commercial-private"
IMAGE = REPOSITORY + "@sha256:" + "a" * 64
PRIOR = REPOSITORY + "@sha256:" + "b" * 64
MANIFEST = "sha256:" + "e" * 64
PRIOR_MANIFEST = "sha256:" + "f" * 64
ENV = {"TABULA_RASA_ENVIRONMENT": "staging", "GITHUB_REPOSITORY": "heathcote-advisory/Project_Tabula_Rasa",
       "TABULA_RASA_ACCESS_MODE": "restricted_alpha",
       "TABULA_RASA_PUBLIC_ORIGIN": "https://stage.example.test", "RENDER_SERVICE_ID": "srv-test",
       "RENDER_API_KEY": "synthetic-only", "TABULA_RASA_ALPHA_USERNAME": "stage", "TABULA_RASA_ALPHA_PASSWORD": "synthetic"}


@pytest.fixture(autouse=True)
def release_ancestry(monkeypatch):
    # The synthetic provider's prior revision normally precedes the candidate.
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(release, "subprocess", SimpleNamespace(run=run))
    return run


def metadata(image, repository):
    assert repository == REPOSITORY
    return {"release": ("c" if image == IMAGE else "d") * 40, "schema": "0001_initial",
            "manifest_digest": MANIFEST if image == IMAGE else PRIOR_MANIFEST}


def provider(*, existing=True, platform_sha=False, prior_ref=PRIOR):
    def read(path, token, *, body=None):
        assert token == "synthetic-only"
        if path.endswith("?limit=100"):
            return [{"deploy": {"status": "live", "image": {"ref": prior_ref,
                     "sha": PRIOR_MANIFEST[7:] if platform_sha else "sha256:" + "b" * 64}}}] if existing else []
        if body:
            return {"id": "dep-new" if body["imageUrl"] == IMAGE else "dep-old"}
        new = path.endswith("dep-new")
        return {"status": "live", "image": {"ref": IMAGE if new else PRIOR,
                "sha": (MANIFEST if new else PRIOR_MANIFEST)[7:] if platform_sha else "sha256:" + ("a" if new else "b") * 64}}
    return Mock(side_effect=read)


@pytest.mark.parametrize("platform_sha", [False, True])
def test_smoke_success_records_exact_release_without_extra_deploys(platform_sha):
    read, smoke = provider(platform_sha=platform_sha), Mock()
    result = release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    assert result["image"] == IMAGE and result["status"] == "smoke_passed"
    assert result["environment"] == "staging" and result["deploy_id"] == "dep-new"
    assert result["release"] == "c" * 40 and result["schema"] == "0001_initial"
    assert result["deploy_started_at"] <= result["verified_at"]
    assert sum(call.kwargs.get("body") is not None for call in read.call_args_list) == 1
    smoke.assert_called_once_with(ENV["TABULA_RASA_PUBLIC_ORIGIN"], "stage", "synthetic", "c" * 40)


def test_superseded_staging_release_stops_before_migration_or_render_mutation(monkeypatch, release_ancestry, capsys):
    read, smoke = provider(), Mock()
    release_ancestry.return_value.returncode = 1
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(release, "image_metadata", metadata)
    monkeypatch.setattr(release, "validated_main", Mock())
    monkeypatch.setattr(release, "api", read)
    monkeypatch.setattr("sys.argv", ["release", "verify", "--image", IMAGE])

    with pytest.raises(SystemExit, match="superseded release"):
        release.main()
    assert not capsys.readouterr().out
    with pytest.raises(release.ReleaseError, match="superseded release"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    release_ancestry.assert_called_with(
        ["git", "merge-base", "--is-ancestor", "d" * 40, "c" * 40], capture_output=True, check=False,
    )
    assert all(not call.kwargs.get("body") for call in read.call_args_list)
    smoke.assert_not_called()


@pytest.mark.parametrize("prior_repository_matches", [True, False])
def test_development_preflight_checks_live_image_before_migration(monkeypatch, capsys, prior_repository_matches):
    for key, value in {**ENV, "TABULA_RASA_ENVIRONMENT": "development"}.items():
        monkeypatch.setenv(key, value)
    prior_ref = PRIOR if prior_repository_matches else PRIOR.replace(REPOSITORY, "ghcr.io/previous-owner/repo/commercial")
    read = provider(prior_ref=prior_ref)
    monkeypatch.setattr(release, "image_metadata", metadata)
    monkeypatch.setattr(release, "validated_main", Mock())
    monkeypatch.setattr(release, "api", read)
    monkeypatch.setattr("sys.argv", ["release", "verify", "--image", IMAGE])

    if prior_repository_matches:
        release.main()
        assert json.loads(capsys.readouterr().out)["release"] == "c" * 40
    else:
        with pytest.raises(SystemExit, match="Prior image needs a verified immutable reference"):
            release.main()
        assert not capsys.readouterr().out
    read.assert_called_once_with("/services/srv-test/deploys?limit=100", "synthetic-only")


def test_same_revision_staging_retry_is_allowed(release_ancestry):
    candidate = metadata(IMAGE, REPOSITORY)
    release.verify_prior_release(candidate, candidate, "staging")
    release_ancestry.assert_not_called()


def test_git_ancestry_error_is_distinct_from_a_superseded_release(release_ancestry):
    release_ancestry.return_value.returncode = 128
    release_ancestry.return_value.stderr = b"private diagnostic"
    with pytest.raises(release.ReleaseError, match="inspect the workflow checkout") as failure:
        release.verify_prior_release(metadata(IMAGE, REPOSITORY), metadata(PRIOR, REPOSITORY), "staging")
    assert "private diagnostic" not in str(failure.value)


@pytest.mark.parametrize("payload", [{}, [{"deploy": {}}], [{"deploy": {"status": None}}]])
def test_invalid_render_deployment_list_stops_before_mutation(payload):
    read, smoke = Mock(return_value=payload), Mock()
    with pytest.raises(release.ReleaseError, match="Invalid Render deployment"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    read.assert_called_once_with("/services/srv-test/deploys?limit=100", "synthetic-only")
    smoke.assert_not_called()


def test_manual_staging_rollback_requires_the_expected_live_revision(release_ancestry):
    read, smoke = provider(), Mock()
    with pytest.raises(release.ReleaseError, match="expected live revision"):
        release.promote(IMAGE, {**ENV, "STAGING_ROLLBACK_FROM": "e" * 40},
                        read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    assert all(not call.kwargs.get("body") for call in read.call_args_list)
    smoke.assert_not_called()

    result = release.promote(IMAGE, {**ENV, "STAGING_ROLLBACK_FROM": "d" * 40},
                             read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    release_ancestry.assert_called_once_with(
        ["git", "merge-base", "--is-ancestor", "c" * 40, "d" * 40], capture_output=True, check=False,
    )
    assert result["rollback_from"] == "d" * 40


@pytest.mark.parametrize("platform_sha", [False, True])
def test_failed_smoke_restores_prior_schema_compatible_digest_and_verifies_it(platform_sha):
    read = provider(platform_sha=platform_sha)
    smoke = Mock(side_effect=[ValueError("private diagnostic"), None])
    with pytest.raises(release.ReleaseError, match="restored and smoke verified"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    assert [call.kwargs["body"]["imageUrl"] for call in read.call_args_list if call.kwargs.get("body")] == [IMAGE, PRIOR]
    assert smoke.call_args_list[1].args[-1] == "d" * 40


def test_initial_smoke_failure_stops_without_inventing_rollback():
    read = provider(existing=False)
    with pytest.raises(release.ReleaseError, match="no prior image"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=Mock(side_effect=ValueError))
    assert sum(call.kwargs.get("body") is not None for call in read.call_args_list) == 1


def test_incompatible_prior_image_stops_before_deploy():
    read = provider()
    def incompatible(image, repository):
        return {**metadata(image, repository), "schema": "new" if image == IMAGE else "old"}
    with pytest.raises(release.ReleaseError, match="schema-incompatible"):
        release.promote(IMAGE, ENV, read=read, metadata=incompatible, verify=Mock(), smoke_check=Mock())
    assert not any(call.kwargs.get("body") for call in read.call_args_list)


def test_uncertain_deploy_wait_never_starts_competing_rollback():
    read = provider()
    original = read.side_effect
    def uncertain(path, token, **kwargs):
        if path.endswith("dep-new"):
            raise release.ReleaseError("uncertain")
        return original(path, token, **kwargs)
    read.side_effect = uncertain
    with pytest.raises(release.ReleaseError, match="uncertain"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=Mock())
    assert sum(call.kwargs.get("body") is not None for call in read.call_args_list) == 1


@pytest.mark.parametrize("phase", ["prior", "candidate", "rollback"])
@pytest.mark.parametrize("image_data", [None, {}, {"ref": IMAGE}, {"sha": MANIFEST},
                         {"ref": IMAGE, "sha": "sha256:" + "9" * 64},
                         {"ref": "evil.test/image@" + MANIFEST, "sha": MANIFEST},
                         {"ref": IMAGE, "sha": "private diagnostic"},
                         {"ref": IMAGE, "sha": None}, {"ref": IMAGE + "\n", "sha": MANIFEST},
                         {"ref": REPOSITORY + ":latest", "sha": MANIFEST}])
def test_invalid_render_metadata_stops_at_each_verification_boundary(phase, image_data):
    read = provider()
    original = read.side_effect
    def altered(path, token, **kwargs):
        response = original(path, token, **kwargs)
        if phase == "prior" and path.endswith("?limit=100"):
            response[0]["deploy"]["image"] = image_data
        elif path.endswith("dep-new" if phase == "candidate" else "dep-old") and phase != "prior":
            response["image"] = image_data
        return response
    read.side_effect = altered
    smoke = Mock(side_effect=ValueError("private diagnostic"))
    with pytest.raises(release.ReleaseError) as failure:
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    assert "private diagnostic" not in str(failure.value)
    assert sum(call.kwargs.get("body") is not None for call in read.call_args_list) == {
        "prior": 0, "candidate": 1, "rollback": 2}[phase]
    assert smoke.call_count == (1 if phase == "rollback" else 0)


def test_unpullable_prior_and_unverified_candidate_stop_before_deploy():
    read = provider()
    def unpullable(image, repository):
        if image == PRIOR:
            raise release.ReleaseError("Image cannot be pulled")
        return metadata(image, repository)
    with pytest.raises(release.ReleaseError, match="cannot be pulled"):
        release.promote(IMAGE, ENV, read=read, metadata=unpullable, verify=Mock())
    assert not any(call.kwargs.get("body") for call in read.call_args_list)
    read.reset_mock()
    with pytest.raises(release.ReleaseError, match="unverified"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata,
                        verify=Mock(side_effect=release.ReleaseError("unverified")))
    read.assert_not_called()


def test_uncertain_create_never_retries_or_rolls_back():
    read = provider()
    original = read.side_effect
    def uncertain(path, token, **kwargs):
        if kwargs.get("body"):
            raise release.ReleaseError("uncertain")
        return original(path, token, **kwargs)
    read.side_effect = uncertain
    smoke = Mock()
    with pytest.raises(release.ReleaseError, match="uncertain"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    assert sum(call.kwargs.get("body") is not None for call in read.call_args_list) == 1
    smoke.assert_not_called()


def test_promotion_timeout_never_rolls_back_or_runs_smoke(monkeypatch):
    read, smoke = provider(), Mock()
    monkeypatch.setattr(release, "wait_live", Mock(side_effect=release.ReleaseError("Deployment timed out")))
    with pytest.raises(release.ReleaseError, match="timed out"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=smoke)
    assert sum(call.kwargs.get("body") is not None for call in read.call_args_list) == 1
    smoke.assert_not_called()


@pytest.mark.parametrize("status", ["build_failed", "update_failed", "pre_deploy_failed", "canceled"])
def test_terminal_deploy_failure_and_timeout_are_bounded(status):
    read, sleep = Mock(return_value={"status": status}), Mock()
    with pytest.raises(release.ReleaseError):
        release.wait_live("srv-test", "dep-test", "secret", read=read, sleep=sleep)
    assert read.call_count == 1 and not sleep.called
    read.return_value = {"status": "update_in_progress"}
    with pytest.raises(release.ReleaseError, match="timed out"):
        release.wait_live("srv-test", "dep-test", "secret", read=read, sleep=sleep)
    assert sleep.call_count == 60


@pytest.mark.parametrize("image", [REPOSITORY + ":latest", "evil.test/image@sha256:" + "a" * 64, IMAGE + "\n"])
def test_untrusted_image_references_rejected_before_docker(image, monkeypatch):
    run = Mock()
    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(release.ReleaseError):
        release.image_metadata(image, REPOSITORY)
    run.assert_not_called()


def test_image_metadata_requires_runtime_platform_and_bounded_sha_schema(monkeypatch):
    labels = {"org.opencontainers.image.revision": "c" * 40, "io.tabula.schema": "0005_bookkeeping_cutover"}
    def run(command, **kwargs):
        if "imagetools" in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"digest": IMAGE.split("@")[1],
                                   "mediaType": "application/vnd.oci.image.manifest.v1+json"}).encode())
        payload = [{"Architecture": "amd64", "Os": "linux", "Config": {"Labels": labels}}]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode())
    monkeypatch.setattr(release.subprocess, "run", run)
    assert release.image_metadata(IMAGE, REPOSITORY)["release"] == "c" * 40
    labels["org.opencontainers.image.revision"] = "untrusted release text"
    with pytest.raises(release.ReleaseError, match="metadata"):
        release.image_metadata(IMAGE, REPOSITORY)


def test_ci_verifier_rejects_failed_and_superseded_development_releases(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=("c" * 40).encode()))
    monkeypatch.setattr(release.subprocess, "run", run)
    provider = Mock(return_value={"workflow_runs": [{"conclusion": "success", "head_sha": "c" * 40}]})
    monkeypatch.setattr(release, "api", provider)
    environ = {**ENV, "GH_TOKEN": "synthetic"}
    release.validated_main("c" * 40, environ)
    provider.return_value = {"workflow_runs": [{"conclusion": "failure", "head_sha": "c" * 40}]}
    with pytest.raises(release.ReleaseError, match="CI success"):
        release.validated_main("c" * 40, environ)
    provider.reset_mock()
    with pytest.raises(release.ReleaseError, match="Superseded"):
        release.validated_main("d" * 40, {**environ, "TABULA_RASA_ENVIRONMENT": "development"})
    provider.assert_not_called()


def test_active_deploy_and_production_stop_without_provider_mutation():
    read = Mock(return_value=[{"deploy": {"status": "update_in_progress"}}])
    with pytest.raises(release.ReleaseError, match="in progress"):
        release.promote(IMAGE, ENV, read=read, metadata=metadata, verify=Mock(), smoke_check=Mock())
    assert not any(call.kwargs.get("body") for call in read.call_args_list)
    read.reset_mock()
    with pytest.raises(release.ReleaseError, match="Production requires named-user"):
        release.promote(IMAGE, {**ENV, "TABULA_RASA_ENVIRONMENT": "production"}, read=read)
    read.assert_not_called()


def test_migration_orders_existing_cli_and_never_deploys_after_failure(monkeypatch):
    target = Mock()
    target.health.return_value = {"connected": True, "revision": database.head_revision()}
    monkeypatch.setattr(database, "bound_database", lambda _: target)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(database.subprocess, "run", run)
    database.migrate({"synthetic": "configuration"})
    assert [call.args[0][-1] for call in run.call_args_list] == ["current", "upgrade", "health"]
    run.reset_mock()
    run.side_effect = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)]
    with pytest.raises(ValueError, match="stopped"):
        database.migrate({})
    assert run.call_count == 2


def test_schema_change_and_wrong_target_stop_before_migration(monkeypatch):
    target = Mock()
    target.health.return_value = {"connected": True, "revision": "old"}
    monkeypatch.setattr(database, "bound_database", lambda _: target)
    run = Mock()
    monkeypatch.setattr(database.subprocess, "run", run)
    with pytest.raises(ValueError, match="Schema change"):
        database.migrate({})
    run.assert_not_called()
    target.dispose.assert_called_once()


def test_shared_runtime_migrator_binding_rejects_wrong_environment_target_and_tls(monkeypatch):
    target = Mock()
    monkeypatch.setattr(hosted, "Database", lambda _: target)
    monkeypatch.setattr(hosted, "database_identity", lambda _: "a" * 64)
    configured = {"TABULA_RASA_ENVIRONMENT": "staging", "TABULA_RASA_DEPLOY_DATABASE_SHA256": "a" * 64,
                  "TABULA_RASA_DATABASE_URL": "postgresql://runtime@ep-fixture.neon.tech/synthetic?sslmode=require"}
    assert hosted.open_alpha_database(configured) is target
    for changes in ({"TABULA_RASA_ENVIRONMENT": "production"},
                    {"TABULA_RASA_DEPLOY_DATABASE_SHA256": "b" * 64},
                    {"TABULA_RASA_DATABASE_URL": "postgresql://runtime@ep-fixture.neon.tech/synthetic"},
                    {"TABULA_RASA_DATABASE_URL": "postgresql://runtime@ep-fixture-pooler.neon.tech/synthetic?sslmode=require"}):
        with pytest.raises(ValueError):
            hosted.open_alpha_database({**configured, **changes})
    target.dispose.assert_called_once()


def test_workflow_credential_separation_serialization_and_main_only_image_build():
    def load(name):
        return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
    config = load("commercial-promote.yml")
    job = config["jobs"]["deploy"]
    assert job["steps"][0]["with"] == {"ref": "${{ github.sha }}", "fetch-depth": "0"}
    assert any(step.get("uses") == "docker/setup-buildx-action@v3" for step in job["steps"])
    assert job["concurrency"] == {"group": "commercial-deploy-${{ inputs.environment }}", "queue": "max"}
    assert job["environment"] == "${{ inputs.environment }}"
    migration = next(step for step in job["steps"] if "bound migration" in step.get("name", ""))
    assert migration["env"] == {"TABULA_RASA_DATABASE_URL": "${{ secrets.TABULA_RASA_MIGRATION_DATABASE_URL }}"}
    assert "--bootstrap" not in migration["run"] and "database migrate" in migration["run"]
    assert "TABULA_RASA_DATABASE_URL" not in job["env"]
    image = load("commercial-image.yml")
    assert image["on"]["workflow_run"]["branches"] == ["main"]
    build = image["jobs"]["build"]
    assert "conclusion == 'success'" in build["if"] and "event == 'push'" in build["if"]
    assert build["outputs"]["image"] == "${{ steps.release.outputs.repository }}@${{ steps.build.outputs.digest }}"
    for environment in ("development", "staging"):
        caller = image["jobs"][environment]
        assert caller["needs"] == "build"
        assert caller["uses"] == "./.github/workflows/commercial-promote.yml"
        assert caller["with"] == {"environment": environment, "image": "${{ needs.build.outputs.image }}"}
    assert image["jobs"]["development"]["if"] == "vars.COMMERCIAL_DEPLOY_ENABLED == 'true'"
    assert "vars.COMMERCIAL_AUTO_STAGING_ENABLED == 'true'" in image["jobs"]["staging"]["if"]
    manual = load("commercial-staging.yml")
    assert "workflow_dispatch" in manual["on"]
    assert "github.ref == 'refs/heads/main'" in manual["jobs"]["staging"]["if"]
    assert manual["jobs"]["staging"]["uses"] == image["jobs"]["staging"]["uses"]
    assert manual["jobs"]["staging"]["with"]["rollback_from"] == "${{ inputs.rollback_from }}"
    assert "rollback_from" not in image["jobs"]["staging"]["with"]


PRODUCTION_ORIGIN = "https://production.example.test"
STAGING_DATABASE = digest(["ep-staging.neon.tech", 5432, "ledger"])
PRODUCTION_DATABASE = digest(["ep-production.neon.tech", 5432, "ledger"])
PRODUCTION_ENV = {
    **ENV, "TABULA_RASA_ENVIRONMENT": "production", "TABULA_RASA_ACCESS_MODE": "named_user",
    "TABULA_RASA_PUBLIC_ORIGIN": PRODUCTION_ORIGIN, "TABULA_RASA_ALPHA_USERNAME": "",
    "TABULA_RASA_ALPHA_PASSWORD": "", "TABULA_RASA_DEPLOY_DATABASE_SHA256": PRODUCTION_DATABASE,
    "RENDER_SERVICE_ID": "srv-production", "GH_TOKEN": "github-fixture",
    "PRODUCTION_PREVIOUS_IMAGE": PRIOR, "PRODUCTION_STAGING_RUN": "123",
    "PRODUCTION_RECOVERY_SHA256": "f" * 64, "STAGING_RENDER_SERVICE_ID": "srv-staging",
    "STAGING_RENDER_API_KEY": "staging-fixture", "PRODUCTION_OIDC_ISSUER": "https://identity.example.test",
    "PRODUCTION_OIDC_CLIENT_ID": "production-client",
}


@pytest.fixture
def production_release(monkeypatch, release_ancestry):
    proof = {**metadata(IMAGE, REPOSITORY), "image": IMAGE, "environment": "staging", "status": "smoke_passed",
             "access_mode": "named_user", "deploy_id": "dep-staging", "render_service": "srv-staging",
             "database_sha256": STAGING_DATABASE, "verified_at": datetime.now(timezone.utc).isoformat(),
             "rollback_from": None, "public_origin": ENV["TABULA_RASA_PUBLIC_ORIGIN"]}
    environ = dict(PRODUCTION_ENV)
    stage_live = {"id": "dep-staging", "status": "live", "image": {"ref": IMAGE, "sha": MANIFEST}}
    prod = provider()
    target = {"id": "srv-production", "type": "web_service", "suspended": "not_suspended",
              "autoDeployTrigger": "off", "imagePath": PRIOR,
              "serviceDetails": {"url": PRODUCTION_ORIGIN, "healthCheckPath": "/health/ready", "runtime": "image"}}
    settings = {"ENVIRONMENT": "production", "ACCESS_MODE": "named_user", "PUBLIC_ORIGIN": PRODUCTION_ORIGIN,
                "OIDC_ISSUER": PRODUCTION_ENV["PRODUCTION_OIDC_ISSUER"],
                "OIDC_CLIENT_ID": PRODUCTION_ENV["PRODUCTION_OIDC_CLIENT_ID"],
                "DATABASE_URL": "postgresql://runtime:fixture@ep-production.neon.tech/ledger?sslmode=require"}

    stage_settings = {"DATABASE_URL": "postgresql://stage:fixture@ep-staging.neon.tech/ledger?sslmode=require"}

    def render(path, token, *, body=None):
        if token == "staging-fixture":
            if path.endswith("/env-vars/TABULA_RASA_DATABASE_URL"):
                return {"key": "TABULA_RASA_DATABASE_URL", "value": stage_settings["DATABASE_URL"]}
            if path == "/services/srv-staging":
                return {"id": "srv-staging", "type": "web_service",
                        "serviceDetails": {"url": ENV["TABULA_RASA_PUBLIC_ORIGIN"]}}
            assert path == "/services/srv-staging/deploys?limit=100" and body is None
            return [{"deploy": stage_live}]
        if path == "/services/srv-production":
            return target
        if "/env-vars/" in path:
            key = path.rsplit("/", 1)[1]
            return {"key": key, "value": settings[key.removeprefix("TABULA_RASA_")]}
        return prod(path, token, body=body)

    run = {"path": ".github/workflows/commercial-image.yml", "event": "workflow_run",
           "head_branch": "main", "head_repository": {"full_name": ENV["GITHUB_REPOSITORY"]},
           "status": "completed", "run_attempt": 1}
    jobs = [{"name": name, "conclusion": "success"} for name in ("build", "staging / deploy")]

    def github(path, token, *, github):
        assert github and token == "github-fixture"
        return {"jobs": jobs} if "/attempts/1/jobs?" in path else run

    def command(args, **_):
        if args[0] == "gh":
            assert args[2:4] == ["download", "123"] and args[7] == "staging-release-1"
            (Path(args[-1]) / "release.json").write_text(json.dumps(proof), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    release_ancestry.side_effect = command
    original = production.staging_evidence
    monkeypatch.setattr(production, "staging_evidence",
                        lambda *args, **kwargs: original(*args, **kwargs, github=github))
    pre_smoke, smoke = Mock(), Mock()
    monkeypatch.setattr(smoke_module, "named_smoke", pre_smoke)

    def promote():
        return release.promote(IMAGE, environ, read=render, metadata=metadata, verify=Mock(), smoke_check=smoke)

    return SimpleNamespace(promote=promote, prod=prod, proof=proof, stage_live=stage_live,
                           target=target, settings=settings, stage_settings=stage_settings, environ=environ, run=run, jobs=jobs,
                           smoke=smoke, pre_smoke=pre_smoke)


def test_production_submits_only_the_live_staging_digest_and_records_success(production_release):
    fixture = production_release
    result = fixture.promote()
    assert result["image"] == IMAGE and result["previous_image"] == PRIOR
    assert result["staging_run"] == "123" and result["staging_deploy_id"] == "dep-staging"
    assert result["schema"] == "0001_initial" and result["status"] == "smoke_passed"
    assert result["render_url"] == "https://dashboard.render.com/web/srv-production"
    assert [call.kwargs["body"] for call in fixture.prod.call_args_list if call.kwargs.get("body")] == [
        {"imageUrl": IMAGE},
    ]
    fixture.pre_smoke.assert_called_once_with(PRODUCTION_ORIGIN, "d" * 40)
    fixture.smoke.assert_called_once_with(PRODUCTION_ORIGIN, "c" * 40)


@pytest.mark.parametrize(("section", "key", "value"), [
    ("proof", "image", PRIOR),
    ("proof", "release", "d" * 40),
    ("proof", "manifest_digest", PRIOR_MANIFEST),
    ("proof", "access_mode", "restricted_alpha"),
    ("proof", "verified_at", (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()),
    ("proof", "database_sha256", PRODUCTION_DATABASE),
    ("proof", "public_origin", "http://stage.example.test"),
    ("stage_live", "id", "dep-replaced"),
    ("stage_live", "image", {"ref": PRIOR, "sha": PRIOR_MANIFEST}),
    ("environ", "PRODUCTION_RECOVERY_SHA256", ""),
    ("environ", "PRODUCTION_PREVIOUS_IMAGE", IMAGE),
    ("target", "autoDeployTrigger", "commit"),
    ("settings", "ACCESS_MODE", "restricted_alpha"),
    ("settings", "OIDC_CLIENT_ID", "staging-client"),
    ("settings", "DATABASE_URL", "postgresql://runtime:fixture@ep-staging.neon.tech/ledger?sslmode=require"),
    ("stage_settings", "DATABASE_URL", "postgresql://runtime:fixture@ep-production.neon.tech/ledger?sslmode=require"),
    ("run", "path", ".github/workflows/commercial-staging.yml"),
])
def test_production_rejects_invalid_evidence_and_target_before_mutation(production_release, section, key, value):
    fixture = production_release
    getattr(fixture, section)[key] = value
    with pytest.raises(release.ReleaseError):
        fixture.promote()
    assert not any(call.kwargs.get("body") for call in fixture.prod.call_args_list)
    fixture.smoke.assert_not_called()


@pytest.mark.parametrize(("section", "field", "diagnostic"), [
    ("run", "head_repository", "completed main release workflow"),
    ("proof", "database_sha256", "isolated staging target"),
    ("proof", "deploy_id", "isolated staging target"),
    ("proof", "public_origin", "HTTPS origin is missing or malformed"),
])
def test_null_staging_evidence_has_an_actionable_diagnostic(production_release, section, field, diagnostic):
    getattr(production_release, section)[field] = None
    with pytest.raises(release.ReleaseError, match=diagnostic):
        production_release.promote()
    assert not any(call.kwargs.get("body") for call in production_release.prod.call_args_list)


def test_production_requires_successful_build_and_staging_jobs(production_release):
    fixture = production_release
    fixture.jobs[0]["conclusion"] = "failure"
    with pytest.raises(release.ReleaseError, match="Successful build and staging"):
        fixture.promote()
    assert not any(call.kwargs.get("body") for call in fixture.prod.call_args_list)


def test_failed_production_smoke_keeps_failed_outcome_without_automatic_rollback(production_release, capsys):
    fixture = production_release
    fixture.smoke.side_effect = ValueError("synthetic smoke failure")
    with pytest.raises(release.ReleaseError, match="Named smoke failed"):
        fixture.promote()
    assert [call.kwargs["body"] for call in fixture.prod.call_args_list if call.kwargs.get("body")] == [
        {"imageUrl": IMAGE},
    ]
    assert json.loads(capsys.readouterr().err)["deploy_id"] == "dep-new"


def test_production_database_gate_uses_bound_credential_and_locked_compatible_upgrade(monkeypatch):
    target = MagicMock()
    target.engine.connect.return_value.__enter__.return_value.scalar.return_value = True
    target.health.return_value = {"connected": True, "revision": database.head_revision()}
    binding, upgrade = Mock(return_value=target), Mock()
    monkeypatch.setattr(database, "open_bound_neon_database", binding)
    monkeypatch.setattr(database, "upgrade", upgrade)
    database.migrate(PRODUCTION_ENV)
    binding.assert_called_once_with(PRODUCTION_ENV)
    upgrade.assert_called_once_with(target, require_current_head=True)
    target.dispose.assert_called_once()
    upgrade.reset_mock()
    target.engine.connect.return_value.__enter__.return_value.scalar.return_value = False
    with pytest.raises(ValueError, match="migration credential"):
        database.migrate(PRODUCTION_ENV)
    upgrade.assert_not_called()
    with pytest.raises(ValueError, match="existing compatible schema"):
        database.migrate(PRODUCTION_ENV, bootstrap=True)


def test_production_dispatch_is_manual_main_only_and_reuses_staging_promotion_lock():
    def load(name):
        return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)

    dispatch = load("commercial-production.yml")
    assert set(dispatch["on"]) == {"workflow_dispatch"}
    assert set(dispatch["on"]["workflow_dispatch"]["inputs"]) == {
        "image", "staging_run", "previous_image", "recovery_sha256",
    }
    job = dispatch["jobs"]["production"]
    assert "refs/heads/main" in job["if"] and "COMMERCIAL_PRODUCTION_ENABLED" in job["if"]
    assert job["uses"] == "./.github/workflows/commercial-promote.yml"
    shared = load("commercial-promote.yml")["jobs"]["deploy"]
    assert "github.event_name == 'workflow_dispatch'" in shared["if"]
    assert "commercial-production.yml@refs/heads/main" in shared["if"]
    names = [step.get("name", "") for step in shared["steps"]]
    assert names.index("Verify tested main image and metadata") < next(
        index for index, name in enumerate(names) if "bound migration" in name
    ) < next(index for index, name in enumerate(names) if "Deploy and verify named-user" in name)
    assert "production" not in load("commercial-image.yml")["jobs"]


def test_production_uses_verified_release_code_before_migration_and_deploy():
    workflow = yaml.load((ROOT / ".github/workflows/commercial-promote.yml").read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["deploy"]["steps"]
    names = [step.get("name", "") for step in steps]
    ordered = [
        "Verify tested main image and metadata",
        "Select verified production release code",
        "Check out tested production release code",
        "Recheck production with tested release code",
        "Explicit bound migration (separate credential and process)",
        "Deploy and verify named-user boundary (operator rollback on failure)",
    ]
    assert [names.index(name) for name in ordered] == sorted(names.index(name) for name in ordered)
    select, checkout, recheck = [steps[names.index(name)] for name in ordered[1:4]]
    for step in (select, checkout, recheck):
        assert step["if"] == "inputs.environment == 'production'"
        assert "continue-on-error" not in step
    assert select["id"] == "tested-release"
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"] == {"ref": "${{ steps.tested-release.outputs.revision }}", "fetch-depth": "0"}
    assert recheck["run"].index("pip install -r requirements-postgres.txt") < recheck["run"].index(
        "tabula_rasa.deployment.release verify"
    )
    assert '--image "$IMAGE"' in recheck["run"]


@pytest.mark.parametrize("revision", ["a" * 40, None, "main", "a" * 40 + "\nother=main"])
def test_workflow_selects_only_a_full_verified_commit(revision, tmp_path, monkeypatch):
    workflow = yaml.load((ROOT / ".github/workflows/commercial-promote.yml").read_text(), Loader=yaml.BaseLoader)
    select = next(step for step in workflow["jobs"]["deploy"]["steps"] if step.get("id") == "tested-release")
    script = select["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "preflight.json").write_text(json.dumps({"release": revision}), encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    if revision == "a" * 40:
        exec(compile(script, "production-release-selection", "exec"), {})
        assert output.read_text(encoding="utf-8") == f"revision={revision}\n"
    else:
        with pytest.raises(SystemExit, match="verified release commit"):
            exec(compile(script, "production-release-selection", "exec"), {})
        assert not output.exists()
