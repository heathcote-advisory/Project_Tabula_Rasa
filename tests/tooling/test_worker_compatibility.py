from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tabula_rasa.deployment import qualify_worker, release, worker_compatibility as compatibility


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


@pytest.mark.parametrize("failure", [None, "setup", "probe"])
def test_disposable_runner_isolates_images_and_cleans_up_on_failure(monkeypatch, failure):
    environ = {**ENV, "IMAGE": CANDIDATE["image"], "GITHUB_REPOSITORY": "example/repo",
               "NAS_WORKER_BASE_SCHEMA": "source", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
    monkeypatch.setattr(release, "image_metadata", lambda *_: {k: v for k, v in CANDIDATE.items() if k != "image"})
    monkeypatch.setattr(qualify_worker.subprocess, "run", Mock(return_value=SimpleNamespace(stdout=CANDIDATE["release"])))
    monkeypatch.setattr(qualify_worker.schema_migration, "rollout_path", lambda *_: [SimpleNamespace(revision="target")])
    monkeypatch.setattr(compatibility, "verify_image", Mock())
    schemas = iter(["source", "target"])
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
        assert qualify_worker.run(environ)["results"] == proof()["results"]
    assert calls[-1][:2] == ("network", "rm")
    assert any(call[:3] == ("network", "create", "--internal") for call in calls)
    for call in calls:
        if call[0] == "run":
            assert "-p" not in call and "--publish" not in call
            if "--entrypoint" in call:
                assert "--read-only" in call and "no-new-privileges" in call
                assert "--cap-drop" in call and "ALL" in call
                assert all("production" not in item for item in call)
