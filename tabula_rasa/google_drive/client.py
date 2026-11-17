from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import secrets
import time
from typing import TYPE_CHECKING, Any

import requests

from .errors import (
    DRIVE_API_ROOT, DRIVE_ID_PATTERN, DRIVE_UPLOAD_ROOT, FOLDER_MIME_TYPE,
    HTTP_TIMEOUT_SECONDS, REVOCATION_ENDPOINT, TOKEN_ENDPOINT,
    GoogleDriveAccessError, GoogleDriveAuthorizationError, _clean_text,
    _json_response,
)

if TYPE_CHECKING:
    from .oauth import GoogleDriveOAuthConfig


_RATE_LIMIT_REASONS = ("rateLimitExceeded", "userRateLimitExceeded")
_QUOTA_LIMIT_REASONS = ("dailyLimitExceeded", "storageQuotaExceeded")


def _forbidden_reason(response: requests.Response) -> str:
    """Read a consistent reason from a small Drive error envelope."""
    if len(response.content) > 16_384:
        return ""
    try:
        payload = response.json()
    except (ValueError, RecursionError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    entries = error.get("errors") if isinstance(error, dict) else None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
        return ""
    first = entries[0]
    reason = first.get("reason") if isinstance(first, dict) else None
    if isinstance(reason, str) and all(
        isinstance(entry, dict) and entry.get("reason") == reason for entry in entries
    ):
        return reason
    return ""


def _escape_query_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


class PathName:
    """Minimal cross-platform basename parser for untrusted upload names."""

    def __init__(self, value: object) -> None:
        self.value = str(value or "").replace("\\", "/")

    @property
    def name(self) -> str:
        return self.value.rsplit("/", 1)[-1]


class _DriveClientCore:
    """Shared token, request, cache, and upload primitives for Drive domains."""

    def __init__(
        self,
        config: GoogleDriveOAuthConfig,
        profile_settings: Mapping[str, Any],
        *,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        list_cache_ttl_seconds: float = 10.0,
    ) -> None:
        from .oauth import validate_drive_profile_settings

        self.config = config
        self.profile_settings = validate_drive_profile_settings(profile_settings)
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.clock = clock
        self.list_cache_ttl_seconds = list_cache_ttl_seconds
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._list_cache: dict[
            tuple[str, int, str, bool],
            tuple[float, list[dict[str, Any]]],
        ] = {}


    def access_token(self) -> str:
        """Return a valid short-lived access token for Drive or Google Picker."""
        self._ensure_access_token()
        return self._access_token


    def revoke(self) -> None:
        """Revoke the profile refresh token at Google."""
        try:
            response = self.session.post(
                REVOCATION_ENDPOINT,
                data={"token": self.profile_settings["refresh_token"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise GoogleDriveAuthorizationError(
                "Google could not confirm token revocation. Retry disconnecting."
            ) from exc
        if response.status_code not in {200, 400}:
            raise GoogleDriveAuthorizationError(
                "Google could not confirm token revocation. Retry disconnecting."
            )


    def _refresh(self) -> None:
        try:
            response = self.session.post(
                TOKEN_ENDPOINT,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": self.profile_settings["refresh_token"],
                    "grant_type": "refresh_token",
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise GoogleDriveAuthorizationError(
                "Google Drive authorization is temporarily unavailable."
            ) from exc
        if response.status_code != 200:
            raise GoogleDriveAuthorizationError(
                "Google Drive authorization was revoked or expired. Reconnect this profile."
            )
        token = _json_response(response)
        access_token = _clean_text(token.get("access_token"))
        if not access_token:
            raise GoogleDriveAuthorizationError(
                "Google Drive authorization was revoked or expired. Reconnect this profile."
            )
        self._access_token = access_token
        try:
            expires_in = max(float(token.get("expires_in", 3600)), 60.0)
        except (TypeError, ValueError):
            expires_in = 3600.0
        self._access_token_expires_at = self.clock() + max(expires_in - 60.0, 1.0)


    def _ensure_access_token(self) -> None:
        if (
            not self._access_token
            or self.clock() >= self._access_token_expires_at
        ):
            self._refresh()


    def _request(
        self,
        method: str,
        url: str,
        *,
        expected: tuple[int, ...] = (200,),
        retry_transient: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        self._ensure_access_token()
        refreshed_after_unauthorized = False
        supplied_headers = dict(kwargs.pop("headers", {}))
        for attempt in range(4):
            headers = dict(supplied_headers)
            headers["Authorization"] = f"Bearer {self._access_token}"
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=HTTP_TIMEOUT_SECONDS,
                    **kwargs,
                )
            except requests.RequestException as exc:
                if not retry_transient or attempt == 3:
                    raise GoogleDriveAccessError(
                        "Google Drive is temporarily unavailable. Retry the request.",
                        retryable=True,
                    ) from exc
                self.sleeper(2**attempt)
                continue
            if response.status_code in expected:
                return response
            if response.status_code == 401 and not refreshed_after_unauthorized:
                self._refresh()
                refreshed_after_unauthorized = True
                continue
            reason = _forbidden_reason(response) if response.status_code == 403 else ""
            if reason in _QUOTA_LIMIT_REASONS:
                raise GoogleDriveAccessError(
                    "Google Drive has reached a quota or storage limit."
                )
            if response.status_code in {429, 500, 502, 503, 504} or reason in _RATE_LIMIT_REASONS:
                if retry_transient and attempt < 3:
                    self.sleeper(2**attempt)
                    continue
                raise GoogleDriveAccessError(
                    "Google Drive is temporarily unavailable. Retry the request.",
                    retryable=True,
                )
            if response.status_code in {401, 403}:
                raise GoogleDriveAuthorizationError(
                    "Google Drive access was revoked or is insufficient. Reconnect this profile."
                )
            raise GoogleDriveAccessError(
                "Google Drive could not complete the request. No success was reported."
            )
        raise GoogleDriveAccessError(
            "Google Drive is temporarily unavailable. Retry the request.",
            retryable=True,
        )


    def folder(self, folder_id: str) -> dict[str, Any]:
        """Validate and return one authorized Drive folder, including the My Drive alias."""
        if folder_id != 'root':
            folder_id = self._valid_folder_id(folder_id)
        response = self._request(
            "GET",
            f"{DRIVE_API_ROOT}/files/{folder_id}",
            params={
                "fields": "id,name,mimeType,trashed,webViewLink,parents,appProperties,capabilities(canAddChildren)",
                "supportsAllDrives": "true",
            },
        )
        folder = dict(_json_response(response))
        if folder.get("mimeType") != FOLDER_MIME_TYPE or folder.get("trashed"):
            raise GoogleDriveAccessError(
                "The selected Google Drive destination is not an available folder."
            )
        return folder


    @staticmethod
    def _valid_folder_id(folder_id: str) -> str:
        folder_id = str(folder_id)
        if not DRIVE_ID_PATTERN.fullmatch(folder_id):
            raise GoogleDriveAccessError("The selected Google Drive folder is invalid.")
        return folder_id


    def _invalidate_list_cache(self) -> None:
        self._list_cache.clear()


    def _list(
        self,
        query: str,
        *,
        page_size: int = 100,
        order_by: str = "",
        use_cache: bool = False,
        fetch_all: bool = False,
    ) -> list[dict[str, Any]]:
        cache_key = (query, page_size, order_by, fetch_all)
        if use_cache and self.list_cache_ttl_seconds > 0:
            cached = self._list_cache.get(cache_key)
            if cached and self.clock() - cached[0] < self.list_cache_ttl_seconds:
                return [dict(item) for item in cached[1]]
        params = {
            "q": query,
            "pageSize": min(max(page_size, 1), 1000),
            "fields": (
                "nextPageToken,files(id,name,mimeType,trashed,size,createdTime,modifiedTime,"
                "description,webViewLink,appProperties,parents)"
            ),
            "spaces": "drive",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if order_by:
            params["orderBy"] = order_by
        result: list[dict[str, Any]] = []
        page_token = ""
        seen_page_tokens: set[str] = set()
        while True:
            page_params = dict(params)
            if page_token:
                page_params["pageToken"] = page_token
            response = self._request(
                "GET",
                f"{DRIVE_API_ROOT}/files",
                params=page_params,
            )
            payload = _json_response(response)
            files = payload.get("files", [])
            if not isinstance(files, list):
                raise GoogleDriveAccessError(
                    "Google Drive returned an unreadable file listing."
                )
            result.extend(
                dict(item) for item in files if isinstance(item, Mapping)
            )
            if not fetch_all:
                break
            next_page_token = payload.get("nextPageToken")
            if next_page_token is None:
                break
            if (
                not isinstance(next_page_token, str)
                or not next_page_token
                or next_page_token in seen_page_tokens
            ):
                raise GoogleDriveAccessError(
                    "Google Drive returned an unreadable file listing."
                )
            seen_page_tokens.add(next_page_token)
            page_token = next_page_token
        if use_cache and self.list_cache_ttl_seconds > 0:
            self._list_cache[cache_key] = (self.clock(), result)
        return [dict(item) for item in result]


    def _multipart_create(
        self,
        metadata: Mapping[str, Any],
        content: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        self._invalidate_list_cache()
        boundary = f"tabula-rasa-{secrets.token_hex(16)}"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode("utf-8")
        body += json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        body += (
            f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        body += content
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")
        try:
            response = self._request(
                "POST",
                f"{DRIVE_UPLOAD_ROOT}/files",
                expected=(200, 201),
                retry_transient=False,
                params={
                    "uploadType": "multipart",
                    "fields": (
                        "id,name,mimeType,size,createdTime,modifiedTime,description,"
                        "webViewLink,appProperties"
                    ),
                    "supportsAllDrives": "true",
                },
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                data=body,
            )
            return dict(_json_response(response, retryable=True))
        except GoogleDriveAccessError as error:
            raise GoogleDriveAccessError(
                str(error),
                retryable=error.retryable,
                upload_attempted=True,
            ) from error


    def delete_file(self, file_id: str) -> None:
        """Delete a file created and managed by the app."""
        self._invalidate_list_cache()
        self._request(
            "DELETE",
            f"{DRIVE_API_ROOT}/files/{file_id}",
            expected=(200, 204),
            params={"supportsAllDrives": "true"},
        )


    def download_file(self, file_id: str) -> bytes:
        """Download an authorized file for validation or recovery."""
        response = self._request(
            "GET",
            f"{DRIVE_API_ROOT}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
        )
        return response.content
