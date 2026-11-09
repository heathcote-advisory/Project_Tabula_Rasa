"""Run a digest-pinned forward release in an explicitly approved Eastern window."""

import argparse
import json
import os
import re
import subprocess
import time
from datetime import date, datetime, time as local_time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import build_opener
from zoneinfo import ZoneInfo

from . import release

EASTERN = ZoneInfo("America/New_York")
START_BUDGET = timedelta(minutes=25)


def utc_now():
    return datetime.now(timezone.utc)


def approval(environ, now):
    raw = environ.get("COMMERCIAL_SCHEMA_WINDOW", "")
    if not raw:
        return None, "no_release_queued"
    if environ.get("GITHUB_RUN_ATTEMPT") != "1":
        return None, "rerun_requires_new_window"
    try:
        plan = json.loads(raw)
        day = date.fromisoformat(plan["date"])
        duration = plan["minutes"]
        if type(duration) is not int or not 30 <= duration <= 60:
            raise ValueError
        start = datetime.combine(day, local_time(3), EASTERN).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        raise release.ReleaseError("Configure an exact Eastern window date and duration of 30 to 60 minutes.") from None
    if not start <= now <= start + timedelta(minutes=duration) - START_BUDGET:
        return None, "outside_safe_start_window"
    try:
        repository = release.commercial_image_repository(environ["GITHUB_REPOSITORY"])
        for key in ("image", "previous_image"):
            release.immutable_digest(plan[key], repository)
        checkpoint = plan["recovery_sha256"]
        created = datetime.fromisoformat(plan["checkpoint_at"])
        if (not re.fullmatch("[0-9a-f]{64}", checkpoint) or checkpoint == "0" * 64
                or not re.fullmatch("srv-[a-z0-9]+", plan["render_service"])
                or not re.fullmatch("[0-9a-f]{64}", plan["database_sha256"])
                or not timedelta(0) <= now - created <= timedelta(hours=24)
                or plan.get("writers_paused") is not True):
            raise ValueError
        if environ["TABULA_RASA_ENVIRONMENT"] == "production" and not re.fullmatch(
            "[1-9][0-9]{0,19}", plan["staging_run"],
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise release.ReleaseError("An exact image, prior image, fresh recovery record and writer-pause approval are required.") from None
    return plan, None


def service_state(read, service, token):
    state = read(f"/services/{service}", token)
    if state.get("id") != service:
        raise release.ReleaseError("Render returned a different service.")
    return state


def maintenance_state(state):
    mode = state["serviceDetails"]["maintenanceMode"]
    if type(mode["enabled"]) is not bool or not isinstance(mode["uri"], str):
        raise release.ReleaseError("Render maintenance status could not be verified.")
    return mode


def set_maintenance(read, service, token, *, enabled, uri):
    read(f"/services/{service}", token, method="PATCH",
         body={"serviceDetails": {"maintenanceMode": {"enabled": enabled, "uri": uri}}})
    state = service_state(read, service, token)
    if maintenance_state(state)["enabled"] is not enabled:
        raise release.ReleaseError("Render did not confirm the requested maintenance state.")
    return state


def verify_no_jobs(read, service, token):
    jobs = read(f"/services/{service}/jobs?status=pending,running&limit=1", token)
    if jobs != []:
        raise release.ReleaseError("Pending or running Render jobs must be drained before migration.")


def wait_drained(read, service, token, *, sleep=time.sleep):
    for _ in range(12):
        state = service_state(read, service, token)
        if not maintenance_state(state)["enabled"]:
            raise release.ReleaseError("Render maintenance protection was lost.")
        if state["suspended"] == "suspended" and read(f"/services/{service}/instances", token) == []:
            return
        sleep(5)
    raise release.ReleaseError("Render did not confirm the service drain within the wait budget.")


def image_source(state, image):
    owner = state.get("ownerId")
    credential = (state.get("registryCredential") or {}).get("id")
    if not all(isinstance(value, str) and value for value in (owner, credential)):
        raise release.ReleaseError("The Render service owner and private registry credential are required.")
    return {"imagePath": image, "ownerId": owner, "registryCredentialId": credential}


def select_suspended_image(read, service, token, source):
    read(f"/services/{service}", token, method="PATCH", body={"image": source})
    state = service_state(read, service, token)
    if (state["suspended"] != "suspended" or not maintenance_state(state)["enabled"]
            or image_source(state, state.get("imagePath")) != source):
        raise release.ReleaseError("The suspended service did not confirm the exact configured image.")


def resume_deployment(read, service, token, *, sleep=time.sleep):
    prior_ids = {item["id"] for item in release.render_deployments(service, token, read=read)}
    read(f"/services/{service}/resume", token, method="POST", no_content=True)
    for _ in range(12):
        state = service_state(read, service, token)
        if not maintenance_state(state)["enabled"]:
            raise release.ReleaseError("Render maintenance protection was lost.")
        if state["suspended"] == "not_suspended":
            new = [item for item in release.render_deployments(service, token, read=read)
                   if item["id"] not in prior_ids]
            if new:
                if len(new) != 1 or new[0].get("trigger") != "service_resumed":
                    raise release.ReleaseError("Unexpected deployment after service resume; inspect Render.")
                return new[0]["id"]
        sleep(5)
    raise release.ReleaseError("Render did not report a new deployment after resume; inspect before any further deploy.")


def public_closed(origin):
    try:
        with build_opener(release.NoRedirect).open(origin + "/version", timeout=10):
            pass
    except HTTPError as error:
        if error.code == 503:
            return
    raise release.ReleaseError("Public Render ingress did not return maintenance status.")


def public_reopened(origin, revision, *, sleep=time.sleep):
    opener = build_opener(release.NoRedirect)
    for _ in range(3):
        try:
            with opener.open(origin + "/version", timeout=5) as response:
                if json.load(response) != {"release": revision}:
                    raise release.ReleaseError("Public ingress is serving an unexpected revision.")
            with opener.open(origin + "/health/ready", timeout=5) as response:
                if response.status == 200:
                    return
        except (OSError, ValueError) as error:
            if isinstance(error, release.ReleaseError):
                raise
        sleep(3)
    raise release.ReleaseError("Public readiness could not be confirmed after reopening ingress.")


def image_database(image, candidate, source, runtime_role, environ, *, apply):
    names = {
        "TABULA_RASA_ENVIRONMENT": environ["TABULA_RASA_ENVIRONMENT"],
        "TABULA_RASA_DATABASE_URL": environ["WINDOW_MIGRATION_DATABASE_URL"],
        "TABULA_RASA_DEPLOY_DATABASE_SHA256": environ["TABULA_RASA_DEPLOY_DATABASE_SHA256"],
        "WINDOW_RUNTIME_ROLE": runtime_role,
        "WINDOW_SOURCE_SCHEMA": source,
        "WINDOW_TARGET_SCHEMA": candidate["schema"],
        "WINDOW_RELEASE": candidate["release"],
    }
    options = [item for name in names for item in ("-e", name)]
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", *options, image, "python", "-m",
             "tabula_rasa.deployment.schema_migration", "apply" if apply else "inspect"],
            env={**os.environ, **names}, capture_output=True, check=False, timeout=180,
        )
        if result.returncode == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise release.ReleaseError("Bound database step failed. Inspect role and revision privately before any retry.")


