"""The required CI gate preserves suite coverage and rejects incomplete results."""

from __future__ import annotations

import os
import json
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def workflow(filename: str = "ci.yml") -> dict:
    # BaseLoader preserves the Actions `on` key instead of YAML 1.1's boolean.
    return yaml.load(
        (PROJECT_ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_ci_runs_for_current_pr_candidates_without_feature_push_duplicates() -> None:
    config = workflow()
    assert config["on"]["pull_request"]["branches"] == ["main"]
    assert set(config["on"]["pull_request"]["types"]) == {
        "opened", "synchronize", "reopened", "ready_for_review",
    }
    assert "paths" not in config["on"]["pull_request"]
    assert config["on"]["push"]["branches"] == ["main"]
    assert config["on"]["merge_group"]["types"] == ["checks_requested"]
    assert config["permissions"] == {"contents": "read"}
    assert config["concurrency"]["cancel-in-progress"] == "true"
    assert "github.event.pull_request.number" in config["concurrency"]["group"]


def test_ci_preserves_complete_python_and_windows_coverage() -> None:
    jobs = workflow()["jobs"]
    python = jobs["python-suite"]
    assert "if" not in python
    assert python["runs-on"].startswith("ubuntu-")
    assert "postgres" in python["services"]
    assert python["env"]["TABULA_RASA_TEST_DATABASE_URL"].startswith(
        "postgresql+psycopg://tabula_test@127.0.0.1:"
    )
    suite = next(step for step in python['steps'] if step.get('name') == 'Run complete Python suite')
    assert suite['env']['TABULA_RASA_TEST_PG_CONTAINER'] == '${{ job.services.postgres.id }}'
    commands = [step["run"] for step in python["steps"] if "run" in step]
    assert "python -m pip install -r requirements-dev.txt" in commands
    assert "ruff check ." in commands
    assert "docker build -f deploy/nas/Dockerfile -t tabula-nas-backup:test ." in commands
    nas = next(step for step in python["steps"] if step.get("name") ==
               "Verify synthetic NAS TLS backup and isolated restore")
    assert "tests/tooling/nas_backup_smoke.py" in nas["run"]
    assert "tabula-nas-backup:test /smoke.py" in nas["run"]
    pytest_commands = [shlex.split(command) for command in commands
                       if command.startswith("python -m pytest")]
    assert len(pytest_commands) == 1
    # No positional test selection, marker filter, keyword filter or deselection.
    assert pytest_commands[0][3:] == ["-ra", "--durations=20", "--junitxml=python-results.xml"]

    windows = jobs["windows-tooling"]
    assert windows["runs-on"].startswith("windows-")
    assert "if" not in windows
    setup = next(step for step in windows["steps"]
                 if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "3.12"
    assert any(step.get("run", "").startswith(
        "python -m pytest tests/tooling/test_codex_environment_setup.py "
    ) for step in windows["steps"])


def test_native_browser_shards_preserve_complete_disjoint_coverage() -> None:
    reusable = workflow("native-browser.yml")
    assert reusable["on"] == {"workflow_call": {"inputs": {
        "shard": {"required": "true", "type": "string"},
        "tests": {"required": "true", "type": "string"},
    }}}
    assert set(reusable["jobs"]) == {"test"}
    native_job = reusable["jobs"]["test"]
    assert "continue-on-error" not in native_job
    assert all("continue-on-error" not in step for step in native_job["steps"])
    assert "if" not in native_job
    assert "postgres" in native_job["services"]
    assert native_job["env"]["TABULA_RASA_TEST_DATABASE_URL"].startswith("postgresql+psycopg://")
    native = next(step for step in native_job["steps"] if step.get("name") ==
                  "Run native browser concurrency and core workflow tests")
    native_lines = native["run"].splitlines()
    assert native_lines[0] == 'git config --global --add safe.directory "$GITHUB_WORKSPACE"'
    command = shlex.split(native_lines[1])
    assert command[:3] == ["python", "-m", "pytest"]
    assert command[3:] == ["$NATIVE_TEST_FILES", "-ra", "--durations=20",
                           "--junitxml=native-browser-results.xml"]
    assert native["env"]["NATIVE_TEST_FILES"] == "${{ inputs.tests }}"
    shards = {
        job_id: job for job_id, job in workflow()["jobs"].items()
        if job_id.startswith("native-browser-")
    }
    assert set(shards) == {
        "native-browser-journey", "native-browser-quality-workspace", "native-browser-workflows",
    }
    assert len({job["name"] for job in shards.values()}) == len(shards)
    files_by_shard = {}
    for job_id, job in shards.items():
        assert "if" not in job and "strategy" not in job
        assert job["uses"] == "./.github/workflows/native-browser.yml"
        assert job["with"]["shard"] == job_id.removeprefix("native-browser-")
        files = shlex.split(job["with"]["tests"])
        assert files, "Every native shard must execute tests"
        files_by_shard[job_id] = files
    assert files_by_shard["native-browser-journey"] == ["tests/postgres/test_browser_journey.py"]
    assert files_by_shard["native-browser-quality-workspace"] == [
        "tests/postgres/test_browser_quality.py", "tests/postgres/test_workspace_browser.py",
    ]
    selected = [file for files in files_by_shard.values() for file in files]
    # Exact inventory comparison rejects omissions, duplicates and extra pytest
    # arguments that could filter cases. Newly added modules must get an owner.
    assert sorted(selected) == sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "tests/postgres").glob("test*browser*.py")
    )
    assert native_job["timeout-minutes"] == "20"
    artifact = next(step for step in native_job["steps"]
                    if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert artifact["with"]["name"] == "native-browser-results-${{ inputs.shard }}"
    assert artifact["if"] == "${{ always() }}"
    assert "native-browser-results.xml" in artifact["with"]["path"].splitlines()
    assert "web/test-results/" in artifact["with"]["path"].splitlines()
    assert native["env"]["TABULA_RASA_REQUIRE_BROWSER_TESTS"] == "1"
    commands = [step.get("run", "") for step in native_job["steps"]]
    assert "npm --prefix web run build" in commands
    version = next(line.removeprefix("playwright==") for line in
                   (PROJECT_ROOT / "requirements-dev.txt").read_text().splitlines()
                   if line.startswith("playwright=="))
    assert native_job["container"] == {
        "image": f"mcr.microsoft.com/playwright/python:v{version}-noble",
        "options": "--init --ipc=host",
    }
    assert not any(step.get("uses", "").startswith("actions/setup-python@")
                   for step in native_job["steps"])
    runtime = next(step for step in native_job["steps"]
                   if step.get("name") == "Verify image Python runtime")
    assert "import ssl, sys" in runtime["run"] and "sys.version_info[:2] == (3, 12)" in runtime["run"]
    assert "python -m pip install -r requirements-dev.txt" in commands
    assert native_job["env"]["HOME"] == "/root"
    assert native_job["env"]["TABULA_RASA_TEST_DATABASE_URL"] == (
        "postgresql+psycopg://tabula_test@postgres:5432/tabula_test"
    )
    assert "ports" not in native_job["services"]["postgres"]
    assert not any("playwright install" in command or "apt-get" in command for command in commands)
    assert not any("test:e2e" in command or "test:browser" in command for command in commands)


def test_browser_shards_preserve_every_project_without_blanket_retries() -> None:
    browser = workflow()["jobs"]["browser"]
    assert "if" not in browser and "services" not in browser
    assert browser["strategy"] == {"fail-fast": "false", "matrix": {"shard": ["1", "2"]}}
    commands = [step.get("run", "") for step in browser["steps"]]
    shard = next(step for step in browser["steps"] if step.get("name") == "Run browser UI shard")
    assert shard["env"] == {"HOME": "/root"}
    assert shard["run"].splitlines() == [
        'git config --global --add safe.directory "$GITHUB_WORKSPACE"',
        'npm --prefix web run test:browser -- --shard=${{ matrix.shard }}/2',
    ]
    # Preinstalled engines/libraries avoid a regional apt mirror download on
    # each shard. Browser revisions must match the installed test package.
    package = json.loads((PROJECT_ROOT / "web/package.json").read_text(encoding="utf-8"))
    lock = json.loads((PROJECT_ROOT / "web/package-lock.json").read_text(encoding="utf-8"))
    version = package["devDependencies"]["@playwright/test"]
    assert lock["packages"]["node_modules/@playwright/test"]["version"] == version
    assert browser["container"]["image"] == f"mcr.microsoft.com/playwright:v{version}-noble"
    assert browser["container"]["options"] == "--init --ipc=host"
    assert not any("playwright install" in command or "apt-get" in command for command in commands)
    assert not any("pytest" in command or "test:contracts" in command for command in commands)
    config = (PROJECT_ROOT / "web/playwright.config.ts").read_text(encoding="utf-8")
    assert "fullyParallel: true" in config  # shard by tests, not imbalanced files
    for project in ["desktop", "mobile", "firefox", "webkit"]:
        assert "name: '" + project + "'" in config
    assert "retries:" not in config and "testIgnore:" not in config and "grep:" not in config
    artifact = next(step for step in browser["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert "matrix.shard" in artifact["with"]["name"]
    assert artifact["if"] == "${{ always() }}"


def test_frontend_contracts_run_once_and_runtime_image_remains_required() -> None:
    frontend = workflow()["jobs"]["frontend-contract"]
    assert "if" not in frontend
    commands = [step.get("run", "") for step in frontend["steps"]]
    for command in [
        "python -m tabula_rasa.http.export_openapi --output contracts/http/openapi.json",
        "npm --prefix web run api:generate",
        "git diff --exit-code -- contracts/http/openapi.json web/src/api/schema.d.ts",
        "npm --prefix web run build", "npm --prefix web run lint", "npm --prefix web test",
        "npm --prefix web run test:contracts",
    ]:
        assert command in commands
    assert any("docker build --platform linux/amd64" in command and "docker run --rm" in command
               for command in commands)
    assert not any("playwright install" in command for command in commands)
    config = (PROJECT_ROOT / "web/playwright.contracts.config.ts").read_text(encoding="utf-8")
    assert "testDir: './contracts'" in config
    assert "projects:" not in config and "webServer:" not in config
    contracts = (PROJECT_ROOT / "web/contracts/visual-review.spec.ts").read_text(encoding="utf-8")
    assert "({ page })" not in contracts
    assert contracts.count("test('") == 5
    browser = (PROJECT_ROOT / "web/e2e/visual-review.spec.ts").read_text(encoding="utf-8")
    assert browser.count("test('") == 2
    assert browser.count("async ({ page })") == 2


def test_required_gate_waits_for_all_suites_even_when_a_dependency_fails() -> None:
    jobs = workflow()["jobs"]
    gate = jobs["full-suite"]
    assert gate["name"] == "Full test suite"
    assert gate["if"] == "${{ always() }}"
    assert set(gate["needs"]) == set(jobs) - {"full-suite"}
    assert "continue-on-error" not in gate
    for job in jobs.values():
        assert "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in job.get("steps", []))
    step = gate["steps"][0]
    assert step["shell"] == "bash"
    assert set(step["env"].values()) == {
        "${{ needs." + job + ".result }}" for job in gate["needs"]
    }


def run_gate(results: dict[str, str]) -> subprocess.CompletedProcess[str]:
    step = workflow()["jobs"]["full-suite"]["steps"][0]
    if os.name == "nt":
        # Use Git's shell, never the unrelated Windows/WSL bash launcher.
        git = shutil.which("git")
        assert git is not None, "Git is required by repository tooling tests"
        bash = Path(git).resolve().parents[1] / "bin/bash.exe"
        assert bash.is_file(), "Git for Windows Bash is required"
    else:
        bash = shutil.which("bash")
        assert bash is not None, "Bash is required by the Actions aggregate gate"
    environment = {**os.environ, **results}
    return subprocess.run(
        [str(bash), "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        env=environment, capture_output=True, text=True, check=False, timeout=10,
    )


def test_required_gate_accepts_all_successful_suites() -> None:
    result = run_gate(dict.fromkeys(workflow()["jobs"]["full-suite"]["steps"][0]["env"],
                                   "success"))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("job", list(workflow()["jobs"]["full-suite"]["steps"][0]["env"]))
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "", "pending", "timed_out"])
def test_required_gate_rejects_failed_or_incomplete_suite(job: str, conclusion: str) -> None:
    results = dict.fromkeys(workflow()["jobs"]["full-suite"]["steps"][0]["env"], "success")
    results[job] = conclusion
    result = run_gate(results)
    assert result.returncode == 1, result.stderr
