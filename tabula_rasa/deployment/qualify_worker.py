"""Probe an installed ARM64 worker against isolated candidate-owned schemas."""

import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from . import release, schema_migration, worker_compatibility as compatibility


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)
    if check and result.returncode:
        raise release.ReleaseError("Disposable worker qualification failed; no compatibility proof was produced.")
    return result


def run(environ, *, emit=lambda _: None):
    worker = compatibility.identity(environ)
    image = environ["IMAGE"]
    candidate = {**release.image_metadata(image, release.commercial_image_repository(environ["GITHUB_REPOSITORY"])),
                 "image": image}
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != candidate["release"]:
        raise release.ReleaseError("Qualification fixtures must come from the exact candidate commit.")
    source = environ.get("PRODUCTION_SCHEMA")
    if not source:
        raise release.ReleaseError("A verified production schema is required. Check the production schema inspection.")
    target = candidate["schema"]
    schema_migration.rollout_path(source, target)
    schemas = [source] if source == target else [source, target]
    docker("pull", "--platform", "linux/arm64", worker["image"])
    metadata = json.loads(docker("image", "inspect", worker["image"]).stdout)[0]
    compatibility.verify_image(metadata, worker)
    docker("pull", "postgres:18.6")
    root = Path(__file__).resolve().parents[2]
    prefix = "tabula-compat-" + uuid4().hex
    network = prefix + "-net"
    containers = []
    results = []
    try:
        docker("network", "create", "--internal", network)
        for index, schema in enumerate(schemas):
            emit({"schema": schema, "status": "preparing_disposable_database"})
            database = f"{prefix}-db-{index}"
            setup = f"{prefix}-setup-{index}"
            probe = f"{prefix}-probe-{index}"
            containers.extend([database, setup, probe])
            password = uuid4().hex
            docker("run", "-d", "--name", database, "--network", network,
                   "-e", f"POSTGRES_PASSWORD={password}", "postgres:18.6")
            for _ in range(60):
                if docker("exec", database, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", check=False).returncode == 0:
                    break
                time.sleep(.5)
            else:
                raise release.ReleaseError("Disposable qualification database did not become ready.")
            flags = ["--rm", "--network", network, "--read-only", "--tmpfs", "/tmp",
                     "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                     "-e", "PYTHONDONTWRITEBYTECODE=1"]
            emit({"schema": schema, "status": "preparing_candidate_fixtures"})
            docker("run", *flags, "--name", setup, "--platform", "linux/amd64",
                   "--mount", f"type=bind,source={root / 'tests'},target=/fixtures/tests,readonly",
                   "-e", "PYTHONPATH=/app:/fixtures", "-e", f"PROBE_PASSWORD={password}",
                   "-e", f"TABULA_RASA_DATABASE_URL=postgresql://postgres:{password}@{database}/postgres",
                   "--entrypoint", "python", image, "/fixtures/tests/tooling/worker_compatibility_prepare.py", schema)
            emit({"schema": schema, "status": "probing_installed_worker"})
            outcome = docker(
                "run", *flags, "--name", probe, "--platform", "linux/arm64",
                "--mount", f"type=bind,source={root / 'tests/tooling/worker_compatibility_probe.py'},target=/tmp/probe.py,readonly",
                "-e", "PYTHONPATH=/app",
                "-e", f"TABULA_RASA_DATABASE_URL=postgresql://probe_worker:{password}@{database}/postgres",
                "--entrypoint", "python", worker["image"], "/tmp/probe.py",
            )
            results.append(json.loads(outcome.stdout.strip().splitlines()[-1]))
            emit({"schema": schema, "status": "probe_completed"})
            docker("rm", "-f", "-v", database)
        proof = {"version": 1, "worker": worker, "candidate": candidate,
                 "run_id": environ["GITHUB_RUN_ID"], "run_attempt": int(environ["GITHUB_RUN_ATTEMPT"]),
                 "status": "passed", "results": results}
        return compatibility.verify(proof, candidate, environ, run_id=environ["GITHUB_RUN_ID"],
                                    attempt=int(environ["GITHUB_RUN_ATTEMPT"]), schemas=schemas)
    finally:
        for name in containers:
            docker("rm", "-f", "-v", name, check=False)
        docker("network", "rm", network, check=False)


def main():
    output = Path("worker-compatibility.json")
    output.unlink(missing_ok=True)
    try:
        proof = run(os.environ, emit=lambda event: print(json.dumps(event), flush=True))
        output.write_text(json.dumps(proof), encoding="utf-8")
    except (release.ReleaseError, schema_migration.ContractError) as error:
        raise SystemExit(f"Installed-worker qualification failed. {error}") from None
    except Exception:
        raise SystemExit("Installed-worker qualification failed. Inspect the isolated probe; no proof was retained.") from None


if __name__ == "__main__":
    main()
