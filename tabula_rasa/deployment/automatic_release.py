"""Select and execute unattended releases through the shared promotion workflow."""

import argparse
import json
import os
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from . import production, release, schema_window
from .release_checkpoint import verify_checkpoint
from .smoke import named_smoke


def production_cutoff(environ, now):
    if environ.get("GITHUB_RUN_ATTEMPT") != "1":
        return None
    start = datetime.combine(now.astimezone(schema_window.EASTERN).date(), time(3), schema_window.EASTERN)
    start = start.astimezone(timezone.utc)
    return start if start <= now < start + timedelta(minutes=15) else None


def staging_run(candidate, environ, cutoff, *, github):
    repo, token = environ["GITHUB_REPOSITORY"], environ["GH_TOKEN"]
    name = f"staging-release-{candidate['release']}"
    result = github(f"/repos/{repo}/actions/artifacts?name={name}&per_page=100", token, github=True)
    if result["total_count"] > 100:
        raise release.ReleaseError("Too many staging records to establish current release provenance.")
    runs = {
        artifact["workflow_run"]["id"] for artifact in result["artifacts"]
        if artifact["name"] == name and not artifact["expired"]
        and datetime.fromisoformat(artifact["created_at"]) <= cutoff
    }
    if not runs:
        return None
    if len(runs) != 1 or type(next(iter(runs))) is not int:
        raise release.ReleaseError("Ambiguous staging verification for the currently live release.")
    return str(runs.pop())


def attempt_task(image, environ):
    digest = image.split("@sha256:")[1]
    generation = environ.get("PRODUCTION_RECOVERY_GENERATION") or "0"
    if not re.fullmatch(r"[0-9]{1,9}", generation):
        raise release.ReleaseError("Invalid production recovery generation.")
    return f"automatic:{digest}:{generation}"


def attempt_status(image, environ, *, github):
    repo, token = environ["GITHUB_REPOSITORY"], environ["GH_TOKEN"]
    path = f"/repos/{repo}/deployments"
    task = attempt_task(image, environ)
    previous = github(f"{path}?environment=production&task={task}&per_page=1", token, github=True)
    if not previous:
        return None
    statuses = github(f"{path}/{int(previous[0]['id'])}/statuses?per_page=1", token, github=True)
    return statuses[0]["state"] if statuses else "pending"


def journal(image, candidate, environ, *, github):
    """Write the attempt before mutation. The owner can authorize a recovery generation."""
    repo, token = environ["GITHUB_REPOSITORY"], environ["GH_TOKEN"]
    path = f"/repos/{repo}/deployments"
    previous = attempt_status(image, environ, github=github)
    if previous:
        raise release.ReleaseError("Production already attempted this digest. Inspect the previous run and validate recovery before rearming.")
    row = github(path, token, github=True, body={
        "ref": candidate["release"], "environment": "production", "task": attempt_task(image, environ),
        "auto_merge": False, "required_contexts": [], "production_environment": True,
        "description": "Scheduled verified commercial promotion",
    })
    identity = row.get("id")
    if type(identity) is not int or row.get("sha") != candidate["release"]:
        raise release.ReleaseError("Could not record the production attempt before mutation.")
    status_path = f"{path}/{identity}/statuses"
    run_url = f"https://github.com/{repo}/actions/runs/{environ['GITHUB_RUN_ID']}"

    def status(value):
        github(status_path, token, github=True, body={"state": value, "log_url": run_url, "auto_inactive": False})

    status("in_progress")
    return status


