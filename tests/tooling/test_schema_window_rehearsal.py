"""Disposable release rehearsal with a simulated provider and real app/database containers."""

import json
import os
import subprocess
from contextlib import ExitStack
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import uuid4

import pytest

from tabula_rasa.deployment import release, schema_window
from tabula_rasa.deployment.smoke import named_smoke
from tabula_rasa.migration.model import digest
from tests.tooling.test_schema_window import ENV, NOW, ORIGIN, PLAN, PRIVATE, SERVICE, Staging


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SCHEMA_WINDOW_REHEARSAL") != "1",
    reason="Requires the explicit isolated Docker rehearsal",
)

BASELINE = "989854605508eb0e5f2691c99343ff4fb1ac155fe286d1bab20885be9fadf611"
CANDIDATE = "f298f6c9f1d4a1c4bcfbbd79fade842f526b0f3c6674ac7d9a7d2f7765ec3dfd"
TLS_POSTGRES = (
    "openssl req -new -x509 -nodes -days 1 -subj /CN=disposable-postgres -newkey rsa:2048 "
    "-keyout /tmp/drill.key -out /tmp/drill.crt >/dev/null 2>&1 && "
    "chown postgres:postgres /tmp/drill.key && chmod 600 /tmp/drill.key && "
    "exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/drill.crt "
    "-c ssl_key_file=/tmp/drill.key"
)


def docker(*args, env=None, data=None, check=True):
    result = subprocess.run(
        ["docker", *args], env=env, input=data, capture_output=True, text=True, timeout=180,
    )
    if check and result.returncode:
        raise RuntimeError(f"Disposable Docker operation failed: {args[0]}.")
    return result


def wait_for(probe, description):
    deadline = monotonic() + 35
    while monotonic() < deadline:
        if probe():
            return
        sleep(0.25)
    pytest.fail(f"Timed out waiting for {description}.")


