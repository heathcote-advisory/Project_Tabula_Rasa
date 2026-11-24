"""Mode-specific deployment probes; named login acceptance is a separate rehearsal."""

import time
from types import SimpleNamespace

GATEWAY_STATUSES = {502, 503, 504}


class NamedSmokeError(ValueError):
    """Safe phase/status diagnostic without remote response details."""


def access_mode(environ, *, require_alpha_credentials=True):
    mode = environ.get('TABULA_RASA_ACCESS_MODE')
    if mode not in {'restricted_alpha', 'named_user'}:
        raise ValueError('Deployment requires an explicit supported access mode.')
    if mode == 'named_user':
        if any(environ.get('TABULA_RASA_' + key) for key in (
                'TRUSTED_ENTITY_ID', 'ALPHA_USERNAME', 'ALPHA_PASSWORD', 'ALPHA_PASSWORD_HASH')):
            raise ValueError('Named deployment rejects synthetic scope and alpha credentials.')
    elif require_alpha_credentials and (not environ.get('TABULA_RASA_ALPHA_USERNAME')
                                        or not environ.get('TABULA_RASA_ALPHA_PASSWORD')):
        raise ValueError('Restricted-alpha smoke requires its independent credentials.')
    return mode


def _named_version_ready(request, release, previous_release, maintenance):
    version = request.get('/version', timeout=5000)
    if maintenance and version.status in GATEWAY_STATUSES:
        return False
    if maintenance and version.status != 200:
        raise NamedSmokeError(f'Version endpoint returned an unexpected status (HTTP {version.status}).')
    try:
        observed = version.json()
    except ValueError:
        raise NamedSmokeError('Version response was not valid JSON.') from None
    if observed != {'release': release}:
        if previous_release is None or observed != {'release': previous_release}:
            raise NamedSmokeError('Release identity mismatch.')
        return False
    if not maintenance:
        return True
    status = request.get('/health/ready', timeout=5000).status
    if status not in {200, *GATEWAY_STATUSES}:
        raise NamedSmokeError(f'Readiness failed (HTTP {status}).')
    return status == 200


def _named_http_ready(request, release, previous_release, maintenance):
    if not _named_version_ready(request, release, previous_release, maintenance):
        return False
    for headers in ({}, {'Authorization': 'Basic c3ludGhldGljOnN5bnRoZXRpYw=='},
                    {'Cookie': 'tabula_entity=alpha-synthetic; tabula_dev_session=synthetic',
                     'X-Forwarded-For': '127.0.0.1'}):
        for path in ('/api/v1/session', '/api/openapi.json',
                     '/api/v1/entities/alpha-synthetic/accounts',
                     '/api/v1/entities/alpha-synthetic/transactions',
                     '/api/v1/entities/alpha-synthetic/documents',
                     '/api/v1/entities/alpha-synthetic/exports/transactions.csv'):
            response = request.get(path, headers=headers)
            if maintenance and response.status in GATEWAY_STATUSES:
                return False
            if (response.status != 401 or response.headers.get('www-authenticate', '').lower().startswith('basic')
                    or response.headers.get('cache-control') != 'no-store'):
                raise NamedSmokeError('Named deployment access restriction failed.')
    for path in ('/api/v1/unknown', '/assets/missing.js'):
        status = request.get(path, headers={'Accept': 'text/html'}).status
        if maintenance and status in GATEWAY_STATUSES:
            return False
        if status != 404:
            raise NamedSmokeError('Routing failure.')
    return True


def named_probe(context, release, *, previous_release=None, maintenance=False, sleep=time.sleep,
                monotonic=time.monotonic, verify_shell=None, emit=lambda _: None):
    """Verify the whole public boundary within one maintenance handover budget."""
    if maintenance and previous_release is None:
        raise NamedSmokeError('Maintenance smoke requires the verified prior release.')
    if not maintenance and context.request.get('/health/ready').status != 200:
        raise NamedSmokeError('Readiness failed.')
    started = monotonic()
    deadline = started + 300
    checks = 101 if maintenance else 21 if previous_release is not None else 1

    def timeout():
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise NamedSmokeError('Release handover did not converge within five minutes.')
        return max(1, min(5000, int(remaining * 1000)))

    def report(path, status):
        if maintenance:
            emit({'status': 'maintenance_public_probe', 'path': path, 'http_status': status,
                  'attempt': attempt + 1, 'elapsed_seconds': round(monotonic() - started, 3)})

    def get(path, **kwargs):
        if maintenance:
            kwargs['timeout'] = timeout()
        response = context.request.get(path, **kwargs)
        if maintenance:
            report(path, response.status)
        return response

    request = SimpleNamespace(get=get)
    for attempt in range(checks):
        if _named_http_ready(request, release, previous_release, maintenance):
            if verify_shell is None or verify_shell(timeout if maintenance else None, report):
                return
        if attempt == checks - 1 or maintenance and monotonic() >= deadline:
            raise NamedSmokeError('Release handover did not converge within five minutes.' if maintenance
                                  else 'Release handover did not converge.')
        sleep(max(0, min(3, deadline - monotonic())) if maintenance else 3)


