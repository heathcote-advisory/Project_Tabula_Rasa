"""Mode-specific deployment probes; named login acceptance is a separate rehearsal."""

import time


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


def named_probe(context, release, *, previous_release=None, maintenance=False, sleep=time.sleep):
    """Anonymous HTTP boundary, including forged legacy credentials/selectors."""
    if maintenance and previous_release is None:
        raise ValueError('Maintenance smoke requires the verified prior release.')
    if not maintenance and context.request.get('/health/ready').status != 200:
        raise ValueError('Readiness failed.')
    checks = 21 if previous_release is not None else 1
    for attempt in range(checks):
        observed = context.request.get('/version', timeout=5000).json()
        if observed == {'release': release}:
            if not maintenance:
                break
            status = context.request.get('/health/ready', timeout=5000).status
            if status == 200:
                break
            if status != 503:
                raise ValueError('Readiness failed.')
        elif previous_release is None or observed != {'release': previous_release}:
            raise ValueError('Release identity mismatch.')
        if attempt == checks - 1:
            raise ValueError('Release handover did not converge.')
        sleep(3)
    for headers in ({}, {'Authorization': 'Basic c3ludGhldGljOnN5bnRoZXRpYw=='},
                    {'Cookie': 'tabula_entity=alpha-synthetic; tabula_dev_session=synthetic',
                     'X-Forwarded-For': '127.0.0.1'}):
        for path in ('/api/v1/session', '/api/openapi.json',
                     '/api/v1/entities/alpha-synthetic/accounts',
                     '/api/v1/entities/alpha-synthetic/transactions',
                     '/api/v1/entities/alpha-synthetic/documents',
                     '/api/v1/entities/alpha-synthetic/exports/transactions.csv'):
            response = context.request.get(path, headers=headers)
            if (response.status != 401 or response.headers.get('www-authenticate', '').lower().startswith('basic')
                    or response.headers.get('cache-control') != 'no-store'):
                raise ValueError('Named deployment access restriction failed.')
    for path in ('/api/v1/unknown', '/assets/missing.js'):
        if context.request.get(path, headers={'Accept': 'text/html'}).status != 404:
            raise ValueError('Routing failure.')


def named_smoke(origin, release, *, previous_release=None, maintenance=False):
    """No provider credentials, login transactions, session storage or private reads.

    Readiness checks PostgreSQL. This verifies the public sign-in shell and
    denied private API boundary, not successful provider login/membership.
    """
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with browser.new_context(base_url=origin) as context:
                named_probe(context, release, previous_release=previous_release, maintenance=maintenance)
                page = context.new_page()
                page.goto('/accounts')
                expect(page.get_by_role('link', name='Sign in', exact=True)).to_have_attribute(
                    'href', '/api/v1/auth/login')
                expect(page.get_by_role('button', name='Sign out', exact=True)).to_have_count(0)
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
