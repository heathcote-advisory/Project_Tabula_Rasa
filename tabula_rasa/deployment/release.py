"""Digest promotion and bounded Render lifecycle; never retry uncertain creates."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit


class ReleaseError(ValueError):
    """Fixed operator diagnostics only."""


MANIFEST_TYPES = {"application/vnd.oci.image.manifest.v1+json",
                  "application/vnd.docker.distribution.manifest.v2+json"}
INDEX_TYPES = {"application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json"}


def commercial_image_repository(github_repository):
    return "ghcr.io/" + github_repository.lower() + "/commercial-private"


def immutable_digest(image, repository):
    if not isinstance(image, str) or not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", image):
        raise ReleaseError("Use an immutable digest in the configured repository.")
    return image.split("@", 1)[1]


def registry_manifest(image, repository):
    """Read the registry descriptor, never a local image/config ID or a tag."""
    digest = immutable_digest(image, repository)
    result = subprocess.run(["docker", "buildx", "imagetools", "inspect", image,
                             "--format", "{{json .Manifest}}"], capture_output=True, check=False)
    try:
        manifest = json.loads(result.stdout)
        if result.returncode or manifest["digest"] != digest:
            raise ValueError
        return manifest
    except (ValueError, KeyError, TypeError):
        raise ReleaseError("Invalid registry image metadata; inspect retained release digests.") from None


def deployed_image(deploy, image, metadata, repository):
    """Require both Render fields to identify the verified immutable artifact."""
    allowed = {immutable_digest(image, repository), metadata["manifest_digest"]}
    try:
        reported = deploy["image"]
        ref_digest = immutable_digest(reported["ref"], repository)
        sha = reported["sha"]
        if not isinstance(sha, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", sha):
            raise ValueError
        digest = sha if sha.startswith("sha256:") else "sha256:" + sha
        if ref_digest not in allowed or digest not in allowed:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ReleaseError("Render image metadata mismatch or missing; stop and inspect provider state.") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api(path, token, *, body=None, github=False):
    base = "https://api.github.com" if github else "https://api.render.com/v1"
    request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                               "Accept": "application/json", "User-Agent": "tabula-release"})
    try:
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.load(response)
    except Exception:
        raise ReleaseError("Provider request failed or its result is uncertain; inspect before retrying.") from None


def image_metadata(image, repository):
    digest = immutable_digest(image, repository)
    manifest = registry_manifest(image, repository)
    try:
        if manifest["mediaType"] in INDEX_TYPES:
            matching = [item for item in manifest["manifests"]
                        if item.get("platform", {}).get("os") == "linux"
                        and item.get("platform", {}).get("architecture") == "amd64"]
            if len(matching) != 1 or matching[0]["mediaType"] not in MANIFEST_TYPES:
                raise ValueError
            digest = matching[0]["digest"]
            manifest = registry_manifest(repository + "@" + digest, repository)
        if manifest["mediaType"] not in MANIFEST_TYPES:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ReleaseError("Invalid linux/amd64 registry manifest.") from None
    runtime_image = repository + "@" + digest
    pull = subprocess.run(["docker", "pull", "--platform", "linux/amd64", runtime_image], capture_output=True, check=False)
    if pull.returncode:
        raise ReleaseError("Image cannot be pulled; preserve registry rollback digests.")
    result = subprocess.run(["docker", "image", "inspect", runtime_image], capture_output=True, check=False)
    try:
        metadata = json.loads(result.stdout)[0]
        labels = metadata["Config"]["Labels"]
        release = labels["org.opencontainers.image.revision"]
        schema = labels["io.tabula.schema"]
        if (result.returncode or metadata["Architecture"] != "amd64" or metadata["Os"] != "linux"
                or not re.fullmatch("[0-9a-f]{40}", release) or not re.fullmatch("[A-Za-z0-9_]{1,100}", schema)):
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError):
        raise ReleaseError("Invalid release image metadata.") from None
    return {"release": release, "schema": schema, "manifest_digest": digest}


def validated_main(release, environ):
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", release, "origin/main"],
                              capture_output=True, check=False)
    if ancestor.returncode:
        raise ReleaseError("Release must be reachable from main.")
    if environ.get("TABULA_RASA_ENVIRONMENT") == "development":
        latest = subprocess.run(["git", "rev-parse", "origin/main"], capture_output=True, check=False)
        if latest.returncode or latest.stdout.decode().strip() != release:
            raise ReleaseError("Superseded development release; deploy the latest validated main image.")
    repo = environ["GITHUB_REPOSITORY"]
    runs = api(f"/repos/{repo}/actions/workflows/ci.yml/runs?head_sha={release}&event=push&branch=main&per_page=20",
               environ["GH_TOKEN"], github=True)["workflow_runs"]
    if not runs or runs[0].get("conclusion") != "success" or runs[0].get("head_sha") != release:
        raise ReleaseError("Current main-revision Repository CI success is required.")


def render_target(environ):
    service, token = environ.get("RENDER_SERVICE_ID", ""), environ.get("RENDER_API_KEY", "")
    if not re.fullmatch("srv-[a-z0-9]+", service) or not token:
        raise ReleaseError("Render service identity and access are required.")
    return service, token


def active_deployment(service, token, *, read):
    deployments = read(f"/services/{service}/deploys?limit=100", token)
    if not isinstance(deployments, list):
        raise ReleaseError("Invalid Render deployment list; inspect provider state.")
    try:
        active = [item["deploy"] for item in deployments]
        statuses = {item["status"] for item in active}
    except (KeyError, TypeError):
        raise ReleaseError("Invalid Render deployment list; inspect provider state.") from None
    if any(not isinstance(status, str) or not status for status in statuses):
        raise ReleaseError("Invalid Render deployment status; inspect provider state.")
    if statuses & {"created", "queued", "build_in_progress", "update_in_progress", "pre_deploy_in_progress"}:
        raise ReleaseError("Another deployment is in progress; inspect before proceeding.")
    live = [item for item in active if item["status"] == "live"]
    if len(live) > 1:
        raise ReleaseError("Ambiguous live deployment; inspect provider state.")
    return live[0] if live else None


def live_image(service, token, repository, *, read, metadata):
    prior = active_deployment(service, token, read=read)
    if prior is None:
        return None, None
    try:
        image = prior["image"]["ref"]
        immutable_digest(image, repository)
    except (ValueError, KeyError, TypeError):
        raise ReleaseError("Prior image needs a verified immutable reference; inspect provider state.") from None
    previous = metadata(image, repository)
    deployed_image(prior, image, previous, repository)
    return image, previous


def verify_prior_release(candidate, previous, environment, rollback_from=""):
    if rollback_from and (
        environment != "staging" or previous is None or rollback_from != previous["release"]
        or candidate["release"] == rollback_from
    ):
        raise ReleaseError("Staging rollback requires the expected live revision and a different target revision.")
    if previous is None:
        return
    if environment in {"staging", "production"} and previous["release"] != candidate["release"]:
        ancestor, descendant = previous["release"], candidate["release"]
        if rollback_from:
            ancestor, descendant = descendant, ancestor
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True, check=False,
        )
        if result.returncode not in (0, 1):
            raise ReleaseError("Cannot verify release ancestry; inspect the workflow checkout and retained revisions.")
        if result.returncode == 1:
            if rollback_from:
                raise ReleaseError("Staging rollback target must precede the expected live revision.")
            raise ReleaseError("Candidate does not advance the live revision; inspect for a superseded release.")
    if previous["schema"] != candidate["schema"]:
        raise ReleaseError("Prior image is schema-incompatible; use reviewed maintenance/bridge procedure.")


def wait_live(service, deploy, token, *, read=api, sleep=time.sleep):
    if not re.fullmatch("dep-[a-z0-9]+", deploy):
        raise ReleaseError("Invalid deployment identity; inspect provider state.")
    for _ in range(60):
        result = read(f"/services/{service}/deploys/{deploy}", token)
        if result.get("status") == "live":
            return result
        if result.get("status") in {"build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"}:
            raise ReleaseError("Render deployment failed; inspect provider state and retained prior deployment.")
        sleep(10)
    raise ReleaseError("Deployment timed out; inspect before any further deployment.")


def promote(image, environ, *, read=api, metadata=image_metadata, verify=validated_main, smoke_check=None):
    from .smoke import named_smoke, smoke
    environment = environ.get("TABULA_RASA_ENVIRONMENT")
    mode = promotion_mode(environ, require_alpha_credentials=True)
    if mode == 'named_user':
        check = smoke_check or named_smoke
        def check_release(revision):
            check(environ['TABULA_RASA_PUBLIC_ORIGIN'], revision)
    else:
        check = smoke_check or smoke
        def check_release(revision):
            check(environ['TABULA_RASA_PUBLIC_ORIGIN'], environ['TABULA_RASA_ALPHA_USERNAME'],
                  environ['TABULA_RASA_ALPHA_PASSWORD'], revision)
    repository = commercial_image_repository(environ["GITHUB_REPOSITORY"])
    candidate = metadata(image, repository)
    verify(candidate["release"], environ)
    origin = environ["TABULA_RASA_PUBLIC_ORIGIN"]
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ReleaseError("An exact HTTPS origin is required.")
    service, token = render_target(environ)
    previous_image, previous = live_image(service, token, repository, read=read, metadata=metadata)
    rollback_from = environ.get("STAGING_ROLLBACK_FROM", "")
    evidence = {}
    if environment == "production":
        evidence = production_preflight(image, candidate, previous_image, previous, environ, read=read)
    else:
        verify_prior_release(candidate, previous, environment, rollback_from)
    deploy_started_at = datetime.now(timezone.utc).isoformat()
    deployed = read(f"/services/{service}/deploys", token, body={"imageUrl": image})
    # No rollback after an uncertain create/wait: it could race a still-running deploy.
    if environment == "production":
        deploy_id = deployed.get("id", "")
        if not re.fullmatch("dep-[a-z0-9]+", deploy_id):
            raise ReleaseError("Invalid production deployment identity; inspect Render before retrying.")
        print(json.dumps({"deploy_id": deploy_id, "deploy_started_at": deploy_started_at,
                          "status": "render_deploy_requested"}), file=sys.stderr, flush=True)
    confirmed = wait_live(service, deployed["id"], token, read=read)
    deployed_image(confirmed, image, candidate, repository)
    try:
        check_release(candidate['release'])
    except Exception:
        if mode == 'named_user':
            # Matching ledger schema alone cannot prove old auth code/epoch
            # compatibility. Never restore an alpha-era bypass automatically.
            raise ReleaseError("Named smoke failed; restrict ingress and inspect. Use the reviewed named-mode rollback checklist.") from None
        if previous_image:
            rollback = read(f"/services/{service}/deploys", token, body={"imageUrl": previous_image})
            restored = wait_live(service, rollback["id"], token, read=read)
            deployed_image(restored, previous_image, previous, repository)
            check_release(previous['release'])
            raise ReleaseError("Smoke failed; prior compatible image restored and smoke verified.") from None
        raise ReleaseError("Initial smoke failed; no prior image exists. Restrict access and inspect.") from None
    return {
        "image": image, **candidate, "environment": environ["TABULA_RASA_ENVIRONMENT"],
        "deploy_id": deployed["id"], "deploy_started_at": deploy_started_at, "rollback_from": rollback_from or None,
        "verified_at": datetime.now(timezone.utc).isoformat(), "access_mode": mode, "status": "smoke_passed",
        "render_service": service, "public_origin": origin, "database_sha256": environ.get("TABULA_RASA_DEPLOY_DATABASE_SHA256"),
        **evidence,
    }


def promotion_mode(environ, *, require_alpha_credentials=False):
    from .smoke import access_mode

    environment = environ.get("TABULA_RASA_ENVIRONMENT")
    mode = access_mode(environ, require_alpha_credentials=require_alpha_credentials and environment != "production")
    if environment not in {"development", "staging", "production"}:
        raise ReleaseError("Invalid promotion environment configuration.")
    if environment == "production" and (mode != "named_user" or environ.get("STAGING_ROLLBACK_FROM")):
        raise ReleaseError("Production requires named-user forward promotion.")
    return mode


def production_preflight(image, candidate, previous_image, previous, environ, *, read):
    from .production import check_production_prior, production_target, staging_evidence
    from .smoke import named_smoke

    recovery = check_production_prior(image, previous_image, previous, candidate, environ)
    proof = staging_evidence(image, candidate, environ, read=read)
    render_url = production_target(environ, read=read)
    try:
        named_smoke(environ["TABULA_RASA_PUBLIC_ORIGIN"], previous["release"])
    except Exception:
        raise ReleaseError("Current production release failed readiness, version or named-user smoke.") from None
    return {**proof, "previous_image": previous_image, "previous_release": previous["release"],
            "recovery_sha256": recovery, "render_url": render_url}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["verify", "promote"])
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        environ = dict(os.environ)
        promotion_mode(environ)
        if args.command == "verify":
            repository = commercial_image_repository(environ["GITHUB_REPOSITORY"])
            metadata = image_metadata(args.image, repository)
            validated_main(metadata["release"], environ)
            environment = environ["TABULA_RASA_ENVIRONMENT"]
            service, token = render_target(environ)
            previous_image, previous = live_image(service, token, repository, read=api, metadata=image_metadata)
            if environment == "production":
                metadata.update(production_preflight(
                    args.image, metadata, previous_image, previous, environ, read=api,
                ), image=args.image, status="preflight_passed")
            else:
                verify_prior_release(metadata, previous, environment, environ.get("STAGING_ROLLBACK_FROM", ""))
            print(json.dumps(metadata, sort_keys=True))
        else:
            print(json.dumps(promote(args.image, environ), sort_keys=True))
    except Exception as error:
        message = str(error) if isinstance(error, ReleaseError) else "Release step failed; inspect configuration and provider state."
        raise SystemExit(message) from None


if __name__ == "__main__":
    main()