def run(environ, *, read=release.api, github=release.api, metadata=release.image_metadata,
        verify=release.validated_main, database=schema_window.image_database, maintenance=schema_window.run,
        smoke=named_smoke, now=schema_window.utc_now, emit=lambda _: None):
    stage = environ["TABULA_RASA_ENVIRONMENT"]
    scheduled = stage == "production"
    cutoff = production_cutoff(environ, now()) if scheduled else None
    if scheduled and (environ.get("COMMERCIAL_AUTO_PRODUCTION_ENABLED") != "true" or cutoff is None):
        return {"status": "skipped", "reason": "disabled_or_outside_safe_start_window"}
    if stage not in {"staging", "production"} or release.promotion_mode(environ) != "named_user":
        raise release.ReleaseError("Unattended promotion requires a bound named-user environment.")
    repository = release.commercial_image_repository(environ["GITHUB_REPOSITORY"])
    service, token = release.render_target(environ)
    origin = environ["TABULA_RASA_PUBLIC_ORIGIN"]
    fingerprint = environ["TABULA_RASA_DEPLOY_DATABASE_SHA256"]
    if scheduled:
        staging_service = environ["STAGING_RENDER_SERVICE_ID"]
        staging_token = environ["STAGING_RENDER_API_KEY"]
        if not re.fullmatch(r"srv-[a-z0-9]+", staging_service) or staging_service == service or not staging_token:
            raise release.ReleaseError("Production requires isolated staging Render read access.")
        _, candidate = release.live_image(staging_service, staging_token, repository, read=read, metadata=metadata)
        if candidate is None:
            return {"status": "skipped", "reason": "no_live_staging_candidate"}
        run_id = staging_run(candidate, environ, cutoff, github=github)
        if run_id is None:
            return {"status": "skipped", "reason": "no_verified_live_staging_candidate_at_cutoff"}
        environ = {**environ, "PRODUCTION_STAGING_RUN": run_id}
        selected = production.staging_evidence(
            None, candidate, environ, read=read, github=github, cutoff=cutoff, metadata=metadata,
        )
        image = selected["staging_image"]
    else:
        image = environ["IMAGE"]
        candidate = metadata(image, repository)
        if candidate["release"] != environ["RELEASE_COMMIT"]:
            raise release.ReleaseError("Staging requires the immutable image from this successful main build.")

    verify(candidate["release"], environ)
    state = schema_window.service_state(read, service, token)
    details = state["serviceDetails"]
    production.verify_render_origin(state, service, origin, token, read=read)
    if (state.get("suspended") != "not_suspended"
            or state.get("autoDeployTrigger", "off" if state.get("autoDeploy") == "no" else "") != "off"
            or details.get("healthCheckPath") != "/health/ready" or details.get("runtime") != "image"):
        raise release.ReleaseError("An active image service with auto-deploy off and readiness configured is required.")
    if any(production.render_setting(service, token, name, read=read) != value for name, value in {
        "ENVIRONMENT": stage, "ACCESS_MODE": "named_user", "PUBLIC_ORIGIN": origin,
    }.items()):
        raise release.ReleaseError("Render runtime environment, access mode or origin does not match.")
    runtime_role = production.verify_render_database(service, token, fingerprint, read=read)
    previous_image, previous = release.live_image(service, token, repository, read=read, metadata=metadata)
    if previous is None:
        raise release.ReleaseError("An existing verified live image is required.")
    if previous["release"] == candidate["release"] and previous != candidate:
        raise release.ReleaseError("A different immutable build of the serving revision is already live.")

    def revalidate(*, check_start=True):
        if scheduled:
            if check_start and production_cutoff(environ, now()) != cutoff:
                raise release.ReleaseError("The safe 03:00 Eastern start budget elapsed.")
            if check_start:
                production.production_target(environ, read=read)
            return production.staging_evidence(image, candidate, environ, read=read, github=github, cutoff=cutoff)
        return {}

    if scheduled and previous == candidate:
        prior_status = attempt_status(image, environ, github=github)
        if prior_status not in {None, "success"}:
            raise release.ReleaseError("The live production digest has an unresolved automatic attempt.")
        smoke(origin, candidate["release"])
        return {"status": "skipped", "reason": "already_live_and_ready", "image": image}

    source = previous["schema"]
    changes_schema = source != candidate["schema"]
    release.verify_prior_release(candidate, previous, stage, maintenance=changes_schema)
    if changes_schema:
        schema_window.verify_no_jobs(read, service, token)

    def checkpoint(image, candidate, source, runtime_role, environ):
        point = database(image, candidate, source, runtime_role, environ, apply=False, checkpoint=True)
        return verify_checkpoint(point, environ, now())

    status = None

    def begin(*, check_start=True):
        nonlocal status
        proof = revalidate(check_start=check_start)
        if scheduled and status is None:
            status = journal(image, candidate, environ, github=github)
            if production_cutoff(environ, now()) != cutoff:
                raise release.ReleaseError("The safe 03:00 Eastern start budget elapsed while recording the attempt.")
        return proof

    try:
        if changes_schema:
            result = maintenance(
                environ, plan={"image": image, "previous_image": previous_image,
                               "render_service": service, "database_sha256": fingerprint},
                read=read, metadata=metadata, verify=verify, check_database=database,
                smoke_check=smoke, now=now, emit=emit, revalidate=begin, checkpoint=checkpoint,
            )
            if result["status"] != "smoke_passed":
                raise release.ReleaseError("The schema-changing release did not finish verified.")
        else:
            point = checkpoint(image, candidate, source, runtime_role, environ) if scheduled else {}
            if not scheduled:
                database(image, candidate, source, runtime_role, environ, apply=False)
            prior_deploy = release.active_deployment(service, token, read=read)
            if prior_deploy is None:
                raise release.ReleaseError("The prior deployment is no longer active.")
            release.deployed_image(prior_deploy, previous_image, previous, repository)
            smoke(origin, previous["release"])
            if release.live_image(service, token, repository, read=read, metadata=metadata) != (previous_image, previous):
                raise release.ReleaseError("The live target changed during preflight.")
            proof = begin()
            deploy, started = release.deploy_verified(
                service, token, image, candidate, repository, read=read, record_request=True,
            )
            if deploy == prior_deploy["id"]:
                raise release.ReleaseError("Render did not create a new deployment for the requested restart.")
            smoke(origin, candidate["release"])
            live = release.active_deployment(service, token, read=read)
            if live is None or live["id"] != deploy:
                raise release.ReleaseError("The new deployment is no longer active after smoke.")
            result = {
                **candidate, **proof, **point, "image": image, "environment": stage, "access_mode": "named_user",
                "deploy_id": deploy, "deploy_started_at": started, "verified_at": now().isoformat(),
                "restarted": True, "status": "smoke_passed", "rollback_from": None, "render_service": service,
                "public_origin": origin, "database_sha256": fingerprint,
            }
        if status:
            status("success")
    except Exception:
        if status:
            try:
                status("failure")
            except release.ReleaseError:
                emit({"status": "could_not_update_production_attempt"})
        raise
    return {**result, "run_attempt": int(environ["GITHUB_RUN_ATTEMPT"])}


def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()
    environ = os.environ

    def emit(event):
        with open(environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            print(json.dumps(event, sort_keys=True), file=output)

    try:
        result = run(environ, emit=emit)
        emit(result)
        if result["status"] == "smoke_passed":
            Path("release.json").write_text(json.dumps(result), encoding="utf-8")
            with open(environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                print("promoted=true", file=output)
    except Exception as error:
        message = str(error) if isinstance(error, release.ReleaseError) else "Inspect the last release phase privately."
        raise SystemExit(f"Automatic release blocked. {message}") from None


if __name__ == "__main__":
    main()
