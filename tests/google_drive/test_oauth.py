from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from tabula_rasa.google_drive import (
    DRIVE_SCOPE, GoogleDriveAuthorizationError, authorization_url,
    create_oauth_state, exchange_authorization_code, load_oauth_config,
    select_redirect_uri, validate_drive_profile_settings, verify_oauth_state,
)

from ._support import AuthorizationSession, drive_settings, oauth_config

class TestGoogleDriveOAuth:
    def test_loads_app_wide_config_and_selects_current_origin(self) -> None:
        config = load_oauth_config(
            {
                "google_drive_oauth": {
                    "client_id": "client.apps.googleusercontent.com",
                    "client_secret": "secret",
                    "redirect_uris": [
                        "http://localhost:8501/",
                        "https://tabula-rasa.streamlit.app/",
                    ],
                    "picker_api_key": "picker",
                    "project_number": "12345",
                }
            }
        )

        assert (
            select_redirect_uri(
                config,
                "https://tabula-rasa.streamlit.app/?page=profiles",
            )
            == "https://tabula-rasa.streamlit.app/"
        )
        assert config.picker_ready


    def test_state_is_profile_scoped_signed_and_expires(self) -> None:
        config = oauth_config()
        state = create_oauth_state(
            config,
            "north",
            "http://localhost:8501/",
            now=1_000,
        )

        payload = verify_oauth_state(config, state, now=1_100)
        assert payload["profile"] == "north"
        assert payload["redirect_uri"] == "http://localhost:8501/"

        with pytest.raises(GoogleDriveAuthorizationError):
            verify_oauth_state(config, state + "tampered", now=1_100)
        with pytest.raises(GoogleDriveAuthorizationError):
            verify_oauth_state(config, state, now=2_000)


    def test_authorization_requests_narrow_scope_and_offline_access(self) -> None:
        url = authorization_url(
            oauth_config(),
            "north",
            "http://localhost:8501/",
            now=1_000,
        )
        query = parse_qs(urlparse(url).query)

        assert query["scope"] == [DRIVE_SCOPE]
        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"]
        assert query["redirect_uri"] == ["http://localhost:8501/"]


    def test_code_exchange_retains_refresh_token_and_safe_identity(self) -> None:
        session = AuthorizationSession()

        settings = exchange_authorization_code(
            oauth_config(),
            "authorization-code",
            "http://localhost:8501/",
            session=session,
            code_verifier='synthetic-pkce-verifier',
            include_account_id=True,
        )

        assert settings == {**drive_settings(), 'authorized_account_id': 'synthetic-north-account'}
        assert session.posted is not None
        assert session.posted["data"]["grant_type"] == "authorization_code"
        assert session.posted['data']['code_verifier'] == 'synthetic-pkce-verifier'


    def test_profile_settings_require_drive_file_scope(self) -> None:
        invalid = drive_settings()
        invalid["scopes"] = ["https://www.googleapis.com/auth/drive"]

        with pytest.raises(Exception, match="drive.file"):
            validate_drive_profile_settings(invalid)


    def test_profile_settings_require_a_root_for_storage_children(self) -> None:
        invalid = drive_settings()
        invalid["receipt_folder"] = {"id": "receiptfolder123", "name": "Receipts"}

        with pytest.raises(Exception, match="storage root"):
            validate_drive_profile_settings(invalid)
