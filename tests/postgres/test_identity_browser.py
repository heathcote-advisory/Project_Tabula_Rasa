"""Compiled browser + signed fake OIDC + disposable PostgreSQL; no hosted claim."""

from pathlib import Path
import json
import socket
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep, time
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect
import pytest
import uvicorn

from tabula_rasa.http.app import create_app
from tabula_rasa.http.composition import PostgresRuntime
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.identity import NamedIdentity
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository
from tests.postgres.test_identity import setup
from tests.postgres.browser_quality_support import assert_accessible
from tests.test_oidc import oidc as oidc
from tests.test_application_documents import FakeDocumentStore


@pytest.fixture
def named_server(database, oidc, request):
    dist = Path(__file__).resolve().parents[2] / 'web/dist'
    if not (dist / 'index.html').is_file():
        import os
        if os.environ.get('TABULA_RASA_REQUIRE_BROWSER_TESTS') == '1':
            pytest.fail('Build web before required named-user browser tests.')
        pytest.skip('Build web to run named-user browser tests.')
    repo, alice, bob, a, b = setup(database, currencies=getattr(request, "param", ("USD", "USD")))
    provider, _, claims, info, _ = oidc
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    settings = HttpSettings('test', origin, access_mode='named_user')
    from dataclasses import replace
    provider.settings = replace(provider.settings, public_origin=origin)
    sessions = PostgresSessionRepository(database, 'test', provider.settings.configuration_hash)
    sessions.configure()
    auth = NamedIdentity(settings, sessions, provider)
    stores = {scope: FakeDocumentStore() for scope in ('alpha', 'beta')}
    runtime = PostgresRuntime(database, auth, identity=auth,
        service_factory=lambda context: create_postgres_services(database, context.entity_id,
            document_store=stores.setdefault(context.entity_id, FakeDocumentStore()), document_store_kind='fake'))
    runtime.close = lambda: None  # Pool owned by disposable fixture.
    app = create_app(settings=settings, runtime_factory=lambda _: runtime,
                     static_dir=dist)
    # The default keep-alive gives Playwright time to reuse its pooled API sockets.
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='critical',
                                         timeout_graceful_shutdown=2))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 5
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(.01)
        assert server.started
        yield origin, repo, alice, bob, a, b, claims, info
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def configure_login(context, origin, claims, *, registration=False):
    """Use signed fake OIDC, adapting only Playwright's external redirect hop."""
    def authorize(route):
        query = parse_qs(urlsplit(route.request.url).query)
        assert query['code_challenge_method'] == ['S256']
        assert query['prompt'] == ['create' if registration else 'login'] and query['max_age'] == ['0']
        claims['nonce'] = query['nonce'][0]
        target = origin + '/api/v1/auth/callback?' + urlencode(
            {'state': query['state'][0], 'code': 'synthetic-code'})
        # WebKit on Windows cannot fulfill a routed redirect response.
        route.fulfill(status=200, content_type='text/html',
            body='<script>location.replace(' + json.dumps(target) + ')</script>')
    def login_redirect(route):
        # Playwright routing cannot fake the later external hop of a
        # redirect chain. Split only that test hop into a new navigation;
        # assert the real server's 303/cookie response before adapting.
        response = route.fetch(max_redirects=0)
        assert response.status == 303
        target = response.headers['location']
        assert target.startswith('https://id.example.test/authorize?')
        headers = {key: value for key, value in response.headers.items()
                   if key not in {'location', 'content-length', 'content-type'}}
        route.fulfill(status=200, headers=headers, content_type='text/html',
            body='<script>location.replace(' + json.dumps(target) + ')</script>')
    context.route('https://id.example.test/authorize**', authorize)
    context.route('**/api/v1/auth/' + ('register' if registration else 'login') + '**', login_redirect)


def test_named_deployment_smoke_uses_public_shell_and_denied_http_boundary(named_server):
    # The deployment entry point owns its Playwright runtime, so exercise it in
    # a separate process even when this pytest session already owns a browser.
    subprocess.run([sys.executable, '-c',
        'import sys; from tabula_rasa.deployment.smoke import named_smoke; '
        'named_smoke(sys.argv[1], "development")', named_server[0]],
        check=True, timeout=30)