class Rehearsal(Staging):
    def __init__(self, stack, repository, metadata):
        image, prior = metadata
        super().__init__(image, prior)
        self.stack = stack
        self.metadata = metadata
        self.network = "schema-window-" + uuid4().hex[:12]
        self.database = self.network + "-db"
        self.app = self.network + "-app"
        self.app_port = None
        hostname = self.network + ".neon.tech"
        owner_password, runtime_password = uuid4().hex, uuid4().hex
        self.owner_url = f"postgresql://owner:{owner_password}@{hostname}/drill?sslmode=require"
        self.runtime_url = f"postgresql://runtime:{runtime_password}@{hostname}/drill?sslmode=require"
        self.settings["DATABASE_URL"] = self.runtime_url
        self.phases = []
        fingerprint = digest([hostname, 5432, "drill"])
        self.environ = {
            **ENV, "GITHUB_REPOSITORY": repository, "GH_TOKEN": os.environ["GH_TOKEN"],
            "TABULA_RASA_DEPLOY_DATABASE_SHA256": fingerprint,
            "COMMERCIAL_SCHEMA_WINDOW": json.dumps({
                **PLAN, "image": image, "previous_image": prior, "database_sha256": fingerprint,
            }),
        }
        docker("network", "create", self.network)
        stack.callback(docker, "network", "rm", self.network)
        stack.callback(docker, "rm", "-f", "-v", self.database, check=False)
        stack.callback(docker, "rm", "-f", self.app, check=False)
        docker(
            "run", "--pull", "never", "-d", "--name", self.database, "--network", self.network,
            "--network-alias", hostname, "-e", "POSTGRES_PASSWORD", "--entrypoint", "sh", "postgres:18.6",
            "-c", TLS_POSTGRES, env={**os.environ, "POSTGRES_PASSWORD": uuid4().hex},
        )
        wait_for(lambda: docker("exec", self.database, "pg_isready", "-h", "127.0.0.1",
                                "-U", "postgres", check=False).returncode == 0, "disposable PostgreSQL")
        self.sql(f"""
CREATE ROLE owner LOGIN PASSWORD '{owner_password}';
CREATE ROLE runtime LOGIN PASSWORD '{runtime_password}';
CREATE DATABASE drill OWNER owner;
\\connect drill
SET ROLE owner;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO runtime;
""", database="postgres")
        result = self.image_python(prior, {"TABULA_RASA_DATABASE_URL": self.owner_url}, "-c", (
            "from tabula_rasa.infrastructure.postgres.config import DatabaseSettings; "
            "from tabula_rasa.infrastructure.postgres.migrations import upgrade; "
            "from tabula_rasa.infrastructure.postgres.runtime import Database; "
            "upgrade(Database(DatabaseSettings.from_environment(required=True)))"
        ))
        assert result.returncode == 0, "Pinned baseline could not initialize the disposable database"
        assert self.sql("SELECT version_num FROM alembic_version;") == "0020_book_currency"
        self.start_app(prior)
        self.public = self.ingress(public=True)
        self.private = self.ingress(public=False)

    def sql(self, statement, *, database="drill"):
        return docker(
            "exec", "-i", self.database, "psql", "-qAt", "-U", "postgres", "-d", database,
            "-v", "ON_ERROR_STOP=1", data=statement + "\n",
        ).stdout.strip()

    def image_python(self, image, settings, *args):
        return docker(
            "run", "--rm", "--pull", "never", "--network", self.network,
            *(part for key in settings for part in ("-e", key)), image, "python", *args,
            env={**os.environ, **settings}, check=False,
        )

    def start_app(self, image):
        settings = {
            "TABULA_RASA_ENVIRONMENT": "staging",
            "TABULA_RASA_PUBLIC_ORIGIN": ORIGIN,
            "TABULA_RASA_ACCESS_MODE": "named_user",
            "TABULA_RASA_DATABASE_URL": self.runtime_url,
            "TABULA_RASA_OIDC_ISSUER": "https://identity.example.test",
            "TABULA_RASA_OIDC_CLIENT_ID": "fixture",
            "TABULA_RASA_OIDC_CLIENT_SECRET": "fixture",
            "TABULA_RASA_SESSION_GENERATION": "isolated-schema-rehearsal-fixture",
        }
        docker(
            "run", "--pull", "never", "-d", "--name", self.app, "--network", self.network,
            "-p", "127.0.0.1::10000", *(part for key in settings for part in ("-e", key)), image,
            env={**os.environ, **settings},
        )
        self.app_port = int(docker("port", self.app, "10000/tcp").stdout.rsplit(":", 1)[1])

        def ready():
            try:
                with urlopen(f"http://127.0.0.1:{self.app_port}/health/ready", timeout=2) as response:
                    return response.status == 200
            except OSError:
                status = docker("inspect", "-f", "{{.State.Status}}", self.app).stdout.strip()
                if status == "exited":
                    logs = docker("logs", "--tail", "20", self.app)
                    pytest.fail(f"Pinned app exited during startup.\n{logs.stdout}{logs.stderr}")
                return False

        wait_for(ready, "pinned app readiness")

    def ingress(self, *, public):
        lab = self

        class Proxy(BaseHTTPRequestHandler):
            def do_GET(self):
                if (public and lab.maintenance) or lab.app_port is None:
                    self.send_error(503)
                    return
                upstream = HTTPConnection("127.0.0.1", lab.app_port, timeout=5)
                try:
                    headers = dict(self.headers)
                    headers["Host"] = ORIGIN.removeprefix("https://")
                    upstream.request("GET", self.path, headers=headers)
                    response = upstream.getresponse()
                    payload = response.read()
                    self.send_response(response.status)
                    for header in ("Content-Type", "Cache-Control", "WWW-Authenticate"):
                        if value := response.getheader(header):
                            self.send_header(header, value)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    upstream.close()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        self.stack.callback(server.server_close)
        self.stack.callback(server.shutdown)
        Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_port}"

    def read(self, path, token, *, body=None, method=None, no_content=False):
        result = super().read(path, token, body=body, method=method, no_content=no_content)
        if path == f"/services/{SERVICE}/suspend":
            docker("stop", self.app)
            docker("rm", self.app)
            self.app_port = None
        elif path == f"/services/{SERVICE}/resume":
            self.start_app(self.configured_image)
        return result

    def migrate(self, image, candidate, source, role, environ, *, apply):
        if apply:
            assert self.suspended and self.maintenance and self.app_port is None
            schema_window.public_closed(self.public)
        result = self.image_python(image, {
            "TABULA_RASA_ENVIRONMENT": "staging",
            "TABULA_RASA_DATABASE_URL": self.owner_url,
            "TABULA_RASA_DEPLOY_DATABASE_SHA256": environ["TABULA_RASA_DEPLOY_DATABASE_SHA256"],
            "WINDOW_SOURCE_SCHEMA": source, "WINDOW_TARGET_SCHEMA": candidate["schema"],
            "WINDOW_RELEASE": candidate["release"], "WINDOW_RUNTIME_ROLE": role,
        }, "-m", "tabula_rasa.deployment.schema_migration", "apply" if apply else "inspect")
        if result.returncode:
            raise release.ReleaseError("Packaged migration command failed.")
        self.schema = self.sql("SELECT version_num FROM alembic_version;")

    def smoke_app(self, origin, revision):
        assert origin == PRIVATE
        if self.maintenance:
            schema_window.public_closed(self.public)
        named_smoke(self.private, revision)

    def rehearse(self, environ):
        return schema_window.run(
            environ, read=self.read, metadata=lambda image, _: self.metadata[image],
            check_database=self.migrate, smoke_check=self.smoke_app,
            check_closed=lambda _: schema_window.public_closed(self.public),
            check_reopened=lambda _, revision: schema_window.public_reopened(self.public, revision),
            now=lambda: NOW, emit=lambda event: self.phases.append(event["status"]),
        )


