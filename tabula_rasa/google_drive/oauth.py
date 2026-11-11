from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from .errors import (
    AUTHORIZATION_ENDPOINT, DRIVE_API_ROOT, DRIVE_ID_PATTERN, DRIVE_SCOPE,
    HTTP_TIMEOUT_SECONDS, PROFILE_KEY_PATTERN, STATE_MAX_AGE_SECONDS,
    TOKEN_ENDPOINT, GoogleDriveAuthorizationError, GoogleDriveConfigurationError,
    _clean_text, _json_response,
)

@dataclass(frozen=True)
class GoogleDriveOAuthConfig:
    """Application-wide OAuth client and Picker configuration."""

    client_id: str
    client_secret: str
    redirect_uris: tuple[str, ...]
    picker_api_key: str = ""
    project_number: str = ""

    @property
    def picker_ready(self) -> bool:
        return bool(self.picker_api_key and self.project_number)


def _mapping(value: object, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GoogleDriveConfigurationError(message)
    return value


def _validate_redirect_uri(value: object) -> str:
    uri = _clean_text(value, maximum=2048)
    parsed = urlparse(uri)
    is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not uri
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or parsed.scheme not in ({"http", "https"} if is_loopback else {"https"})
    ):
        raise GoogleDriveConfigurationError(
            "Google Drive OAuth redirect URIs are invalid."
        )
    return uri


def load_oauth_config(secrets_mapping: Mapping[str, Any]) -> GoogleDriveOAuthConfig:
    """Load and validate the app-wide Google Drive OAuth configuration."""
    raw = _mapping(
        secrets_mapping.get("google_drive_oauth"),
        "Google Drive OAuth is not configured for this deployment.",
    )
    client_id = _clean_text(raw.get("client_id"))
    client_secret = _clean_text(raw.get("client_secret"))
    redirect_values = raw.get("redirect_uris")
    if isinstance(redirect_values, str):
        redirect_values = [redirect_values]
    if (
        not client_id
        or not client_secret
        or not isinstance(redirect_values, Sequence)
        or isinstance(redirect_values, (bytes, bytearray))
    ):
        raise GoogleDriveConfigurationError(
            "Google Drive OAuth client settings are incomplete."
        )
    redirect_uris = tuple(
        dict.fromkeys(_validate_redirect_uri(value) for value in redirect_values)
    )
    if not redirect_uris:
        raise GoogleDriveConfigurationError(
            "Google Drive OAuth needs at least one redirect URI."
        )
    picker_api_key = _clean_text(raw.get("picker_api_key"))
    project_number = _clean_text(raw.get("project_number"), maximum=32)
    if bool(picker_api_key) != bool(project_number):
        raise GoogleDriveConfigurationError(
            "Google Picker needs both picker_api_key and project_number."
        )
    return GoogleDriveOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uris=redirect_uris,
        picker_api_key=picker_api_key,
        project_number=project_number,
    )


def select_redirect_uri(
    config: GoogleDriveOAuthConfig,
    current_url: str,
) -> str:
    """Select the registered redirect URI matching the current app origin."""
    current = urlparse(str(current_url))
    for candidate in config.redirect_uris:
        parsed = urlparse(candidate)
        if (parsed.scheme, parsed.netloc) == (current.scheme, current.netloc):
            return candidate
    raise GoogleDriveConfigurationError(
        "This app URL is not listed in google_drive_oauth.redirect_uris."
    )


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise GoogleDriveAuthorizationError(
            "The Google authorization state is invalid. Start the connection again."
        ) from exc


def create_oauth_state(
    config: GoogleDriveOAuthConfig,
    profile_key: str,
    redirect_uri: str,
    *,
    now: int | None = None,
) -> str:
    """Return a signed, short-lived state value for one business profile."""
    if not PROFILE_KEY_PATTERN.fullmatch(profile_key):
        raise GoogleDriveConfigurationError("The business profile key is invalid.")
    if redirect_uri not in config.redirect_uris:
        raise GoogleDriveConfigurationError(
            "The selected Google OAuth redirect URI is not registered in app secrets."
        )
    payload = {
        "v": 1,
        "profile": profile_key,
        "redirect_uri": redirect_uri,
        "issued_at": int(time.time() if now is None else now),
        "nonce": secrets.token_urlsafe(18),
    }
    encoded = _urlsafe_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        config.client_secret.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_urlsafe_encode(signature)}"