@pytest.mark.parametrize('width', [1440, 390])
def test_stale_provider_session_has_accessible_deliberate_recovery_and_clean_user_isolation(named_server, width, native_browser):
    origin, _, _, _, _, _, claims, info = named_server
    with native_browser('chromium') as browser:
        retained = browser.new_context(viewport={'width': width, 'height': 1000})
        configure_login(retained, origin, claims)
        page = retained.new_page()
        claims['auth_time'] = int(time()) - 600
        page.goto(origin)
        page.get_by_role('link', name='Sign in', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('did not confirm a recent sign-in')
        expect(page.get_by_role('alert')).to_contain_text('Request reference:')
        expect(page.get_by_text('You do not need to delete browser cookies.', exact=False)).to_be_visible()
        assert retained.request.get(origin + '/api/v1/session').status == 401
        assert_accessible(page)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # Recovery is a real click. A fresh signed response establishes Alice
        # without deleting either the app or provider browser state.
        claims['auth_time'] = int(time())
        page.get_by_role('link', name='Sign in', exact=True).click()
        expect(page.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        alice = retained.request.get(origin + '/api/v1/session').json()
        assert alice['entity']['id'] == 'alpha'
        clean = browser.new_context(viewport={'width': width, 'height': 1000})
        configure_login(clean, origin, claims)
        claims['sub'] = info['sub'] = 'bob'
        other = clean.new_page()
        other.goto(origin)
        other.get_by_role('link', name='Sign in', exact=True).click()
        expect(other.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        bob = clean.request.get(origin + '/api/v1/session').json()
        assert bob['entity']['id'] == 'beta' and bob['actor_id'] != alice['actor_id']
        assert clean.request.get(origin + '/api/v1/entities/alpha/accounts').status == 403
        assert retained.request.get(origin + '/api/v1/entities/beta/accounts').status == 403
        page.get_by_role('button', name='Sign out', exact=True).click()
        expect(page.get_by_role('link', name='Sign in', exact=True)).to_be_visible()
        expect(page.get_by_role('alert')).to_have_count(0)
        assert clean.request.get(origin + '/api/v1/session').status == 200


@pytest.mark.parametrize('width', [1440, 390])
def test_named_sign_in_revocation_two_users_and_cross_tab_logout(named_server, width, native_browser):
    origin, repo, alice, bob, a, _, claims, info = named_server
    with native_browser('chromium') as browser:
        alice_browser = browser.new_context(viewport={'width': width, 'height': 1000})
        bob_browser = browser.new_context(viewport={'width': width, 'height': 1000})
        for context in (alice_browser, bob_browser):
            configure_login(context, origin, claims)
        first, second = alice_browser.new_page(), bob_browser.new_page()
        first.goto(origin)
        expect(first.get_by_role('link', name='Sign in', exact=True)).to_be_visible()
        assert_accessible(first)
        with first.expect_response(lambda response: '/api/v1/auth/login' in response.url) as begin:
            first.get_by_role('link', name='Sign in', exact=True).click()
        assert begin.value.status == 200  # Redirect adapter above asserts the native 303.
        expect(first.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        assert alice_browser.request.get(origin + '/api/v1/entities/beta/accounts').status == 403
        claims['sub'] = info['sub'] = 'bob'
        second.goto(origin)
        second.get_by_role('link', name='Sign in', exact=True).click()
        expect(second.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        assert bob_browser.request.get(origin + '/api/v1/session').json()['entity']['id'] == 'beta'
        repo.set_membership(a, bob.id, 'owner')
        repo.set_user_state(alice.id, 'disabled')
        first.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(first.get_by_role('link', name='Sign in', exact=True)).to_be_visible()
        assert bob_browser.request.get(origin + '/api/v1/session').status == 200
        peer = bob_browser.new_page()
        peer.goto(origin)
        expect(peer.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        with second.expect_response(lambda response: response.request.method == 'POST'
                                    and response.url.endswith('/auth/logout')) as logged_out:
            second.get_by_role('button', name='Sign out', exact=True).click()
        assert logged_out.value.status == 204
        expect(second.get_by_role('link', name='Sign in', exact=True)).to_be_visible()
        expect(peer.get_by_role('link', name='Sign in', exact=True)).to_be_visible()
        peer.reload()
        expect(peer.get_by_role('link', name='Sign in', exact=True)).to_be_visible()


@pytest.mark.parametrize('width', [1440, 390])
def test_named_transaction_receipt_logout_then_other_user_has_no_residual_data(named_server, width, native_browser):
    origin, repo, alice, _, _, b, claims, info = named_server
    # An overlapping membership lets Alice explicitly select another workspace;
    # Bob still cannot read Alice's original workspace.
    repo.set_membership(b, alice.id, 'read_only')
    from tests.postgres._support import account, category
    with repo.database.transaction() as connection:
        for scope in ('alpha', 'beta'):
            account(connection, scope, 'cash')
            category(connection, scope, 'office', kind='Expense')
    with native_browser('chromium') as browser:
        context = browser.new_context(viewport={'width': width, 'height': 1000})
        # Installed in every document before app code, including Alice's
        # original receipt document if Back restores it from the BFCache.
        context.add_init_script("""(() => {
                const privateText = text => ['Alice private transaction', 'alice-private.pdf']
                    .some(value => (text || '').includes(value));
                const record = records => {
                    if (sessionStorage.getItem('security_probe_active') !== '1') return;
                    if (privateText(document.body?.textContent) || records.some(change =>
                        [...change.addedNodes, ...change.removedNodes].some(node => privateText(node.textContent))))
                        sessionStorage.setItem('security_probe_exposure', '1');
                };
                addEventListener('pageshow', () => record([]));
                new MutationObserver(record).observe(document, {subtree: true, childList: true, characterData: true});
            })();""")
        configure_login(context, origin, claims)
        page = context.new_page()
        page.goto(origin)
        page.get_by_role('link', name='Sign in', exact=True).click()
        expect(page.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        selector = page.get_by_label('Select business')
        if selector.input_value() != 'alpha':
            selector.select_option('alpha')
            page.get_by_role('button', name='Discard draft and switch').click()
            expect(page.get_by_text('Role: Owner', exact=True)).to_be_visible()
        page.get_by_label('Select business').select_option('beta')
        page.get_by_role('button', name='Discard draft and switch').click()
        expect(page.get_by_text('Role: Read-only', exact=True)).to_be_visible()
        page.get_by_label('Select business').select_option('alpha')
        page.get_by_role('button', name='Discard draft and switch').click()
        expect(page.get_by_text('Role: Owner', exact=True)).to_be_visible()
        page.goto(origin + '/transactions/new')
        page.get_by_label('Date', exact=True).fill('2026-10-06')
        page.get_by_label('Amount', exact=True).fill('19.25')
        page.get_by_label('From account', exact=True).select_option('cash')
        page.get_by_label('Category', exact=True).select_option('office')
        page.get_by_label('Description / memo', exact=True).fill('Alice private transaction')
        with page.expect_response(lambda response: response.request.method == 'POST'
                                  and response.url.endswith('/transactions')) as created:
            page.get_by_role('button', name='Save transaction', exact=True).click()
        assert created.value.status == 201
        record = created.value.json()['id']
        page.goto(origin + '/transactions/' + record + '/receipts')
        page.get_by_label('Receipt files').set_input_files({
            'name': 'alice-private.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF synthetic Alice',
        })
        page.get_by_role('button', name='Attach selected receipts').click()
        expect(page.get_by_role('status').filter(has_text='1 receipt attached.')).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('link', name='Download alice-private.pdf', exact=True).click()
        assert Path(download.value.path()).read_bytes() == b'%PDF synthetic Alice'
        linked = context.request.get(origin + '/api/v1/entities/alpha/transactions/' + record + '/receipts').json()['items']
        document = linked[0]['id']
        receipt_url = page.url
        receipt_history_length = page.evaluate('history.length')
        previous = context.request.get(origin + '/api/v1/session').json()
        with page.expect_response(lambda response: response.request.method == 'POST'
                                  and response.url.endswith('/auth/logout')) as logged_out:
            page.get_by_role('button', name='Sign out', exact=True).click()
        assert logged_out.value.status == 204
        expect(page.get_by_role('link', name='Sign in', exact=True)).to_be_visible()
        expect(page.get_by_text('alice-private.pdf', exact=False)).to_have_count(0)
        claims['sub'] = info['sub'] = 'bob'
        page.get_by_role('link', name='Sign in', exact=True).click()
        expect(page.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        current = context.request.get(origin + '/api/v1/session').json()
        assert current['actor_id'] != previous['actor_id']
        assert current['cache_scope'] != previous['cache_scope']
        assert current['entity']['id'] == 'beta'
        page.evaluate("sessionStorage.setItem('security_probe_active', '1')")
        for path in ('transactions', 'transactions/' + record + '/receipts', 'documents/' + document + '/content'):
            assert context.request.get(origin + '/api/v1/entities/alpha/' + path).status == 403
        page.goto(origin + '/transactions')
        expect(page.get_by_role('heading', name='No matching entries', exact=True)).to_be_visible()
        expect(page.get_by_text('Alice private transaction', exact=True)).to_have_count(0)
        # Traverse every entry added by Bob's login and ledger navigation,
        # including the actual receipt entry visited while Alice was signed in.
        history_steps = page.evaluate('history.length') - receipt_history_length
        assert history_steps > 0
        for _ in range(history_steps):
            page.go_back(wait_until='networkidle')
            assert page.evaluate("sessionStorage.getItem('security_probe_exposure')") is None
            expect(page.get_by_text('Alice private transaction', exact=True)).to_have_count(0)
            expect(page.get_by_text('alice-private.pdf', exact=False)).to_have_count(0)
        expect(page).to_have_url(receipt_url)
        for storage in ('localStorage', 'sessionStorage'):
            stored = page.evaluate(f'JSON.stringify(Object.entries({storage}))')
            assert all(value not in stored for value in ('Alice private transaction', 'alice-private.pdf', document))


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
def test_customer_closure_export_cancel_and_expired_status(named_server, database, native_browser, width, engine):
    from sqlalchemy import text
    origin, _, _, _, _, _, claims, _ = named_server
    claims['auth_time'] = int(time())
    with native_browser(engine) as browser:
        context = browser.new_context(viewport={'width': width, 'height': 1000})
        configure_login(context, origin, claims)
        page = context.new_page()
        page.goto(origin)
        page.get_by_role('link', name='Sign in', exact=True).click()
        expect(page.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        page.goto(origin + '/data')
        page.get_by_role('button', name='Request business closure', exact=True).click()
        page.get_by_role('button', name='Review and confirm business closure', exact=True).click()
        dialog = page.get_by_role('alertdialog')
        expect(dialog.get_by_role('button', name='Confirm request')).to_be_disabled()
        expect(dialog).to_contain_text('alpha')
        dialog.get_by_role('checkbox').check()
        dialog.get_by_role('button', name='Confirm request').click()
        expect(page.get_by_text('Business closure is in progress.', exact=False)).to_be_visible()
        page.goto(origin + '/data')
        expect(page.get_by_text('Read-only export/recovery window', exact=True)).to_be_visible()
        page.get_by_label('Archive scope').select_option('metadata_only')
        with page.expect_download() as download:
            page.get_by_role('button', name='Download handoff ZIP', exact=True).click()
        assert download.value.failure() is None
        assert_accessible(page)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('button', name='Cancel business closure', exact=True).click()
        page.get_by_role('alertdialog').get_by_role('button', name='Cancel request').click()
        expect(page.get_by_text('Business closure is in progress.', exact=False)).to_have_count(0)
        page.goto(origin + '/data')
        page.get_by_role('button', name='Request business closure', exact=True).click()
        page.get_by_role('button', name='Review and confirm business closure', exact=True).click()
        dialog = page.get_by_role('alertdialog')
        dialog.get_by_role('checkbox').check()
        dialog.get_by_role('button', name='Confirm request').click()
        expect(page.get_by_text('Business closure is in progress.', exact=False)).to_be_visible()
        with database.transaction() as c:
            c.execute(text("UPDATE customer_lifecycle_requests SET recover_until=clock_timestamp()-interval '1 second' WHERE state='confirmed' AND entity_id='alpha'"))
        page.reload()
        expect(page.get_by_text('Access ended; data retained', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Cancel business closure', exact=True)).to_have_count(0)
        assert context.request.get(origin + '/api/v1/entities/alpha/accounts').status == 403
        assert_accessible(page)


@pytest.mark.parametrize('width', [1440, 390])
def test_registration_then_private_invitation_without_automatic_business_access(named_server, native_browser, width):
    from sqlalchemy import text
    from tabula_rasa.application.workspaces import WorkspaceService
    from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository
    from tests.postgres.test_identity import verified

    origin, repo, _, _, org, _, claims, info = named_server
    with repo.database.transaction(read_only=True) as c:
        configuration = c.scalar(text("SELECT configuration_hash FROM identity_environments WHERE environment='test'"))
    sessions = PostgresSessionRepository(repo.database, 'test', configuration)
    _, owner = sessions.issue(verified())
    service = WorkspaceService(PostgresWorkspaceRepository(repo.database))
    invitation = service.invite(owner, org, 'shared@example.test', 'editor')
    claims.update(sub='new-registered-user', auth_time=int(time()))
    info.update(sub=claims['sub'], email='shared@example.test')
    with native_browser('chromium') as browser:
        context = browser.new_context(viewport={'width': width, 'height': 1000})
        configure_login(context, origin, claims, registration=True)
        page = context.new_page()
        page.goto(origin + '/invitations')
        expect(page.get_by_role('link', name='Sign in', exact=True)).to_be_visible()
        expect(page.get_by_role('link', name='Create account', exact=True)).to_be_visible()
        assert_accessible(page)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('link', name='Create account', exact=True).click()
        expect(page.get_by_role('heading', name='Join an invited business', exact=True)).to_be_visible()
        current = context.request.get(origin + '/api/v1/session').json()
        assert current['state'] == 'needs_setup' and current['actor_id'] != owner.user_id
        with repo.database.transaction(read_only=True) as c:
            assert c.scalar(text('SELECT count(*) FROM memberships WHERE user_id=:user'), {'user': current['actor_id']}) == 0
            assert c.scalar(text('SELECT count(*) FROM business_entities')) == 2
            assert c.scalar(text('SELECT count(*) FROM beta_named_users WHERE user_id=:user'), {'user': current['actor_id']}) == 0
        assert context.request.get(origin + '/api/v1/entities/alpha/accounts').status == 403
        assert context.request.get(origin + '/api/v1/entities/beta/accounts').status == 403
        page.get_by_label('Invitation credential', exact=True).fill(invitation.credential)
        page.get_by_role('button', name='Review invitation', exact=True).click()
        expect(page.get_by_role('region', name='Invited business')).to_contain_text('alpha')
        expect(page.get_by_role('region', name='Invited business')).to_contain_text('Editor')
        page.get_by_role('button', name='Accept invitation', exact=True).click()
        expect(page.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        accepted = context.request.get(origin + '/api/v1/session').json()
        assert accepted['entity']['id'] == 'alpha' and accepted['entity']['role'] == 'editor'
        assert context.request.get(origin + '/api/v1/entities/beta/accounts').status == 403
        with repo.database.transaction(read_only=True) as c:
            assert c.scalar(text('SELECT count(*) FROM memberships WHERE user_id=:user'), {'user': current['actor_id']}) == 1
            assert c.scalar(text('SELECT count(*) FROM business_entities')) == 2