@pytest.fixture(scope="module")
def release_images():
    repository = os.environ["GITHUB_REPOSITORY"]
    registry = release.commercial_image_repository(repository)
    images = [f"{registry}@sha256:{value}" for value in (CANDIDATE, BASELINE)]
    metadata = {image: release.image_metadata(image, registry) for image in images}
    for image in images:
        docker("pull", "--platform", "linux/amd64", image)
    assert [info["schema"] for info in metadata.values()] == ["0021_customer_lifecycle", "0020_book_currency"]
    return repository, metadata


@pytest.fixture
def lab(release_images):
    with ExitStack() as stack:
        yield Rehearsal(stack, *release_images)


def test_schema_window_reopens_after_real_private_smoke(lab):
    prior = lab.metadata[lab.prior_image]["release"]
    with pytest.raises(release.ReleaseError, match="binding has changed"):
        lab.rehearse({**lab.environ, "RENDER_SERVICE_ID": "srv-wrong"})
    assert not lab.actions
    schema_window.public_reopened(lab.public, prior)
    result = lab.rehearse(lab.environ)
    assert result["status"] == "smoke_passed" and result["image"] == lab.image
    assert lab.schema == "0021_customer_lifecycle"
    assert lab.phases.index("private_smoke_passed") < lab.phases.index("ingress_reopened")


def test_failed_migration_keeps_ingress_closed_until_compatible_recovery(lab):
    lab.sql("SET ROLE owner; CREATE TABLE customer_lifecycle_events (sentinel text);")
    with pytest.raises(release.ReleaseError, match="Packaged migration command failed.*Keep ingress restricted"):
        lab.rehearse(lab.environ)
    assert lab.phases[-1] == "maintenance_failed"
    assert "ingress_reopened" not in lab.phases
    assert lab.maintenance and lab.suspended and lab.app_port is None
    schema_window.public_closed(lab.public)
    with pytest.raises(HTTPError, match="503"):
        urlopen(lab.private + "/version", timeout=5)
    assert lab.sql("SELECT version_num FROM alembic_version;") == "0020_book_currency"
    assert lab.sql("SELECT to_regclass('customer_lifecycle_requests') IS NULL;") == "t"

    # Operator recovery keeps the verified compatible image private until smoke passes.
    lab.start_app(lab.prior_image)
    prior = lab.metadata[lab.prior_image]["release"]
    lab.smoke_app(PRIVATE, prior)
    lab.maintenance = False
    schema_window.public_reopened(lab.public, prior)