def named_smoke(origin, release, *, previous_release=None, maintenance=False, emit=lambda _: None):
    """No provider credentials, login transactions, session storage or private reads.

    Readiness checks PostgreSQL. This verifies the public sign-in shell and
    denied private API boundary, not successful provider login/membership.
    """
    from playwright.sync_api import Error, expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with browser.new_context(base_url=origin) as context:
                page = context.new_page()
                asset_gateway = []
                page.on('response', lambda response: asset_gateway.append(response.status)
                        if response.url.startswith(origin + '/assets/') and response.status in GATEWAY_STATUSES else None)

                def verify_shell(timeout, report):
                    asset_gateway.clear()
                    try:
                        response = page.goto('/accounts', **({'timeout': timeout()} if timeout else {}))
                        if response is not None:
                            report('/accounts', response.status)
                        if maintenance and response is not None and response.status in GATEWAY_STATUSES:
                            return False
                        if maintenance and (response is None or response.status != 200):
                            raise NamedSmokeError('Public sign-in shell check failed.')
                        expect(page.get_by_role('link', name='Sign in', exact=True)).to_have_attribute(
                            'href', '/api/v1/auth/login', **({'timeout': timeout()} if timeout else {}))
                        expect(page.get_by_role('button', name='Sign out', exact=True)).to_have_count(
                            0, **({'timeout': timeout()} if timeout else {}))
                        for status in asset_gateway:
                            report('/assets/', status)
                        return not (maintenance and asset_gateway)
                    except (AssertionError, Error):
                        if maintenance and asset_gateway:
                            for status in asset_gateway:
                                report('/assets/', status)
                            return False
                        raise NamedSmokeError('Public sign-in shell check failed.') from None

                try:
                    named_probe(context, release, previous_release=previous_release, maintenance=maintenance,
                                verify_shell=verify_shell, emit=emit)
                except Error:
                    raise NamedSmokeError('Public HTTP probe request failed.') from None
        finally:
            browser.close()


def smoke(origin, username, password, release):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            anonymous = browser.new_context(base_url=origin)
            for path in ("/accounts", "/api/v1/session", "/api/openapi.json"):
                response = anonymous.request.get(path, headers={"Accept": "text/html"})
                if response.status != 401:
                    raise ValueError("Alpha access restriction failed.")
            if anonymous.request.get("/health/ready").status != 200:
                raise ValueError("Readiness failed.")
            if anonymous.request.get("/version").json() != {"release": release}:
                raise ValueError("Release identity mismatch.")
            anonymous.close()
            context = browser.new_context(base_url=origin, http_credentials={"username": username, "password": password})
            session = context.request.get("/api/v1/session").json()
            if session.get("entity", {}).get("id") != "alpha-synthetic" or not session.get("trusted_development"):
                raise ValueError("Synthetic deployment context mismatch.")
            if context.request.get("/api/v1/entities/another/accounts").status != 403:
                raise ValueError("Fixed entity isolation failed.")
            for path in ("/api/v1/unknown", "/assets/missing.js"):
                if context.request.get(path, headers={"Accept": "text/html"}).status != 404:
                    raise ValueError("Routing failure.")
            page = context.new_page()
            page.goto("/accounts")
            expect(page.get_by_role("heading", name="Accounts", exact=True)).to_be_visible()
            expect(page.get_by_role("rowheader", name="Deployment connectivity", exact=True)).to_be_visible()
            context.close()
        finally:
            browser.close()


if __name__ == "__main__":
    import argparse
    import os
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    args = parser.parse_args()
    try:
        if os.environ.get("TABULA_RASA_ENVIRONMENT") not in {"development", "staging"}:
            raise ValueError
        origin = os.environ["TABULA_RASA_PUBLIC_ORIGIN"]
        if not origin.startswith("https://"):
            raise ValueError
        if access_mode(os.environ) == 'named_user':
            named_smoke(origin, args.release)
        else:
            smoke(origin, os.environ["TABULA_RASA_ALPHA_USERNAME"], os.environ["TABULA_RASA_ALPHA_PASSWORD"], args.release)
    except Exception:
        raise SystemExit("Deployment browser smoke failed; inspect routing, access, release and selected mode.") from None
    print("Deployment browser smoke passed.")
