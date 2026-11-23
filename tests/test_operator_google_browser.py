from concurrent.futures import ThreadPoolExecutor
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tabula_rasa.google_drive import (
    GoogleDriveAuthorizationError,
    GoogleDriveConfigurationError,
    GoogleDriveOAuthConfig,
)
from tabula_rasa import operator_google_browser as browser


urlopen = build_opener(ProxyHandler({})).open


@pytest.fixture
def config():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return GoogleDriveOAuthConfig(
        "test-client",
        "private-client-secret",
        (f"http://127.0.0.1:{port}/callback", "https://example.test/callback"),
        "restricted-picker-key",
        "123",
    )


def assert_closed(url):
    with pytest.raises(URLError):
        urlopen(url, timeout=1)


@pytest.mark.parametrize("outcome", ["accepted", "denied", "timeout", "interrupted"])
def test_consent_only_accepts_current_attempt_and_closes_listener(
    monkeypatch,
    config,
    capsys,
    outcome,
):
    redirect = config.redirect_uris[0]

    def deliver(authorization_url):
        state = parse_qs(urlsplit(authorization_url).query)["state"][0]
        with pytest.raises(HTTPError, match="400"):
            urlopen(f"{redirect}?state=wrong&code=wrong", timeout=2)
        callback = (
            {"code": "private-authorization-code"}
            if outcome == "accepted"
            else {"error": "access_denied"}
        )
        with urlopen(
            f"{redirect}?{urlencode({'state': state, **callback})}", timeout=2
        ) as response:
            assert response.read() == b"Return to the terminal to continue."

    with ThreadPoolExecutor() as executor:
        submitted = []

        def launch(url):
            if outcome == "interrupted":
                raise KeyboardInterrupt
            if outcome != "timeout":
                submitted.append(executor.submit(deliver, url))
            return True

        monkeypatch.setattr(browser.webbrowser, "open", launch)
        timeout = 0.05 if outcome == "timeout" else 3
        if outcome == "accepted":
            assert (
                browser.request_google_code(config, "north", redirect, timeout=timeout)
                == "private-authorization-code"
            )
        elif outcome == "interrupted":
            with pytest.raises(KeyboardInterrupt):
                browser.request_google_code(config, "north", redirect, timeout=timeout)
        else:
            with pytest.raises(GoogleDriveAuthorizationError, match="denied|timed out"):
                browser.request_google_code(config, "north", redirect, timeout=timeout)
        for future in submitted:
            future.result()

    assert_closed(redirect)
    assert capsys.readouterr() == ("", "")
    if outcome == "accepted":
        monkeypatch.setattr(browser.webbrowser, "open", lambda _: True)
        with pytest.raises(GoogleDriveAuthorizationError, match="timed out"):
            browser.request_google_code(config, "north", redirect, timeout=0.05)
    with pytest.raises(GoogleDriveConfigurationError, match="loopback"):
        browser.local_redirect_uri(config, config.redirect_uris[1])


@pytest.mark.parametrize("selection", ["blank-workbook-123", None])
def test_picker_payload_and_shutdown(monkeypatch, config, capsys, selection):
    redirect = config.redirect_uris[0]

    def deliver(url):
        address = urlsplit(url)
        origin = f"{address.scheme}://{address.netloc}"
        with urlopen(url, timeout=2) as response:
            assert response.headers.get("Cache-Control") == "no-store"
            page = response.read().decode()
        payload = page.split('id="picker-data">')[1].split("</script>")[0]
        assert json.loads(payload) == {
            "accessToken": "picker-access-token",
            "apiKey": "restricted-picker-key",
            "appId": "123",
            "kind": "spreadsheet",
        }
        assert "private-client-secret" not in page + url
        assert "picker-access-token" not in url
        for request_origin, expected in (("http://evil.test", 404), (origin, 200)):
            request = Request(
                url,
                data=json.dumps(selection).encode(),
                headers={"Origin": request_origin, "Content-Type": "application/json"},
            )
            if expected == 404:
                with pytest.raises(HTTPError, match="404"):
                    urlopen(request, timeout=2)
            else:
                with urlopen(request, timeout=2) as response:
                    assert response.read() == b"Return to the terminal to continue."

    with ThreadPoolExecutor() as executor:
        pending = []

        def launch(url):
            pending.append(executor.submit(deliver, url))
            return True

        monkeypatch.setattr(browser.webbrowser, "open", launch)
        if selection:
            assert (
                browser.request_picker_id(
                    config,
                    redirect,
                    "spreadsheet",
                    lambda: "picker-access-token",
                    timeout=3,
                )
                == selection
            )
        else:
            with pytest.raises(GoogleDriveAuthorizationError, match="cancelled"):
                browser.request_picker_id(
                    config,
                    redirect,
                    "spreadsheet",
                    lambda: "picker-access-token",
                    timeout=3,
                )
        pending[0].result()
    assert_closed(redirect)
    assert capsys.readouterr() == ("", "")
