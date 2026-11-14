"""Verify that a bound Neon database has a usable point in retained history."""

import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from tabula_rasa.infrastructure.postgres.hosted import direct_neon_settings
from tabula_rasa.infrastructure.postgres.identity import database_url_identity
from tabula_rasa.migration.model import digest

from .release import NoRedirect, ReleaseError


def neon_read(path, token):
    request = Request("https://console.neon.tech/api/v2" + path,
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with build_opener(NoRedirect).open(request, timeout=30) as response:
        return json.load(response)


def wal_position(lsn):
    if not re.fullmatch(r'[0-9A-F]{1,8}/[0-9A-F]{1,8}', lsn):
        raise ValueError
    high, low = lsn.split('/')
    return int(high, 16) << 32 | int(low, 16)


def verify_checkpoint(point, environ, now, *, read=neon_read):
    try:
        url = direct_neon_settings({"TABULA_RASA_DATABASE_URL": environ["WINDOW_MIGRATION_DATABASE_URL"]}).url
        project, branch, endpoint = (environ["NEON_" + key] for key in ("PROJECT_ID", "BRANCH_ID", "ENDPOINT_ID"))
        token = environ["NEON_RELEASE_API_KEY"]
        fingerprint = environ["TABULA_RASA_DEPLOY_DATABASE_SHA256"]
        position = wal_position(point["lsn"])
        # PostgreSQL and the runner use independently synchronized clocks.
        age = now - datetime.fromisoformat(point["at"])
        if (not token or database_url_identity(url) != fingerprint
                or not all(re.fullmatch(r"[a-z0-9-]{1,60}", value) for value in (project, branch, endpoint))
                or not position
                or not -timedelta(seconds=5) <= age <= timedelta(minutes=15)):
            raise ValueError
        path = f"/projects/{project}"
        compute = read(f"{path}/endpoints/{endpoint}", token)["endpoint"]
        settings = read(path, token)["project"]
        branch_state = read(f"{path}/branches/{branch}", token)["branch"]
        if (compute["id"] != endpoint or compute["host"] != url.host or compute["branch_id"] != branch
                or compute["type"] != "read_write" or compute["disabled"] is not False
                or settings["id"] != project or int(settings["history_retention_seconds"]) < 3600
                or branch_state["id"] != branch):
            raise ValueError
        # A child must be recoverable from its own history after the fork.
        if branch_state.get('parent_id') and position <= wal_position(branch_state['parent_lsn']):
            raise ValueError
        query = urlencode({"db_name": url.database, "lsn": point["lsn"], "format": "sql"})
        schema = read(f"{path}/branches/{branch}/schema?{query}", token)["sql"]
        if not isinstance(schema, str) or "alembic_version" not in schema:
            raise ValueError
    except Exception:
        raise ReleaseError("Neon did not confirm retained history at the bound recovery point.") from None
    return {"recovery_sha256": digest([fingerprint, branch, point["lsn"], point["at"]]),
            "checkpoint_at": point["at"], "checkpoint_lsn": point["lsn"]}
