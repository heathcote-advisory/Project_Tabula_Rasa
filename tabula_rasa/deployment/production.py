"""Production preflight for the shared immutable Render promotion."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from tabula_rasa.infrastructure.postgres.hosted import direct_neon_settings
from tabula_rasa.infrastructure.postgres.identity import database_url_identity

from . import release


def staging_evidence(image, candidate, environ, *, read, github=release.api):
    run_id = environ.get("PRODUCTION_STAGING_RUN", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id):
        raise release.ReleaseError("Select the automatic staging workflow run ID.")
    repo = environ["GITHUB_REPOSITORY"]
    path = f"/repos/{repo}/actions/runs/{run_id}"
    token = environ["GH_TOKEN"]
    run = github(path, token, github=True)
    attempt = run.get("run_attempt")
    head_repository = run.get("head_repository")
    if (run.get("path") != ".github/workflows/commercial-image.yml"
            or run.get("event") != "workflow_run" or run.get("head_branch") != "main"
            or not isinstance(head_repository, dict) or head_repository.get("full_name") != repo
            or run.get("status") != "completed" or type(attempt) is not int or attempt < 1):
        raise release.ReleaseError("Staging evidence must come from the completed main release workflow.")
    jobs = github(f"{path}/attempts/{attempt}/jobs?per_page=100", token, github=True)["jobs"]
    for name in ("build", "staging / deploy"):
        matching = [job for job in jobs if job.get("name") == name]
        if len(matching) != 1 or matching[0].get("conclusion") != "success":
            raise release.ReleaseError("Successful build and staging jobs from the same attempt are required.")

    with TemporaryDirectory() as directory:
        result = release.subprocess.run(
            ["gh", "run", "download", run_id, "--repo", repo,
             "--name", f"staging-release-{attempt}", "--dir", directory],
            env=dict(environ), capture_output=True, check=False, timeout=60,
        )
        try:
            artifact = Path(directory) / "release.json"
            if result.returncode or artifact.stat().st_size > 8192:
                raise ValueError
            proof = json.loads(artifact.read_text(encoding="utf-8"))
            age = datetime.now(timezone.utc) - datetime.fromisoformat(proof["verified_at"])
        except (OSError, ValueError, KeyError, TypeError):
            raise release.ReleaseError("Staging evidence is missing, expired or malformed.") from None

    service = environ.get("STAGING_RENDER_SERVICE_ID", "")
    staging_db = proof.get("database_sha256")
    production_db = environ.get("TABULA_RASA_DEPLOY_DATABASE_SHA256", "")
    deploy = proof.get("deploy_id")
    expected = {**candidate, "image": image, "environment": "staging",
                "status": "smoke_passed", "access_mode": "named_user",
                "render_service": service, "rollback_from": None}
    if (any(proof.get(key) != value for key, value in expected.items())
            or not timedelta(0) <= age <= timedelta(days=7)
            or not isinstance(staging_db, str) or not re.fullmatch(r"[0-9a-f]{64}", staging_db)
            or not re.fullmatch(r"[0-9a-f]{64}", production_db) or staging_db == production_db
            or not re.fullmatch(r"srv-[a-z0-9]+", service) or service == environ.get("RENDER_SERVICE_ID")
            or not isinstance(deploy, str) or not re.fullmatch(r"dep-[a-z0-9]+", deploy)):
        raise release.ReleaseError("Staging evidence does not match the candidate or isolated staging target.")

    staging_token = environ.get("STAGING_RENDER_API_KEY", "")
    if not staging_token:
        raise release.ReleaseError("Staging Render read access is required to verify the current live deployment.")
    stage_target = read(f"/services/{service}", staging_token)
    verify_render_origin(stage_target, service, proof.get("public_origin"), staging_token, read=read)
    verify_render_database(service, staging_token, staging_db, read=read)
    current = release.active_deployment(service, staging_token, read=read)
    if current is None or current.get("id") != deploy:
        raise release.ReleaseError("The verified staging deployment is no longer live.")
    repository = release.commercial_image_repository(repo)
    release.deployed_image(current, image, candidate, repository)
    return {"staging_run": run_id, "staging_attempt": attempt,
            "staging_run_url": f"https://github.com/{repo}/actions/runs/{run_id}",
            "staging_deploy_id": deploy, "staging_verified_at": proof["verified_at"]}


def verify_render_origin(target, service, origin, token, *, read):
    if not isinstance(origin, str):
        raise release.ReleaseError("The verified HTTPS origin is missing or malformed.")
    hostname = urlsplit(origin).hostname
    if (target.get("id") != service or target.get("type") != "web_service" or not hostname
            or not re.fullmatch(r"[a-z0-9.-]+", hostname) or origin != f"https://{hostname}"):
        raise release.ReleaseError("The verified HTTPS origin must belong to the configured Render service.")
    if (target.get("serviceDetails") or {}).get("url") == origin:
        return
    domain = read(f"/services/{service}/custom-domains/{hostname}", token)
    if (domain.get("name") != hostname or domain.get("verificationStatus") != "verified"
            or domain.get("redirectForName")):
        raise release.ReleaseError("Render custom domain is unverified or redirects to a different origin.")


def production_target(environ, *, read):
    service, token = release.render_target(environ)
    origin = environ["TABULA_RASA_PUBLIC_ORIGIN"]
    target = read(f"/services/{service}", token)
    details = target.get("serviceDetails") or {}
    verify_render_origin(target, service, origin, token, read=read)
    if (target.get("suspended") != "not_suspended"
            or target.get("autoDeployTrigger", "off" if target.get("autoDeploy") == "no" else "") != "off"
            or details.get("healthCheckPath") != "/health/ready" or details.get("runtime") != "image"):
        raise release.ReleaseError("Render production must be an active image service with auto-deploy off and readiness configured.")

    issuer = environ.get("PRODUCTION_OIDC_ISSUER", "")
    client = environ.get("PRODUCTION_OIDC_CLIENT_ID", "")
    parsed_issuer = urlsplit(issuer)
    if (parsed_issuer.scheme != "https" or not parsed_issuer.hostname or parsed_issuer.username
            or parsed_issuer.password or parsed_issuer.query or parsed_issuer.fragment
            or issuer.endswith("/") or not client):
        raise release.ReleaseError("Expected production Zitadel issuer and client are required.")
    expected = {"ENVIRONMENT": "production", "ACCESS_MODE": "named_user",
                "PUBLIC_ORIGIN": origin, "OIDC_ISSUER": issuer, "OIDC_CLIENT_ID": client}
    if any(render_setting(service, token, name, read=read) != value for name, value in expected.items()):
        raise release.ReleaseError("Render production origin or named-user identity settings do not match.")

    verify_render_database(service, token, environ.get("TABULA_RASA_DEPLOY_DATABASE_SHA256"), read=read)
    return f"https://dashboard.render.com/web/{service}"


def render_setting(service, token, name, *, read):
    key = f"TABULA_RASA_{name}"
    result = read(f"/services/{service}/env-vars/{key}", token)
    if result.get("key") != key or not result.get("value"):
        raise release.ReleaseError("Required Render service configuration is missing.")
    return result["value"]


def verify_render_database(service, token, expected_fingerprint, *, read):
    # Compare the configured runtime endpoint to the verified migration binding.
    # The credential-bearing URL never leaves this check.
    url = render_setting(service, token, "DATABASE_URL", read=read)
    try:
        settings = direct_neon_settings({"TABULA_RASA_DATABASE_URL": url})
        if database_url_identity(settings.url) != expected_fingerprint:
            raise ValueError
    except ValueError:
        raise release.ReleaseError("Render runtime database does not match the intended direct Neon TLS endpoint.") from None


def check_production_prior(image, previous_image, previous, candidate, environ):
    repository = release.commercial_image_repository(environ["GITHUB_REPOSITORY"])
    expected = environ.get("PRODUCTION_PREVIOUS_IMAGE", "")
    expected_digest = release.immutable_digest(expected, repository)
    recovery = environ.get("PRODUCTION_RECOVERY_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", recovery):
        raise release.ReleaseError("A reviewed production recovery record SHA-256 is required.")
    if previous is None or expected_digest not in {
        release.immutable_digest(previous_image, repository), previous["manifest_digest"],
    }:
        raise release.ReleaseError("Production is no longer on the expected immutable release.")
    if previous["release"] == candidate["release"] or previous_image == image:
        raise release.ReleaseError("The requested production revision is already live.")
    release.verify_prior_release(candidate, previous, "production")
    return recovery
