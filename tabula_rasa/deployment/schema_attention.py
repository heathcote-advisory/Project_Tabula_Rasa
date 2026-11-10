"""Detect a pending schema change and record it in the operator's issue queue."""

import argparse
import os
import re

from . import release


ENVIRONMENTS = {"staging", "production"}
RUNBOOK = "docs/operations/DEPLOYMENT.md#schedule-an-approved-forward-release"


def revision(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", value):
        raise release.ReleaseError("The database and release must have identifiable schema revisions.")
    return value


def inspect_schema(environ):
    from tabula_rasa.infrastructure.postgres.hosted import open_bound_neon_database

    required = revision(environ["REQUIRED_SCHEMA"])
    database = open_bound_neon_database(environ)
    try:
        health = database.health()
    finally:
        database.dispose()
    if health.get("connected") is not True:
        raise release.ReleaseError("The bound database schema could not be read.")
    current = revision(health.get("revision"))
    return current, current != required


def notify(environ, *, read=release.api):
    from .schema_migration import MAINTENANCE_TRANSITIONS

    stage = environ["TARGET_ENVIRONMENT"]
    if stage not in ENVIRONMENTS:
        raise release.ReleaseError("Schema alerts require staging or production.")
    current = revision(environ["CURRENT_SCHEMA"])
    required = revision(environ["REQUIRED_SCHEMA"])
    if current == required:
        return
    repo = environ["GITHUB_REPOSITORY"]
    image = environ["RELEASE_IMAGE"]
    release.immutable_digest(image, release.commercial_image_repository(repo))
    commit, run = environ["RELEASE_COMMIT"], environ["GITHUB_RUN_ID"]
    assignee = environ.get("SCHEMA_ATTENTION_ASSIGNEE", "")
    if (not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9]+", run)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", assignee)):
        raise release.ReleaseError("Configure a release operator and verified release identity for schema alerts.")
    next_step = (
        "Review the candidate and queue an approved maintenance window."
        if (current, required) in MAINTENANCE_TRANSITIONS
        else "Review and validate this transition before adding it to the scheduled migration path."
    )
    if stage == "production":
        next_step += " Obtain successful live staging evidence for this digest and explicit production approval."
    title = f"Schema change needs attention in {stage} ({current} → {required})"
    marker = "<!-- commercial-schema-attention:"
    body = (
        f"{marker}{commit} -->\n\n"
        f"**{stage.capitalize()} requires a schema decision.**\n\n"
        f"- Current revision: `{current}`\n"
        f"- Required revision: `{required}`\n"
        f"- Candidate image: `{image}`\n"
        f"- [Release workflow](https://github.com/{repo}/actions/runs/{run})\n\n"
        f"{next_step}\n\n"
        f"[Deployment runbook](https://github.com/{repo}/blob/main/{RUNBOOK})"
    )
    api = f"/repos/{repo}"
    token = environ["GH_TOKEN"]
    page = 1
    while True:
        issues = read(f"{api}/issues?state=open&per_page=100&page={page}", token, github=True)
        existing = next((issue for issue in issues if issue.get("title") == title
                         and (issue.get("user") or {}).get("login") == "github-actions[bot]"
                         and (issue.get("body") or "").startswith(marker)), None)
        if existing or len(issues) < 100:
            break
        page += 1

    payload = {"title": title, "body": body, "assignees": [assignee]}
    if existing:
        previous = re.match(re.escape(marker) + r"([0-9a-f]{40}) -->", existing["body"])
        if previous is None:
            raise release.ReleaseError("The tracked schema alert has invalid release metadata.")
        prior_commit = previous[1]
        if prior_commit != commit:
            comparison = read(f"{api}/compare/{prior_commit}...{commit}", token, github=True)
            if comparison.get("status") == "behind":
                return
            if comparison.get("status") != "ahead":
                raise release.ReleaseError("The alert candidate does not advance the tracked release.")
        if image in existing["body"] and assigned_to(existing, assignee):
            return
        result = read(f"{api}/issues/{existing['number']}", token, github=True, method="PATCH", body=payload)
    else:
        result = read(f"{api}/issues", token, github=True, method="POST", body=payload)
    if not assigned_to(result, assignee):
        raise release.ReleaseError("GitHub did not assign the schema alert to the configured operator.")
    print(f"Schema change needs attention: {result['html_url']}")


def assigned_to(issue, assignee):
    return any(user["login"].lower() == assignee.lower() for user in issue.get("assignees", []))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect", "notify"])
    args = parser.parse_args()
    try:
        if args.command == "notify":
            notify(os.environ)
        else:
            current, pending = inspect_schema(os.environ)
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                print(f"current={current}\npending={str(pending).lower()}", file=output)
    except release.ReleaseError as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit("Schema attention check failed. Inspect the environment binding and release configuration.") from None


if __name__ == "__main__":
    main()
