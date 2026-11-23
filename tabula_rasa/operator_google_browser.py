"""Short-lived Google consent and Picker browser interaction for local operators."""

from __future__ import annotations

import json
import secrets
import socket
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from tabula_rasa.google_drive import (
    GoogleDriveAuthorizationError,
    GoogleDriveConfigurationError,
    GoogleDriveOAuthConfig,
    authorization_url,
)
from tabula_rasa.google_drive.errors import DRIVE_ID_PATTERN, STATE_MAX_AGE_SECONDS
from tabula_rasa.google_drive_picker import picker_page


class _LoopbackServer(HTTPServer):
    deadline: float

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(max(0.001, min(self.deadline - monotonic(), 2)))
        return connection, address

    def handle_error(self, request, client_address) -> None:
        # Requests can contain authorization codes or Picker data.
        pass


class _IPv6LoopbackServer(_LoopbackServer):
    address_family = socket.AF_INET6


def local_redirect_uri(config: GoogleDriveOAuthConfig, requested: str | None) -> str:
    """Use an existing registered HTTP loopback callback and Picker origin."""
    for uri in config.redirect_uris:
        address = urlsplit(uri)
        if (
            (requested is None or requested == uri)
            and address.scheme == "http"
            and address.hostname in {"localhost", "127.0.0.1", "::1"}
            and address.username is None
            and address.password is None
        ):
            try:
                if address.port:
                    return uri
            except ValueError:
                continue
    raise GoogleDriveConfigurationError(
        "Select a configured HTTP loopback redirect URI with an explicit port."
    )


def _interaction(
    redirect_uri: str,
    path: str,
    browser_url: str,
    *,
    state: str | None = None,
    page: bytes | None = None,
    timeout: float,
) -> str:
    address = urlsplit(redirect_uri)
    origin = f"{address.scheme}://{address.netloc}"
    result: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args) -> None:
            pass

        def reply(self, status: int, body: bytes = b"", *, html: bool = False) -> None:
            self.send_response(status)
            self.send_header(
                "Content-Type", "text/html; charset=utf-8" if html else "text/plain"
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Referrer-Policy",
                "strict-origin-when-cross-origin" if html else "no-referrer",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            target = urlsplit(self.path)
            if self.headers.get("Host") != address.netloc or target.path != path:
                self.reply(404)
                return
            if page is not None:
                self.reply(200, page, html=True)
                return
            query = parse_qs(target.query)
            if query.get("state") != [state]:
                self.reply(400, b"Invalid authorization response.")
                return
            codes = query.get("code", [])
            result.append(
                codes[0] if len(codes) == 1 and "error" not in query else None
            )
            self.reply(200, b"Return to the terminal to continue.")

        def do_POST(self) -> None:
            if (
                page is None
                or self.path != path
                or self.headers.get("Host") != address.netloc
                or self.headers.get("Origin") != origin
                or self.headers.get_content_type() != "application/json"
            ):
                self.reply(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                selection = (
                    json.loads(self.rfile.read(size)) if 0 < size <= 1024 else False
                )
            except (ValueError, TimeoutError):
                selection = False
            if selection is not None and (
                not isinstance(selection, str)
                or not DRIVE_ID_PATTERN.fullmatch(selection)
            ):
                self.reply(400, b"Invalid Picker response.")
                return
            result.append(selection)
            self.reply(200, b"Return to the terminal to continue.")

    server_type = _IPv6LoopbackServer if address.hostname == "::1" else _LoopbackServer
    host = "::1" if address.hostname == "::1" else "127.0.0.1"
    try:
        server = server_type((host, address.port), Handler)
    except OSError:
        raise GoogleDriveConfigurationError(
            "The configured Google loopback port is unavailable. Close the other listener and retry."
        ) from None
    with server:
        server.deadline = monotonic() + timeout
        try:
            opened = webbrowser.open(browser_url)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            raise GoogleDriveAuthorizationError(
                "Could not open the browser. Set a default browser and retry."
            )
        while not result and (remaining := server.deadline - monotonic()) > 0:
            server.timeout = min(remaining, 0.25)
            server.handle_request()
        if not result:
            raise GoogleDriveAuthorizationError(
                "Google browser interaction timed out. Start it again."
            )
    if result[0] is None:
        raise GoogleDriveAuthorizationError(
            "Google browser interaction was cancelled or denied."
        )
    return result[0]


def request_google_code(
    config: GoogleDriveOAuthConfig,
    profile_key: str,
    redirect_uri: str,
    *,
    login_hint: str = "",
    timeout: float = STATE_MAX_AGE_SECONDS,
) -> str:
    """Return only a code bound to this exact consent attempt."""
    url = authorization_url(config, profile_key, redirect_uri, login_hint=login_hint)
    state = parse_qs(urlsplit(url).query)["state"][0]
    return _interaction(
        redirect_uri,
        urlsplit(redirect_uri).path or "/",
        url,
        state=state,
        timeout=timeout,
    )


def request_picker_id(
    config: GoogleDriveOAuthConfig,
    redirect_uri: str,
    kind: str,
    access_token: Callable[[], str],
    *,
    timeout: float = STATE_MAX_AGE_SECONDS,
) -> str:
    """Grant one selected file using the existing restricted Picker contract."""
    if not config.picker_ready:
        raise GoogleDriveConfigurationError(
            "Configure picker_api_key and project_number first."
        )
    address = urlsplit(redirect_uri)
    path = f"/picker/{secrets.token_urlsafe(24)}"
    return _interaction(
        redirect_uri,
        path,
        f"{address.scheme}://{address.netloc}{path}",
        page=picker_page(
            kind, access_token(), config.picker_api_key, config.project_number
        ),
        timeout=timeout,
    )