def run(environ, *, read=release.api, metadata=release.image_metadata, verify=release.validated_main,
        check_database=image_database, check_closed=public_closed, check_reopened=public_reopened,
        smoke_check=None, now=utc_now, emit=lambda _: None):
    from .production import (
        check_production_prior, production_target, render_setting, staging_evidence,
        verify_render_database, verify_render_origin,
    )
    from .schema_migration import MAINTENANCE_TRANSITIONS
    from .smoke import named_smoke

    plan, reason = approval(environ, now())
    if plan is None:
        return {"status": "skipped", "reason": reason}
    environment = environ["TABULA_RASA_ENVIRONMENT"]
    if environment not in {"staging", "production"} or release.promotion_mode(environ) != "named_user":
        raise release.ReleaseError("Scheduled maintenance requires an isolated named-user target.")
    smoke = smoke_check or named_smoke
    repository = release.commercial_image_repository(environ["GITHUB_REPOSITORY"])
    image = plan["image"]
    emit({"status": "selected", "environment": environment, "image": image,
          "checkpoint_sha256": plan["recovery_sha256"], "at": now().isoformat()})
    candidate = metadata(image, repository)
    verify(candidate["release"], environ)
    service, token = release.render_target(environ)
    fingerprint = environ.get("TABULA_RASA_DEPLOY_DATABASE_SHA256")
    if service != plan["render_service"] or fingerprint != plan["database_sha256"]:
        raise release.ReleaseError("The approved service or database binding has changed.")
    origin = environ["TABULA_RASA_PUBLIC_ORIGIN"]
    private_origin = environ.get("WINDOW_PRIVATE_ORIGIN", "")
    if private_origin == origin or not re.fullmatch(r"https://[a-z0-9.-]+(?::[0-9]+)?", private_origin):
        raise release.ReleaseError("A distinct, privately restricted smoke origin for this Render service is required.")
    state = service_state(read, service, token)
    details = state["serviceDetails"]
    verify_render_origin(state, service, origin, token, read=read)
    if (state["suspended"] != "not_suspended"
            or state.get("autoDeployTrigger", "off" if state.get("autoDeploy") == "no" else "") != "off"
            or details.get("runtime") != "image" or details.get("healthCheckPath") != "/health/ready"
            or not details.get("plan") or details["plan"] == "free" or maintenance_state(state)["enabled"]):
        raise release.ReleaseError("Maintenance requires an active paid image service with auto-deploy off and readiness configured.")
    if any(render_setting(service, token, name, read=read) != value for name, value in {
        "ENVIRONMENT": environment, "ACCESS_MODE": "named_user", "PUBLIC_ORIGIN": origin,
    }.items()):
        raise release.ReleaseError("Render environment, access mode or public origin does not match.")
    runtime_role = verify_render_database(service, token, fingerprint, read=read)
    previous_image, previous = release.live_image(service, token, repository, read=read, metadata=metadata)
    if previous is None:
        raise release.ReleaseError("An existing verified live release is required.")
    if image == previous_image or candidate["schema"] == previous["schema"]:
        return {"status": "skipped", "reason": "schema_compatible_use_normal_promotion"}
    candidate_source = image_source(state, image)
    source = previous["schema"]
    evidence = {
        "environment": environment, "image": image, **candidate, "source_schema": source,
        "checkpoint_sha256": plan["recovery_sha256"], "checkpoint_at": plan["checkpoint_at"],
        "previous_image": previous_image, "previous_release": previous["release"],
        "render_service": service, "public_origin": origin, "database_sha256": fingerprint,
        "access_mode": "named_user", "rollback_from": None,
    }
    emit({**evidence, "status": "preflight_started"})
    if ((source, candidate["schema"]) not in MAINTENANCE_TRANSITIONS
            or candidate["release"] == previous["release"]):
        raise release.ReleaseError("The forward schema transition has no reviewed maintenance path.")
    preflight_env = {
        **environ, "PRODUCTION_PREVIOUS_IMAGE": plan["previous_image"],
        "PRODUCTION_RECOVERY_SHA256": plan["recovery_sha256"],
        "PRODUCTION_STAGING_RUN": plan.get("staging_run", ""),
    }
    # Both environments use the same exact-live-image and forward ancestry guard.
    check_production_prior(image, previous_image, previous, candidate, preflight_env, maintenance=True)
    if environment == "production":
        evidence.update(staging_evidence(image, candidate, preflight_env, read=read))
        production_target(preflight_env, read=read)
    verify_no_jobs(read, service, token)
    check_database(image, candidate, source, runtime_role, environ, apply=False)
    smoke(private_origin, previous["release"])
    if approval(environ, now())[0] is None:
        return {"status": "skipped", "reason": "preflight_exhausted_safe_start_budget"}
    try:
        uri = maintenance_state(state)["uri"]
        emit({"status": "restricting_ingress", "at": now().isoformat()})
        set_maintenance(read, service, token, enabled=True, uri=uri)
        check_closed(origin)
        direct = details.get("url")
        if direct and direct != origin and details.get("renderSubdomainPolicy") != "disabled":
            check_closed(direct)
        evidence["maintenance_started_at"] = now().isoformat()
        emit({"status": "draining_service", "at": evidence["maintenance_started_at"]})
        read(f"/services/{service}/suspend", token, method="POST", no_content=True)
        wait_drained(read, service, token)
        verify_no_jobs(read, service, token)
        emit({"status": "configuring_suspended_image", "at": now().isoformat()})
        select_suspended_image(read, service, token, candidate_source)
        emit({"status": "applying_forward_migration", "at": now().isoformat()})
        check_database(image, candidate, source, runtime_role, environ, apply=True)
        evidence["schema_verified_at"] = now().isoformat()
        emit({"status": "schema_verified", "at": evidence["schema_verified_at"]})
        started = now().isoformat()
        emit({"status": "resuming_approved_image", "at": started})
        deploy_id = resume_deployment(read, service, token)
        emit({"status": "verifying_deployment", "deploy_id": deploy_id, "at": now().isoformat()})
        confirmed = release.wait_live(service, deploy_id, token, read=read)
        release.deployed_image(confirmed, image, candidate, repository)
        check_closed(origin)
        smoke(private_origin, candidate["release"])
        if not maintenance_state(service_state(read, service, token))["enabled"]:
            raise release.ReleaseError("Ingress protection was lost before verification.")
        verified_at = now().isoformat()
        emit({"status": "private_smoke_passed", "at": verified_at})
        set_maintenance(read, service, token, enabled=False, uri=uri)
        emit({"status": "ingress_reopened", "at": now().isoformat()})
        check_reopened(origin, candidate["release"])
    except Exception as error:
        emit({"status": "maintenance_failed", "at": now().isoformat()})
        detail = f"{error} " if isinstance(error, release.ReleaseError) else ""
        raise release.ReleaseError(
            detail + "Maintenance stopped. Inspect Render ingress and suspension, deployed digest and committed schema. "
            "Keep ingress restricted until verified; follow forward repair or compatible recovery. Do not rerun blindly."
        ) from None
    return {**evidence, "deploy_id": deploy_id, "deploy_started_at": started, "verified_at": verified_at,
            "traffic_restored_at": now().isoformat(), "status": "smoke_passed"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["select", "run"])
    args = parser.parse_args()
    environ = os.environ

    def summary(result):
        with open(environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            print(json.dumps(result, sort_keys=True), file=output)

    def output(name, value):
        with open(environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            print(f"{name}={value}", file=stream)

    try:
        if args.command == "select":
            plan, reason = approval(environ, utc_now())
            if plan:
                output("eligible", "true")
            else:
                summary({"status": "skipped", "reason": reason})
            return
        result = run(environ, emit=summary)
        summary(result)
        if result["status"] == "smoke_passed":
            Path("release.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
            output("promoted", "true")
    except Exception as error:
        summary({"status": "failed", "at": utc_now().isoformat()})
        message = str(error) if isinstance(error, release.ReleaseError) else "Schema window failed; inspect the last recorded phase."
        raise SystemExit(message) from None


if __name__ == "__main__":
    main()
