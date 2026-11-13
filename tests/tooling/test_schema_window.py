import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest
import yaml

from tabula_rasa.deployment import release, schema_window
from tabula_rasa.migration.model import digest

REPOSITORY = "owner/repo"
REGISTRY = release.commercial_image_repository(REPOSITORY)
IMAGE = REGISTRY + "@sha256:" + "a" * 64
PRIOR_IMAGE = REGISTRY + "@sha256:" + "b" * 64
CANDIDATE = {"release": "a" * 40, "schema": "0021_customer_lifecycle", "manifest_digest": IMAGE.split("@")[1]}
PRIOR = {"release": "b" * 40, "schema": "0020_book_currency", "manifest_digest": PRIOR_IMAGE.split("@")[1]}
NOW = datetime(2026, 11, 1, 8, 5, tzinfo=timezone.utc)
ORIGIN = "https://stage.example.test"
PRIVATE = "https://stage.private.test"
SERVICE = "srv-stage"
DATABASE = digest(["ep-stage.neon.tech", 5432, "ledger"])
PLAN = {
    "date": "2026-11-01", "minutes": 40, "image": IMAGE, "previous_image": PRIOR_IMAGE,
    "recovery_sha256": "c" * 64, "checkpoint_at": "2026-11-01T07:45:00+00:00", "writers_paused": True,
    "render_service": SERVICE, "database_sha256": DATABASE,
}
ENV = {
    "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_RUN_ATTEMPT": "1", "TABULA_RASA_ENVIRONMENT": "staging",
    "TABULA_RASA_ACCESS_MODE": "named_user", "TABULA_RASA_PUBLIC_ORIGIN": ORIGIN,
    "WINDOW_PRIVATE_ORIGIN": PRIVATE, "RENDER_SERVICE_ID": SERVICE, "RENDER_API_KEY": "fixture",
    "TABULA_RASA_DEPLOY_DATABASE_SHA256": DATABASE, "COMMERCIAL_SCHEMA_WINDOW": json.dumps(PLAN),
}


