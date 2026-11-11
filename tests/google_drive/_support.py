"""Shared fake Drive sessions for responsibility-focused tests."""

from __future__ import annotations

import json
from typing import Any

import requests

from tabula_rasa.google_drive import DRIVE_SCOPE, GoogleDriveOAuthConfig

class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any] | None = None,
        *,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        self.content = content

    def json(self) -> dict[str, Any]:
        return self.payload


def drive_error(status, *reasons):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"error": {
        "code": status,
        "message": "synthetic-private-provider-response",
        "errors": [{"reason": reason} for reason in reasons],
    }}).encode()
    return response


class AuthorizationSession:
    def __init__(self) -> None:
        self.posted: dict[str, Any] | None = None

    def post(self, _url: str, **kwargs: Any) -> FakeResponse:
        self.posted = kwargs
        return FakeResponse(
            200,
            {
                "access_token": "short-lived-access",
                "refresh_token": "durable-refresh",
                "scope": DRIVE_SCOPE,
            },
        )

    def get(self, _url: str, **_kwargs: Any) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "user": {
                    "displayName": "North Owner",
                    "emailAddress": "north@example.test",
                    "permissionId": "synthetic-north-account",
                }
            },
        )


class DriveSession:
    def __init__(
        self,
        responses: list[FakeResponse | requests.RequestException],
        *,
        access_token: str = "short-lived-access",
    ) -> None:
        self.responses = list(responses)
        self.access_token = access_token
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.token_refreshes = 0
        self.refresh_tokens: list[str] = []

    def post(self, _url: str, **kwargs: Any) -> FakeResponse:
        self.token_refreshes += 1
        self.refresh_tokens.append(str(kwargs["data"]["refresh_token"]))
        return FakeResponse(
            200,
            {
                "access_token": self.access_token,
                "expires_in": 3600,
            },
        )

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, requests.RequestException):
            raise response
        return response


def oauth_config() -> GoogleDriveOAuthConfig:
    return GoogleDriveOAuthConfig(
        client_id="client.apps.googleusercontent.com",
        client_secret="client-secret",
        redirect_uris=(
            "http://localhost:8501/",
            "https://tabula-rasa.streamlit.app/",
        ),
        picker_api_key="picker-key",
        project_number="1234567890",
    )


def drive_settings(
    *,
    email: str = "north@example.test",
    refresh_token: str = "durable-refresh",
) -> dict[str, Any]:
    return {
        "authorized_email": email,
        "authorized_name": "North Owner",
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": [DRIVE_SCOPE],
    }


def managed_folder(
    folder_id: str,
    name: str,
    *,
    profile_key: str = "north",
    storage_kind: str,
    root_id: str | None = None,
) -> dict[str, Any]:
    properties = {
        "tabulaRasaKind": storage_kind,
        "tabulaRasaProfile": profile_key,
    }
    folder: dict[str, Any] = {
        "id": folder_id,
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "trashed": False,
        "appProperties": properties,
    }
    if root_id is not None:
        properties["tabulaRasaStorageRoot"] = root_id
        folder["parents"] = [root_id]
    return folder
