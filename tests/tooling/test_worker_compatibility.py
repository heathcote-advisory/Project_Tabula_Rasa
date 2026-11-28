from copy import deepcopy
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tabula_rasa.deployment import qualify_worker, release, worker_compatibility as compatibility
from tabula_rasa.infrastructure.postgres import config, runtime


ENV = {"NAS_WORKER_IMAGE": "ghcr.io/example/installed-worker@sha256:" + "a" * 64,
       "NAS_WORKER_CONFIG_SHA256": "b" * 64, "NAS_WORKER_ROOTFS_SHA256": "c" * 64}
CANDIDATE = {"image": "ghcr.io/example/candidate@sha256:" + "d" * 64,
             "release": "e" * 40, "schema": "target", "manifest_digest": "sha256:" + "f" * 64}


def proof():
    return {"version": 1, "worker": compatibility.identity(ENV), "candidate": CANDIDATE,
            "run_id": "123", "run_attempt": 2, "status": "passed",
            "results": [{"schema": schema, "success": True, "checks": compatibility.CHECKS}
                        for schema in ("source", "target")]}


def verify(value, environ=ENV):
    return compatibility.verify(value, CANDIDATE, environ, run_id="123", attempt=2, schemas=["source", "target"])


def test_compatibility_binds_both_schemas_to_candidate_worker_and_attempt():
    assert verify(proof())["status"] == "passed"


@pytest.mark.parametrize("field,value", [
    ("worker", {}), ("candidate", {**CANDIDATE, "image": "changed"}),
    ("run_id", "124"), ("run_attempt", 1), ("status", "failed"), ("version", 0),
    ("results", []), ("results", None),
])
def test_missing_failed_or_mismatched_proof_is_rejected(field, value):
    with pytest.raises(release.ReleaseError):
        verify({**proof(), field: value})
    with pytest.raises(release.ReleaseError):
        verify(None)


@pytest.mark.parametrize("change", ["missing_source", "missing_check", "failed", "duplicate"])
def test_probe_results_require_complete_success_and_source_coverage(change):
    value = deepcopy(proof())
    if change == "missing_source":
        value["results"].pop(0)
    elif change == "missing_check":
        value["results"][0]["checks"].pop()
    elif change == "failed":
        value["results"][0]["success"] = False
    else:
        value["results"].append(value["results"][0])
    with pytest.raises(release.ReleaseError):
        verify(value)


@pytest.mark.parametrize("key", list(ENV))
def test_installed_worker_upgrade_invalidates_previous_evidence(key):
    with pytest.raises(release.ReleaseError):
        verify(proof(), {**ENV, key: ENV[key].replace("a", "9").replace("b", "9").replace("c", "9")})


def test_registry_worker_content_must_match_installed_export():
    metadata = {"Os": "linux", "Architecture": "arm64", "Config": {"Env": ["PATH=/bin"]},
                "RootFS": {"Layers": ["sha256:" + "a" * 64]}}
    expected = {**compatibility.identity(ENV), "config_sha256": compatibility.fingerprint(metadata["Config"]),
                "rootfs_sha256": compatibility.fingerprint(metadata["RootFS"]["Layers"])}
    compatibility.verify_image(metadata, expected)
    for key, value in [("Architecture", "amd64"), ("Config", {}), ("RootFS", {})]:
        with pytest.raises(release.ReleaseError, match="installed NAS export"):
            compatibility.verify_image({**metadata, key: value}, expected)


@pytest.mark.parametrize("value", ["", "ghcr.io/example/worker:latest", "sha256:" + "a" * 64])
def test_worker_identity_requires_a_registry_digest(value):
    with pytest.raises(release.ReleaseError, match="registry digest"):
        compatibility.identity({**ENV, "NAS_WORKER_IMAGE": value})