def at(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


@pytest.mark.parametrize(("day", "early", "first", "late"), [
    ("2026-03-08", "06:59", "07:00", "07:16"),
    ("2026-07-15", "06:59", "07:00", "07:16"),
    ("2026-11-01", "07:59", "08:00", "08:16"),
])
def test_eastern_window_tracks_dst_and_defers_late_runs(day, early, first, late):
    plan = {**PLAN, "date": day, "checkpoint_at": f"{day}T04:00:00+00:00"}
    environ = {**ENV, "COMMERCIAL_SCHEMA_WINDOW": json.dumps(plan)}
    assert schema_window.approval(environ, at(f"{day}T{early}:00+00:00"))[0] is None
    assert schema_window.approval(environ, at(f"{day}T{first}:00+00:00"))[0] == plan
    assert schema_window.approval(environ, at(f"{day}T{late}:00+00:00"))[0] is None


@pytest.mark.parametrize("changes", [{"COMMERCIAL_SCHEMA_WINDOW": ""}, {"GITHUB_RUN_ATTEMPT": "2"}])
def test_empty_queue_and_rerun_do_not_contact_providers(changes):
    read, metadata = Mock(), Mock()
    assert schema_window.run({**ENV, **changes}, read=read, metadata=metadata, now=lambda: NOW)["status"] == "skipped"
    read.assert_not_called()
    metadata.assert_not_called()


class Staging:
    def __init__(self, image=IMAGE, prior_image=PRIOR_IMAGE):
        self.image = image
        self.prior_image = prior_image
        self.actions = []
        self.closed_origins = []
        self.maintenance = False
        self.suspended = False
        self.live_image = prior_image
        self.configured_image = prior_image
        self.schema = PRIOR["schema"]
        self.candidate = CANDIDATE.copy()
        self.failure = None
        self.details = {
            "url": ORIGIN, "runtime": "image", "plan": "starter", "healthCheckPath": "/health/ready",
        }
        self.settings = {
            "ENVIRONMENT": "staging", "ACCESS_MODE": "named_user", "PUBLIC_ORIGIN": ORIGIN,
            "DATABASE_URL": "postgresql://runtime:fixture@ep-stage.neon.tech/ledger?sslmode=require",
        }
        self.emit = Mock()

    def deploy(self):
        return {"id": "dep-candidate" if self.live_image == self.image else "dep-prior", "status": "live",
                "image": {"ref": self.live_image, "sha": self.live_image.split("@")[1]}}

    def read(self, path, token, *, body=None, method=None, no_content=False):
        assert token == "fixture"
        base = f"/services/{SERVICE}"
        if method == "PATCH":
            if "image" in body:
                assert self.suspended and self.maintenance
                assert body == {"image": {
                    "imagePath": self.image, "ownerId": "tea-owner", "registryCredentialId": "rgc-private",
                }}
                self.actions.append("configure")
                if self.failure != "configure":
                    self.configured_image = self.image
            else:
                self.maintenance = body["serviceDetails"]["maintenanceMode"]["enabled"]
                self.actions.append("close" if self.maintenance else "open")
        elif path == base + "/suspend":
            assert self.maintenance
            self.actions.append("suspend")
            self.suspended = True
        elif path == base + "/resume":
            assert self.maintenance and self.schema == self.candidate["schema"]
            assert self.configured_image == self.image
            self.actions.append("resume")
            self.suspended = False
            self.live_image = self.image
        elif path == base:
            return {
                "id": SERVICE, "type": "web_service", "autoDeployTrigger": "off",
                "imagePath": self.configured_image, "ownerId": "tea-owner", "registryCredential": {"id": "rgc-private"},
                "suspended": "suspended" if self.suspended else "not_suspended",
                "serviceDetails": {**self.details, "maintenanceMode": {"enabled": self.maintenance, "uri": ""}},
            }
        elif path.startswith(base + "/env-vars/TABULA_RASA_"):
            key = path.rsplit("/", 1)[1]
            return {"key": key, "value": self.settings[key.removeprefix("TABULA_RASA_")]}
        elif path == base + "/custom-domains/stage.example.test":
            return {"name": "stage.example.test", "verificationStatus": "verified"}
        elif path == base + "/jobs?status=pending,running&limit=1":
            return [{}] if self.failure == "jobs" or (self.failure == "drain" and self.suspended) else []
        elif path == base + "/instances":
            assert self.suspended
            return []
        elif path == base + "/deploys?limit=100":
            assert body is None
            deploy = self.deploy()
            if self.live_image == self.image and not getattr(self, "deploy_finished", False):
                deploy.update(trigger="service_resumed", status="update_in_progress")
            return [{"deploy": deploy}]
        elif path == base + "/deploys/dep-candidate":
            self.actions.append("deploy")
            deploy = self.deploy()
            if self.failure == "deploy":
                deploy["status"] = "update_failed"
            elif self.failure == "old_image_resumed":
                deploy["image"] = {"ref": self.prior_image, "sha": self.prior_image.split("@")[1]}
            self.deploy_finished = True
            return deploy
        else:
            raise AssertionError(path)

    def db(self, image, candidate, source, role, environ, *, apply):
        if candidate["schema"] != CANDIDATE["schema"]:
            raise release.ReleaseError("The rollout contract is missing.")
        assert image == IMAGE and candidate == CANDIDATE and role == "runtime"
        if source != self.schema or self.failure == "database":
            raise release.ReleaseError("Wrong database revision.")
        self.actions.append("upgrade" if apply else "inspect")
        if apply:
            assert self.suspended and self.maintenance
            if self.failure == "migration":
                raise release.ReleaseError("Migration failed.")
            self.schema = candidate["schema"]

    def smoke(self, origin, revision):
        assert origin == PRIVATE
        if revision == PRIOR["release"]:
            self.actions.append("smoke_old")
        else:
            assert revision == CANDIDATE["release"] and self.maintenance
            self.actions.append("smoke_new")
            if self.failure == "smoke":
                raise RuntimeError("browser failed")

    def closed(self, origin):
        assert self.maintenance
        self.closed_origins.append(origin)

    def reopened(self, origin, revision):
        assert origin == ORIGIN and revision == CANDIDATE["release"] and not self.maintenance

    def run(self, *, environ=ENV, now=lambda: NOW):
        return schema_window.run(
            environ, read=self.read, metadata=lambda image, _: self.candidate if image == IMAGE else PRIOR,
            verify=Mock(), check_database=self.db, check_closed=self.closed, check_reopened=self.reopened,
            smoke_check=self.smoke, now=now, emit=self.emit,
        )


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setattr(release.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    return Staging()


def test_window_uses_one_migration_and_reopens_only_after_verified_deploy(staging):
    result = staging.run()
    assert staging.actions == [
        "inspect", "smoke_old", "close", "suspend", "configure", "upgrade", "resume", "deploy", "smoke_new", "open",
    ]
    assert result["status"] == "smoke_passed"
    assert result["image"] == IMAGE and result["source_schema"] == PRIOR["schema"]
    assert result["deploy_id"] == "dep-candidate" and result["traffic_restored_at"]


@pytest.mark.parametrize("policy", ["enabled", "disabled"])
def test_checks_the_direct_render_url_only_when_enabled(staging, policy):
    direct = "https://stage.onrender.com"
    staging.details.update(url=direct, renderSubdomainPolicy=policy)
    assert staging.run()["status"] == "smoke_passed"
    assert direct in staging.closed_origins if policy == "enabled" else direct not in staging.closed_origins


def test_public_ingress_waits_for_maintenance_and_stops_when_unconfirmed(monkeypatch):
    maintenance = HTTPError(ORIGIN, 503, "maintenance", {}, None)
    opener = Mock()
    monkeypatch.setattr(schema_window, "build_opener", lambda _: opener)
    opener.open.side_effect = [URLError("reset"), maintenance]
    delay = Mock()
    schema_window.public_closed(ORIGIN, sleep=delay)
    delay.assert_called_once_with(3)
    opener.open.reset_mock()
    opener.open.side_effect = URLError("still unavailable")
    with pytest.raises(release.ReleaseError, match="did not return maintenance status"):
        schema_window.public_closed(ORIGIN, sleep=delay)
    assert opener.open.call_count == 3


def test_drain_honors_the_configured_grace_period():
    state = {
        "id": SERVICE, "suspended": "suspended",
        "serviceDetails": {"maintenanceMode": {"enabled": True, "uri": ""}},
    }
    observations = iter([[{"id": "still-running"}]] * 24 + [[]])

    def read(path, token):
        return next(observations) if path.endswith("/instances") else state

    delay = Mock()
    schema_window.wait_drained(read, SERVICE, "fixture", 120, sleep=delay)
    assert delay.call_count == 24


@pytest.mark.parametrize(("failure", "last_action"), [
    ("drain", "suspend"), ("configure", "configure"), ("migration", "upgrade"), ("deploy", "deploy"),
    ("old_image_resumed", "deploy"), ("smoke", "smoke_new"),
])
def test_failure_never_reopens_or_retries_after_ingress_restriction(staging, failure, last_action):
    staging.failure = failure
    with pytest.raises(release.ReleaseError, match="Maintenance stopped"):
        staging.run()
    assert staging.actions[-1] == last_action and staging.maintenance
    assert "open" not in staging.actions
    assert staging.emit.call_args.args[0]["status"] == "maintenance_failed"


@pytest.mark.parametrize("failure", ["jobs", "database", "wrong_target", "wrong_database", "unknown", "stale", "stale_plan"])
def test_preflight_refuses_invalid_inputs_without_downtime(staging, monkeypatch, failure):
    staging.failure = failure
    environ = ENV.copy()
    if failure == "wrong_target":
        environ["RENDER_SERVICE_ID"] = "srv-other"
    elif failure == "wrong_database":
        staging.settings["DATABASE_URL"] = staging.settings["DATABASE_URL"].replace("ep-stage", "ep-other")
    elif failure == "unknown":
        staging.candidate["schema"] = "0030_unknown"
    elif failure == "stale":
        monkeypatch.setattr(release.subprocess, "run", Mock(return_value=Mock(returncode=1)))
    elif failure == "stale_plan":
        environ["COMMERCIAL_SCHEMA_WINDOW"] = json.dumps({**PLAN, "checkpoint_at": "2026-10-29T07:45:00+00:00"})
    with pytest.raises(release.ReleaseError):
        staging.run(environ=environ)
    assert not staging.actions and not staging.maintenance


def test_compatible_release_and_preflight_delay_skip_without_downtime(staging):
    staging.candidate["schema"] = PRIOR["schema"]
    assert staging.run()["reason"] == "schema_compatible_use_normal_promotion"
    assert not staging.actions
    staging.candidate["schema"] = CANDIDATE["schema"]
    now = Mock(side_effect=[NOW, NOW, at("2026-11-01T08:16:00+00:00")])
    assert staging.run(now=now)["reason"] == "preflight_exhausted_safe_start_budget"
    assert staging.actions == ["inspect", "smoke_old"]


def test_production_requires_explicit_run_and_matching_staging_proof(staging, monkeypatch):
    from tabula_rasa.deployment import production

    staging.settings["ENVIRONMENT"] = "production"
    environ = {**ENV, "TABULA_RASA_ENVIRONMENT": "production"}
    with pytest.raises(release.ReleaseError, match="approval"):
        staging.run(environ=environ)
    proof = Mock(side_effect=release.ReleaseError("Staging evidence does not match."))
    monkeypatch.setattr(production, "staging_evidence", proof)
    environ["COMMERCIAL_SCHEMA_WINDOW"] = json.dumps({**PLAN, "staging_run": "123"})
    with pytest.raises(release.ReleaseError, match="Staging evidence does not match"):
        staging.run(environ=environ)
    assert proof.call_args.args[0] == IMAGE
    assert proof.call_args.args[2]["PRODUCTION_STAGING_RUN"] == "123"
    assert not staging.actions


def test_immediate_staging_and_scheduled_production_use_the_shared_environment_queue():
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    schedule = yaml.load((workflows / "commercial-schema-window.yml").read_text(), Loader=yaml.BaseLoader)
    shared = yaml.load((workflows / "commercial-promote.yml").read_text(), Loader=yaml.BaseLoader)["jobs"]
    assert schedule["on"] == {"schedule": [{"cron": "0 3 * * *", "timezone": "America/New_York"}]}
    assert set(schedule["jobs"]) == {"production"}
    assert schedule["jobs"]["production"]["uses"] == "./.github/workflows/commercial-promote.yml"
    assert schedule["jobs"]["production"]["with"] == {"environment": "production", "automatic": "true"}
    assert "COMMERCIAL_AUTO_PRODUCTION_ENABLED" in schedule["jobs"]["production"]["if"]
    assert set(shared) == {"deploy"}
    assert shared["deploy"]["concurrency"]["group"] == "commercial-deploy-${{ inputs.environment }}"
    assert "github.event_name == 'schedule'" in shared["deploy"]["if"]
