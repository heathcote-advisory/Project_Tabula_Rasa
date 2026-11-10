from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from tabula_rasa.deployment import release, schema_attention
from tabula_rasa.infrastructure.postgres import hosted


REPO = "owner/repo"
IMAGE = release.commercial_image_repository(REPO) + "@sha256:" + "a" * 64
COMMIT = "b" * 40
ASSIGNED = {"assignees": [{"login": "release-operator"}], "html_url": "https://github.com/owner/repo/issues/1"}
ENV = {
    "GITHUB_REPOSITORY": REPO,
    "TARGET_ENVIRONMENT": "staging",
    "CURRENT_SCHEMA": "0021_customer_lifecycle",
    "REQUIRED_SCHEMA": "0022_new_schema",
    "RELEASE_IMAGE": IMAGE,
    "RELEASE_COMMIT": COMMIT,
    "GITHUB_RUN_ID": "123",
    "SCHEMA_ATTENTION_ASSIGNEE": "release-operator",
    "GH_TOKEN": "synthetic",
}


@pytest.mark.parametrize(("actual", "expected"), [("0022_new_schema", False), ("0021_customer_lifecycle", True)])
def test_inspects_bound_schema_without_migration(monkeypatch, actual, expected):
    database = Mock()
    database.health.return_value = {"connected": True, "revision": actual}
    bind = Mock(return_value=database)
    monkeypatch.setattr(hosted, "open_bound_neon_database", bind)
    assert schema_attention.inspect_schema(ENV) == (actual, expected)
    bind.assert_called_once_with(ENV)
    database.health.assert_called_once_with()
    database.dispose.assert_called_once_with()
    database.transaction.assert_not_called()


@pytest.mark.parametrize("health", [{"connected": False}, {"connected": True, "revision": None}])
def test_unknown_database_state_never_becomes_a_mismatch(monkeypatch, health):
    database = Mock()
    database.health.return_value = health
    monkeypatch.setattr(hosted, "open_bound_neon_database", lambda _: database)
    with pytest.raises(release.ReleaseError):
        schema_attention.inspect_schema(ENV)
    database.dispose.assert_called_once_with()


@pytest.mark.parametrize(("stage", "current", "required", "message"), [
    ("staging", "0021_customer_lifecycle", "0022_new_schema", "Review and validate this transition"),
    ("production", "0020_book_currency", "0021_customer_lifecycle", "explicit production approval"),
])
def test_creates_assigned_alert_for_the_exact_candidate(stage, current, required, message):
    api = Mock(side_effect=[[], ASSIGNED])
    schema_attention.notify(
        {**ENV, "TARGET_ENVIRONMENT": stage, "CURRENT_SCHEMA": current, "REQUIRED_SCHEMA": required}, read=api,
    )
    request = api.call_args
    assert request.kwargs["method"] == "POST"
    assert request.kwargs["body"]["assignees"] == ["release-operator"]
    body = request.kwargs["body"]["body"]
    assert all(value in body for value in (IMAGE, current, required, message, "/actions/runs/123"))


def test_compatible_schema_never_writes_an_issue():
    api = Mock()
    schema_attention.notify({**ENV, "CURRENT_SCHEMA": ENV["REQUIRED_SCHEMA"]}, read=api)
    api.assert_not_called()


def test_matching_issue_with_no_author_does_not_block_the_bot_alert():
    user_issue = {
        "title": "Schema change needs attention in staging (0021_customer_lifecycle → 0022_new_schema)",
        "user": None,
        "body": f"<!-- commercial-schema-attention:{COMMIT} -->",
    }
    api = Mock(side_effect=[[user_issue], ASSIGNED])
    schema_attention.notify(ENV, read=api)
    assert api.call_args.kwargs["method"] == "POST"


@pytest.mark.parametrize(("status", "write"), [("ahead", True), ("behind", False)])
def test_only_a_newer_release_updates_the_open_alert(status, write):
    existing = {
        **ASSIGNED, "number": 1, "user": {"login": "github-actions[bot]"},
        "title": "Schema change needs attention in staging (0021_customer_lifecycle → 0022_new_schema)",
        "body": f"<!-- commercial-schema-attention:{'c' * 40} -->",
    }
    api = Mock(side_effect=[[existing], {"status": status}, ASSIGNED])
    schema_attention.notify(ENV, read=api)
    assert api.call_count == (3 if write else 2)
    if write:
        assert api.call_args.args[0] == "/repos/owner/repo/issues/1"
        assert api.call_args.kwargs["method"] == "PATCH"


def test_repeated_release_and_assignment_are_a_noop():
    existing = {
        **ASSIGNED, "number": 1, "user": {"login": "github-actions[bot]"},
        "title": "Schema change needs attention in staging (0021_customer_lifecycle → 0022_new_schema)",
        "body": f"<!-- commercial-schema-attention:{COMMIT} -->\n{IMAGE}",
    }
    api = Mock(return_value=[existing])
    schema_attention.notify(ENV, read=api)
    api.assert_called_once()


def test_missing_or_unassignable_operator_is_visible():
    api = Mock()
    with pytest.raises(release.ReleaseError, match="Configure a release operator"):
        schema_attention.notify({**ENV, "SCHEMA_ATTENTION_ASSIGNEE": ""}, read=api)
    api.assert_not_called()
    api.side_effect = [[], {**ASSIGNED, "assignees": []}]
    with pytest.raises(release.ReleaseError, match="did not assign"):
        schema_attention.notify(ENV, read=api)


def test_read_and_notification_jobs_have_separate_credentials():
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows"

    def load(name):
        return yaml.load((workflows / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    parent = load("commercial-image.yml")["jobs"]["schema-attention"]
    assert parent["needs"] == "build"
    assert parent["strategy"]["matrix"]["environment"] == ["staging", "production"]
    assert "SCHEMA_WINDOWS_ENABLED" not in parent["if"]
    called = load("commercial-schema-attention.yml")
    names = {"SCHEMA_ATTENTION_DATABASE_URL", "TABULA_RASA_RUNTIME_DATABASE_URL"}
    assert set(called["on"]["workflow_call"]["secrets"]) == names
    assert parent["secrets"] == {name: "${{ secrets." + name + " }}" for name in names}
    inspect, notify = called["jobs"].values()
    assert inspect["permissions"] == {"contents": "read"}
    assert inspect["environment"]["deployment"] == "false"
    assert "MIGRATION_DATABASE_URL" not in str(inspect)
    assert "environment" not in notify and "DATABASE_URL" not in str(notify)
    assert notify["permissions"]["issues"] == "write"
    assert notify["needs"] == "inspect" and "outputs.pending" in notify["if"]