def test_failed_qualification_removes_stale_proof_and_sanitizes_errors(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "worker-compatibility.json"
    output.write_text(json.dumps(proof()), encoding="utf-8")
    monkeypatch.setattr(qualify_worker, "run", Mock(side_effect=RuntimeError("private diagnostic")))
    with pytest.raises(SystemExit) as error:
        qualify_worker.main()
    assert "private diagnostic" not in str(error.value)
    assert not output.exists()


@pytest.mark.parametrize("script", ["prepare", "probe"])
@pytest.mark.parametrize("explicit_url", [False, True])
def test_probe_scripts_never_fall_back_to_workstation_settings(monkeypatch, tmp_path, script, explicit_url):
    local = tmp_path / ".env.local"
    local.write_text("TABULA_RASA_DATABASE_URL=postgresql://localhost/workstation\n"
                     "TABULA_RASA_DATABASE_POOL_SIZE=2\n", encoding="utf-8")
    monkeypatch.setattr(config, "_LOCAL_ENV_PATH", local)
    monkeypatch.delenv("TABULA_RASA_DATABASE_POOL_SIZE", raising=False)
    if explicit_url:
        monkeypatch.setenv("TABULA_RASA_DATABASE_URL", "postgresql://localhost/disposable")
    else:
        monkeypatch.delenv("TABULA_RASA_DATABASE_URL", raising=False)
    captured = []

    class Configured(Exception):
        pass

    def database(settings):
        captured.append(settings)
        raise Configured

    monkeypatch.setattr(runtime, "Database", database)

    def invoke():
        path = Path(__file__).with_name(f"worker_compatibility_{script}.py")
        if script == "prepare":
            runpy.run_path(str(path))["prepare"]("synthetic_schema")
        else:
            runpy.run_path(str(path))

    if explicit_url:
        with pytest.raises(Configured):
            invoke()
        assert captured[0].url.database == "disposable"
        assert captured[0].pool_size == 5
    else:
        with pytest.raises(config.DatabaseConfigurationError, match="Set TABULA_RASA_DATABASE_URL"):
            invoke()
        assert captured == []


@pytest.mark.parametrize("failure", [None, "setup", "probe"])
@pytest.mark.parametrize("source", ["source", "target"])
def test_disposable_runner_isolates_images_and_cleans_up_on_failure(monkeypatch, failure, source):
    environ = {**ENV, "IMAGE": CANDIDATE["image"], "GITHUB_REPOSITORY": "example/repo",
               "PRODUCTION_SCHEMA": source, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
    monkeypatch.setattr(release, "image_metadata", lambda *_: {k: v for k, v in CANDIDATE.items() if k != "image"})
    monkeypatch.setattr(qualify_worker.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=CANDIDATE["release"])))
    path = [] if source == "target" else [SimpleNamespace(revision=s) for s in ("intermediate", "target")]
    rollout_path = Mock(return_value=path)
    monkeypatch.setattr(qualify_worker.schema_migration, "rollout_path", rollout_path)
    monkeypatch.setattr(compatibility, "verify_image", Mock())
    expected_results = [row for row in proof()["results"] if row["schema"] in {source, "target"}]
    schemas = iter(row["schema"] for row in expected_results)
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(stdout="[{}]", returncode=0)
        if args[0] == "run" and "--entrypoint" in args:
            is_probe = args[-1] == "/tmp/probe.py"
            if failure == ("probe" if is_probe else "setup"):
                raise release.ReleaseError("Synthetic failure")
            if is_probe:
                return SimpleNamespace(stdout=json.dumps({"schema": next(schemas), "success": True,
                                                          "checks": compatibility.CHECKS}), returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(qualify_worker, "docker", docker)
    if failure:
        with pytest.raises(release.ReleaseError):
            qualify_worker.run(environ)
    else:
        assert qualify_worker.run(environ)["results"] == expected_results
        assert [call[-1] for call in calls if "/fixtures/tests/tooling/worker_compatibility_prepare.py" in call] == [
            row["schema"] for row in expected_results
        ]
    rollout_path.assert_called_once_with(source, "target")
    assert calls[-1][:2] == ("network", "rm")
    assert any(call[:3] == ("network", "create", "--internal") for call in calls)
    for call in calls:
        if call[0] == "run":
            assert "-p" not in call and "--publish" not in call
            if "--entrypoint" in call:
                assert "--read-only" in call and "no-new-privileges" in call
                assert "--cap-drop" in call and "ALL" in call
                assert all("production" not in item for item in call)


@pytest.mark.parametrize("source", ["", "unsupported"])
def test_qualification_stops_before_docker_without_a_supported_source(monkeypatch, source):
    environ = {**ENV, "IMAGE": CANDIDATE["image"], "GITHUB_REPOSITORY": "example/repo", "PRODUCTION_SCHEMA": source}
    monkeypatch.setattr(release, "image_metadata", lambda *_: CANDIDATE)
    monkeypatch.setattr(qualify_worker.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=CANDIDATE["release"])))
    docker = Mock()
    monkeypatch.setattr(qualify_worker, "docker", docker)
    error = qualify_worker.schema_migration.ContractError if source else release.ReleaseError
    with pytest.raises(error):
        qualify_worker.run(environ)
    docker.assert_not_called()