def verify_oauth_state(
    config: GoogleDriveOAuthConfig,
    state: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify and return a signed OAuth state payload."""
    try:
        encoded, supplied_signature = state.split(".", 1)
    except ValueError as exc:
        raise GoogleDriveAuthorizationError(
            "The Google authorization state is invalid. Start the connection again."
        ) from exc
    expected_signature = hmac.new(
        config.client_secret.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(
        _urlsafe_encode(expected_signature),
        supplied_signature,
    ):
        raise GoogleDriveAuthorizationError(
            "The Google authorization state is invalid. Start the connection again."
        )
    try:
        payload = json.loads(_urlsafe_decode(encoded).decode("utf-8"))
        issued_at = int(payload["issued_at"])
        profile_key = str(payload["profile"])
        redirect_uri = str(payload["redirect_uri"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise GoogleDriveAuthorizationError(
            "The Google authorization state is invalid. Start the connection again."
        ) from exc
    current_time = int(time.time() if now is None else now)
    if (
        payload.get("v") != 1
        or not PROFILE_KEY_PATTERN.fullmatch(profile_key)
        or redirect_uri not in config.redirect_uris
        or issued_at > current_time + 30
        or current_time - issued_at > STATE_MAX_AGE_SECONDS
    ):
        raise GoogleDriveAuthorizationError(
            "The Google authorization request expired. Start the connection again."
        )
    return payload


def authorization_url(
    config: GoogleDriveOAuthConfig,
    profile_key: str,
    redirect_uri: str,
    *,
    login_hint: str = "",
    now: int | None = None,
) -> str:
    """Build a Google web-server authorization URL with offline access."""
    state = create_oauth_state(
        config,
        profile_key,
        redirect_uri,
        now=now,
    )
    parameters = {
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    if login_hint:
        parameters["login_hint"] = login_hint
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(parameters)}"


def _authorized_user(
    access_token: str,
    *,
    session: requests.Session,
) -> Mapping[str, Any]:
    try:
        response = session.get(
            f"{DRIVE_API_ROOT}/about",
            params={"fields": "user(displayName,emailAddress,permissionId)"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GoogleDriveAuthorizationError(
            "Google Drive could not confirm the authorized account."
        ) from exc
    if response.status_code != 200:
        raise GoogleDriveAuthorizationError(
            "Google Drive could not confirm the authorized account."
        )
    user = _json_response(response).get("user")
    if not isinstance(user, Mapping):
        raise GoogleDriveAuthorizationError(
            "Google Drive could not confirm the authorized account."
        )
    return user


def exchange_authorization_code(
    config: GoogleDriveOAuthConfig,
    code: str,
    redirect_uri: str,
    *,
    session: requests.Session | None = None,
    code_verifier: str | None = None,
    include_account_id: bool = False,
) -> dict[str, Any]:
    """Exchange an authorization code and return sanitized persistent settings."""
    if not _clean_text(code):
        raise GoogleDriveAuthorizationError(
            "Google did not return an authorization code. Start the connection again."
        )
    client = session or requests.Session()
    try:
        response = client.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                **({"code_verifier": code_verifier} if code_verifier else {}),
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GoogleDriveAuthorizationError(
            "Google authorization is temporarily unavailable. Retry the connection."
        ) from exc
    if response.status_code != 200:
        raise GoogleDriveAuthorizationError(
            "Google rejected the authorization response. Start the connection again."
        )
    token = _json_response(response)
    refresh_token = _clean_text(token.get("refresh_token"))
    access_token = _clean_text(token.get("access_token"))
    if not refresh_token or not access_token:
        raise GoogleDriveAuthorizationError(
            "Google did not issue offline access. Revoke the prior grant and reconnect."
        )
    granted_scopes = str(token.get("scope") or DRIVE_SCOPE).split()
    if DRIVE_SCOPE not in granted_scopes:
        raise GoogleDriveAuthorizationError(
            "Google Drive file access was not granted. Reconnect and approve access."
        )
    user = _authorized_user(access_token, session=client)
    settings = {
        "authorized_email": _clean_text(user.get("emailAddress"), maximum=320),
        "authorized_name": _clean_text(user.get("displayName"), maximum=200),
        "refresh_token": refresh_token,
        "token_uri": TOKEN_ENDPOINT,
        "scopes": sorted(set(granted_scopes)),
    }
    if include_account_id:
        settings["authorized_account_id"] = _clean_text(user.get("permissionId"), maximum=256)
    return settings


def validate_drive_profile_settings(value: object) -> dict[str, Any]:
    """Validate persisted per-profile Drive authorization and folder settings."""
    raw = _mapping(value, "Google Drive profile settings are invalid.")
    allowed = {
        "authorized_email",
        "authorized_name",
        "refresh_token",
        "token_uri",
        "scopes",
        "storage_root",
        "receipt_folder",
        "backup_folder",
    }
    if set(raw) - allowed:
        raise GoogleDriveConfigurationError(
            "Google Drive profile settings contain unsupported fields."
        )
    if (
        raw.get("storage_root") is None
        and any(raw.get(field) is not None for field in ("receipt_folder", "backup_folder"))
    ):
        raise GoogleDriveConfigurationError(
            "Google Drive receipt and backup folders require a storage root."
        )
    refresh_token = _clean_text(raw.get("refresh_token"))
    token_uri = _clean_text(raw.get("token_uri"))
    scopes = raw.get("scopes")
    if (
        not refresh_token
        or token_uri != TOKEN_ENDPOINT
        or not isinstance(scopes, Sequence)
        or isinstance(scopes, (str, bytes, bytearray))
    ):
        raise GoogleDriveConfigurationError(
            "Google Drive profile authorization is incomplete."
        )
    normalized_scopes = sorted(
        {
            scope
            for raw_scope in scopes
            if (scope := _clean_text(raw_scope, maximum=500))
        }
    )
    if DRIVE_SCOPE not in normalized_scopes:
        raise GoogleDriveConfigurationError(
            "Google Drive profile authorization lacks drive.file access."
        )
    normalized: dict[str, Any] = {
        "authorized_email": _clean_text(raw.get("authorized_email"), maximum=320),
        "authorized_name": _clean_text(raw.get("authorized_name"), maximum=200),
        "refresh_token": refresh_token,
        "token_uri": TOKEN_ENDPOINT,
        "scopes": normalized_scopes,
    }
    for field in ("storage_root", "receipt_folder", "backup_folder"):
        folder = raw.get(field)
        if folder is None:
            continue
        folder_mapping = _mapping(folder, "Google Drive folder settings are invalid.")
        folder_id = _clean_text(folder_mapping.get("id"), maximum=256)
        folder_name = _clean_text(folder_mapping.get("name"), maximum=255)
        if (
            set(folder_mapping) != {"id", "name"}
            or not DRIVE_ID_PATTERN.fullmatch(folder_id)
            or not folder_name
        ):
            raise GoogleDriveConfigurationError(
                "Google Drive folder settings are invalid."
            )
        normalized[field] = {"id": folder_id, "name": folder_name}
    return normalized


def profile_google_credentials(settings: Mapping[str, Any]):
    """Build refreshable credentials from one profile and the shared OAuth client.

    drive.file authorizes both Sheets and Drive for explicitly selected files.
    Read-only utilities enforce read-only operations, not a second OAuth grant.
    """
    from google.oauth2.credentials import Credentials

    config = load_oauth_config(settings)
    grant = validate_drive_profile_settings(settings.get("google_drive"))
    return Credentials(
        token=None,
        refresh_token=grant["refresh_token"],
        token_uri=TOKEN_ENDPOINT,
        client_id=config.client_id,
        client_secret=config.client_secret,
        scopes=[DRIVE_SCOPE],
    )
