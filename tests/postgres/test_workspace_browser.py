"""Two-user native workspace lifecycle and desktop/mobile cache isolation."""

import os
from pathlib import Path
from uuid import uuid4
from time import perf_counter

import pytest
from playwright.sync_api import expect
from sqlalchemy import text

from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tests.postgres.test_identity import verified
from tests.postgres.test_identity_browser import named_server as named_server, configure_login
from tests.postgres.browser_quality_support import assert_accessible
from tests.test_oidc import oidc as oidc


@pytest.mark.parametrize('named_server', [('USD', 'CAD')], indirect=True)
@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
@pytest.mark.parametrize('width', [1440, 390])
def test_fixed_currency_switching_reports_and_downloads(named_server, engine, width, native_browser):
    from tests.postgres._support import account, category

    origin, repo, alice, _, _, b, _, _ = named_server
    repo.set_membership(b, alice.id, 'read_only')
    with repo.database.transaction() as c:
        configuration = c.scalar(text("SELECT configuration_hash FROM identity_environments WHERE environment='test'"))
        for scope in ('alpha', 'beta'):
            account(c, scope, 'cash')
            category(c, scope, 'sales', kind='Income')
    for scope, amount in [('alpha', '9.01'), ('beta', '123.45')]:
        services = create_postgres_services(repo.database, scope)
        services.transactions.create(transaction_id='local', txn_date='2026-10-05', kind='Income',
            amount=amount, account_id='cash', category_id='sales')
    sessions = PostgresSessionRepository(repo.database, 'test', configuration)
    raw, _ = sessions.issue(verified())
    sessions.select(raw, 'alpha')
    with native_browser(engine) as browser:
        context = browser.new_context(viewport={'width': width, 'height': 1000})
        context.add_cookies([{'name': 'tabula_dev_session', 'value': raw, 'url': origin,
                             'httpOnly': True, 'sameSite': 'Lax'}])
        page = context.new_page()
        # Isolate explicit selection from unrelated foreground authority refresh.
        page.add_init_script("Object.defineProperty(document, 'visibilityState', {get: () => 'hidden'})")
        page.goto(origin)
        for scope, currency, role in [('beta', 'CAD', 'Read-only'), ('alpha', 'USD', 'Owner'), ('beta', 'CAD', 'Read-only')]:
            page.get_by_label('Select business', exact=True).select_option(scope)
            page.get_by_role('button', name='Discard draft and switch').click()
            expect(page.get_by_text('Book currency: ' + currency, exact=True)).to_be_visible()
            expect(page.get_by_text('Role: ' + role, exact=True)).to_be_visible()
            expect(page.locator('header').first).to_contain_text(currency)
            session = context.request.get(origin + '/api/v1/session').json()
            assert session['entity']['currency_code'] == currency
            page.goto(origin + '/reports')
            expect(page.get_by_text('All monetary amounts are in ' + currency + '.', exact=False)).to_be_visible()
            assert_accessible(page)
            page.goto(origin + '/data')
            with page.expect_download() as downloading:
                page.get_by_role('button', name='Download transactions CSV', exact=True).click()
            assert downloading.value.suggested_filename.endswith('-' + currency + '.csv')
            assert context.request.get(origin + '/api/v1/entities/' + ('alpha' if scope == 'beta' else 'beta')
                                       + '/exports/transactions.csv').status == 403
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert_accessible(page)
        # Read-only CAD membership cannot write even when a selector claims USD.
        response = context.request.post(origin + '/api/v1/entities/beta/accounts',
            headers={'Origin': origin, 'X-CSRF-Token': session['csrf_token'], 'X-Currency': 'USD'},
            data={'name': 'Forbidden', 'type': 'Bank'})
        assert response.status == 403


@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
@pytest.mark.parametrize('width', [1440, 390, 320])
def test_independent_roles_flat_navigation_and_shared_invitee(named_server, engine, width, native_browser):
    from tabula_rasa.application.workspaces import WorkspaceService
    from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository

    origin, repo, alice, bob, a, b, _, _ = named_server
    with repo.database.transaction() as connection:
        configuration_hash = connection.scalar(text("SELECT configuration_hash FROM identity_environments WHERE environment='test'"))
        connection.execute(text("UPDATE business_entities SET name=:name WHERE id='alpha'"),
            {'name': 'Independent long business name ' * 5})
    sessions = PostgresSessionRepository(repo.database, 'test', configuration_hash)
    alice_raw, alice_session = sessions.issue(verified())
    _, bob_session = sessions.issue(verified('bob'))
    guest_raw, guest = sessions.issue(verified('shared-read-only'))
    service = WorkspaceService(PostgresWorkspaceRepository(repo.database))
    repo.set_membership(b, alice.id, 'admin')
    invitations = [service.invite(actor, org, guest.verified_email, 'read_only')
                   for actor, org in ((alice_session, a), (bob_session, b))]
    with native_browser(engine) as browser:
        owner_context = browser.new_context(viewport={'width': width, 'height': 1000})
        guest_context = browser.new_context(viewport={'width': width, 'height': 1000})
        for context, raw in ((owner_context, alice_raw), (guest_context, guest_raw)):
            context.add_cookies([{'name': 'tabula_dev_session', 'value': raw, 'url': origin, 'httpOnly': True, 'sameSite': 'Lax'}])
        owner, invitee = owner_context.new_page(), guest_context.new_page()
        owner.goto(origin + '/businesses')
        available = owner.get_by_role('region', name='Available businesses')
        expect(available.get_by_text('Owner', exact=True)).to_be_visible()
        expect(available.get_by_text('Admin', exact=True)).to_be_visible()
        # List and sidebar share one confirmation/selection boundary.
        available.get_by_role('button', name='Open business').focus()
        owner.keyboard.press('Enter')
        owner.get_by_role('button', name='Discard draft and switch').click()
        expect(owner.get_by_text('Role: Admin', exact=True)).to_be_visible()
        expect(owner).to_have_url(origin + '/')
        owner.goto(origin + '/business/members')
        expect(owner.locator('header').first).to_contain_text('Business & members')
        expect(owner.get_by_role('button', name='Grant ownership')).to_have_count(0)
        assert_accessible(owner)
        for index, invitation in enumerate(invitations):
            invitee.goto(origin + '/invitations')
            # WebKit can leave this password field empty after a bulk fill.
            invitee.bring_to_front()
            credential_field = invitee.get_by_label('Invitation credential', exact=True)
            credential_field.focus()
            expect(credential_field).to_be_focused()
            credential_field.press_sequentially(invitation.credential)
            expect(credential_field).not_to_be_empty()
            invitee.get_by_role('button', name='Review invitation').click()
            offer = invitee.get_by_role('region', name='Invited business')
            expect(offer.get_by_text('Business reference: ' + ('alpha' if index == 0 else 'beta'), exact=True)).to_be_visible()
            expect(offer.get_by_text('Your role in this business: Read-only', exact=True)).to_be_visible()
            assert_accessible(invitee)
            invitee.get_by_role('button', name='Accept invitation').click()
            expect(invitee.get_by_text('Role: Read-only', exact=True)).to_be_visible()
            assert guest_context.request.get(origin + '/api/v1/session').json()['entity']['id'] == ('alpha' if index == 0 else 'beta')
            if index == 0:
                choices = guest_context.request.get(origin + '/api/v1/businesses').json()['items']
                assert [item['id'] for item in choices] == ['alpha']
                assert guest_context.request.get(origin + '/api/v1/entities/beta/accounts').status == 403
        invitee.goto(origin + '/businesses')
        expect(invitee.get_by_role('region', name='Available businesses').get_by_text('Read-only', exact=True)).to_have_count(2)
        # A's role/removal never changes valid B authorization or selected scope.
        beta_before = sessions.validate(guest_raw).selected
        member = next(item for item in service.members(alice_session, a) if item['user_id'] == guest.user_id)
        started = perf_counter()
        service.business_operation(alice_session, 'alpha', 'change_member',
            guest.user_id, 'read_only', False, member['authorization_version'])
        mutation_ms = (perf_counter() - started) * 1000
        invitee.reload()
        expect(invitee.get_by_role('region', name='Available businesses').get_by_role('listitem')).to_have_count(1)
        assert sessions.validate(guest_raw).selected == beta_before
        assert guest_context.request.get(origin + '/api/v1/businesses/alpha/members').status == 403
        assert owner_context.request.get(origin + '/api/v1/session').json()['entity']['role'] == 'admin'
        owner.goto(origin + '/businesses')
        expect(owner.get_by_role('heading', name='My Businesses', exact=True)).to_be_visible()
        expect(owner.get_by_role('region', name='Available businesses').get_by_role('listitem')).to_have_count(2)
        assert_accessible(owner)
        assert owner.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert invitee.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if root := os.environ.get('TABULA_RASA_BROWSER_EVIDENCE'):
            target = Path(root)
            target.mkdir(parents=True, exist_ok=True)
            owner.screenshot(path=str(target / f'{engine}-{width}-flat-businesses.png'), full_page=True)
            invitee.screenshot(path=str(target / f'{engine}-{width}-isolated-revocation.png'), full_page=True)
        # Synthetic local timing is diagnostic only, never hosted-latency evidence.
        print(f'local synthetic membership mutation: {engine} {width}px {mutation_ms:.1f}ms')


@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
@pytest.mark.parametrize('width', [1440, 390, 320])
def test_first_ledger_retry_refresh_repeat_visit_and_two_entity_switch(named_server, width, engine, native_browser):
    origin, repo, _, _, _, _, claims, info = named_server
    claims['sub'] = info['sub'] = 'new-owner'
    first_name = 'IndependentStudio' * 9
    def evidence(page, stage):
        assert_accessible(page)
        if root := os.environ.get('TABULA_RASA_BROWSER_EVIDENCE'):
            target = Path(root)
            target.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target / f'{engine}-{width}-{stage}.png'), full_page=True)
    with native_browser(engine) as browser:
        context = browser.new_context(viewport={'width': width, 'height': 1000})
        configure_login(context, origin, claims)
        page = context.new_page()
        page.add_init_script("Object.defineProperty(document, 'visibilityState', {get: () => 'hidden'})")
        page.goto(origin)
        page.get_by_role('link', name='Sign in', exact=True).click()
        expect(page.get_by_role('button', name='Create your first business', exact=True)).to_be_visible()
        actor = context.request.get(origin + '/api/v1/session').json()['actor_id']
        from tests.postgres.test_workspaces import approve_internal
        approve_internal(repo.database, actor)
        page.get_by_role('button', name='Create your first business', exact=True).click()
        page.get_by_label('Business name').fill(first_name)
        page.get_by_role('button', name='Review defaults').focus()
        page.keyboard.press('Enter')
        page.get_by_role('button', name='Back', exact=True).click()
        expect(page.get_by_label('Business name')).to_have_value(first_name)
        page.get_by_role('button', name='Review defaults').click()
        expect(page.get_by_label('Workspace name')).to_have_count(0)
        page.get_by_role('button', name='Back', exact=True).focus()
        page.keyboard.press('Tab')
        expect(page.get_by_role('button', name='Create business', exact=True)).to_be_focused()
        evidence(page, 'review-first')
        peer = context.new_page()
        # Disable focus/periodic authority checks: only the broadcast may refresh this peer.
        peer.add_init_script("Object.defineProperty(document, 'visibilityState', {get: () => 'hidden'})")
        peer.goto(origin)
        peer.get_by_role('button', name='Create your first business', exact=True).click()
        peer.get_by_label('Business name').fill('Unsubmitted peer draft')
        page.bring_to_front()
        endpoint = '**/api/v1/businesses'
        def unavailable(route):
            if route.request.method != 'POST':
                route.continue_()
                return
            expect(page.get_by_role('button', name='Retry same setup', exact=True)).to_be_disabled()
            evidence(page, 'pending-first')
            route.fulfill(status=503, content_type='application/json',
                body='{"error":{"code":"unavailable"},"request_id":"synthetic-failure"}')
        page.route(endpoint, unavailable)
        page.get_by_role('button', name='Create business', exact=True).click()
        expect(page.get_by_role('alert')).to_be_visible()
        evidence(page, 'uncertain-first')
        expect(peer.get_by_label('Business name')).to_have_value('Unsubmitted peer draft')
        receipt = page.evaluate('Object.values(sessionStorage)[0]')
        page.reload()
        page.get_by_role('button', name='Create your first business', exact=True).click()
        expect(page.get_by_text('Setup was interrupted.', exact=False)).to_be_visible()
        page.get_by_label('Business name').fill(first_name)
        page.get_by_role('button', name='Review defaults').click()
        # Commit on the real API, then lose only its response.
        def lost_response(route):
            if route.request.method != 'POST':
                route.continue_()
                return
            response = route.fetch()
            assert response.status == 200
            route.fulfill(status=503, content_type='application/json',
                body='{"error":{"code":"unavailable"},"request_id":"synthetic-lost-response"}')
        page.unroute(endpoint)
        page.route(endpoint, lost_response)
        page.get_by_role('button', name='Create business', exact=True).click()
        expect(page.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        expect(peer.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        expect(peer.get_by_label('Business name')).to_have_count(0)
        assert context.request.get(origin + '/api/v1/businesses/creation/' + receipt).json()['state'] == 'ready'
        with repo.database.transaction(read_only=True) as connection:
            assert connection.scalar(text('SELECT count(*) FROM workspace_bootstraps WHERE user_id=:user'), {'user': actor}) == 1
        # Model a reload after commit but before the first receipt is retired.
        page.evaluate("([key, receipt]) => sessionStorage.setItem(key, receipt)",
            [f'tabula-setup:{actor}:first', receipt])
        page.reload()
        expect(page.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        expect(page.get_by_role('button', name='Create your first business', exact=True)).to_have_count(0)
        page.get_by_role('link', name='Review accounts', exact=True).click()
        expect(page.get_by_role('heading', name='Accounts', exact=True)).to_be_visible()
        expect(page.get_by_role('alert')).to_have_count(0)
        assert context.request.get(origin + '/api/v1/entities/alpha/accounts').status == 403
        current = context.request.get(origin + '/api/v1/session').json()
        previous_slot = f'tabula-setup:{actor}:{current["organization_id"]}'
        page.evaluate("([key, receipt]) => sessionStorage.setItem(key, receipt)", [previous_slot, receipt])
        page.goto(origin + '/businesses')
        expect(page.get_by_role('region', name='Available businesses').get_by_role('listitem')).to_have_count(1)
        page.get_by_role('button', name='Create another business', exact=True).click()
        expect(page.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        assert page.evaluate("key => sessionStorage.getItem(key)", previous_slot) is None
        peer.goto(origin + '/businesses')
        expect(peer.get_by_role('region', name='Available businesses').get_by_role('listitem')).to_have_count(1)
        peer.get_by_role('button', name='Create another business', exact=True).click()
        expect(peer.get_by_label('Business name')).to_be_visible()
        peer.get_by_label('Business name').fill('Another unsubmitted peer draft')
        # The old organization-addition endpoint stays denied while the
        # business-first form below creates an independent container.
        current = context.request.get(origin + '/api/v1/session').json()
        denied = context.request.post(origin + '/api/v1/workspaces/' + current['organization_id'] + '/businesses',
            headers={'Origin': origin, 'X-CSRF-Token': current['csrf_token']},
            data={'request_id': uuid4().hex, 'workspace_name': 'Ignored', 'business_name': 'Rejected addition'})
        assert denied.status == 403
        expect(peer.get_by_label('Business name')).to_have_value('Another unsubmitted peer draft')
        assert len(context.request.get(origin + '/api/v1/entities').json()['items']) == 1
        with repo.database.transaction(read_only=True) as connection:
            assert connection.scalar(text('SELECT count(*) FROM workspace_bootstraps WHERE user_id=:user'), {'user': actor}) == 1
        page.bring_to_front()
        page.goto(origin + '/businesses')
        # Wait for the async list before clicking a control below it.
        expect(page.get_by_role('region', name='Available businesses').get_by_role('listitem')).to_have_count(1)
        page.get_by_role('button', name='Create another business', exact=True).click()
        expect(page.get_by_label('Business name')).to_be_visible()
        page.get_by_label('Business name').fill('Second business')
        review_action = page.get_by_role('button', name='Review defaults')
        # A focused field may still scroll the viewport. Read both rectangles
        # in one layout snapshot rather than mixing separate viewport offsets.
        section = page.get_by_role('region', name='Other ways to get started')
        gap = section.evaluate("""(section, action) =>
                section.getBoundingClientRect().top - action.getBoundingClientRect().bottom""",
            review_action.element_handle())
        assert gap >= 32
        expect(page.get_by_label('Business name')).to_have_value('Second business')
        evidence(page, 'another-business')
        expect(page.get_by_label('Business name')).to_have_value('Second business')
        expect(review_action).to_be_enabled()
        review_action.click()
        page.get_by_role('button', name='Create business', exact=True).click()
        expect(page.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        expect(peer.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        expect(peer.get_by_label('Select business')).to_have_value(
            context.request.get(origin + '/api/v1/session').json()['entity']['id'])
        choices = context.request.get(origin + '/api/v1/entities').json()['items']
        assert len(choices) == 2
        assert {item['role'] for item in choices} == {'owner'}
        expect(peer.get_by_label('Business name')).to_have_count(0)
        assert len({item['organization_id'] for item in choices}) == 2
        first = next(item['id'] for item in choices if item['name'] == first_name)
        page.get_by_label('Select business').select_option(first)
        page.get_by_role('button', name='Discard draft and switch').click()
        expect(page.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        assert context.request.get(origin + '/api/v1/session').json()['entity']['id'] == first
        assert_accessible(page)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
@pytest.mark.parametrize('width', [1440, 390, 320])
def test_onboarding_invitee_joins_existing_workspace_without_new_ownership(named_server, width, engine, native_browser):
    from tabula_rasa.application.workspaces import WorkspaceService
    from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository

    origin, repo, _, _, org, _, _, _ = named_server
    with repo.database.transaction(read_only=True) as connection:
        configuration_hash = connection.scalar(text("SELECT configuration_hash FROM identity_environments WHERE environment='test'"))
    sessions = PostgresSessionRepository(repo.database, 'test', configuration_hash)
    _, owner = sessions.issue(verified())
    raw, invitee = sessions.issue(verified('new-invitee'))
    service = WorkspaceService(PostgresWorkspaceRepository(repo.database))
    invitation = service.invite(owner, org, 'shared@example.test', 'editor')
    with native_browser(engine) as browser:
        context = browser.new_context(viewport={'width': width, 'height': 1000})
        context.add_cookies([{'name': 'tabula_dev_session', 'value': raw, 'url': origin, 'httpOnly': True, 'sameSite': 'Lax'}])
        page = context.new_page()
        page.add_init_script("Object.defineProperty(document, 'visibilityState', {get: () => 'hidden'})")
        page.goto(origin + '/invitations')
        expect(page.get_by_role('button', name='Create your first business', exact=True)).to_have_count(0)
        credential_field = page.get_by_label('Invitation credential', exact=True)
        credential_field.focus()
        expect(credential_field).to_be_focused()
        assert_accessible(page)
        if root := os.environ.get('TABULA_RASA_BROWSER_EVIDENCE'):
            page.screenshot(path=str(Path(root) / f'{engine}-{width}-invitation.png'), full_page=True)
        credential_field.fill(invitation.credential)
        peer = context.new_page()
        peer.add_init_script("Object.defineProperty(document, 'visibilityState', {get: () => 'hidden'})")
        status_routes = []
        peer.route('**/api/v1/beta/status', lambda route: status_routes.append(route))
        with peer.expect_request('**/api/v1/beta/status'):
            peer.goto(origin)
        create = peer.get_by_role('button', name='Create your first business', exact=True)
        expect(create).to_be_visible()
        before_status = create.bounding_box()
        # Deliver status after controls are usable: it must not move a click target.
        assert len(status_routes) == 1
        status_routes[0].fulfill(response=context.request.get(origin + '/api/v1/beta/status'))
        expect(peer.get_by_text('New admissions are paused.', exact=False)).to_be_visible()
        assert create.bounding_box() == before_status
        create.click()
        peer.get_by_label('Business name').fill('Unsubmitted invitee draft')
        page.bring_to_front()
        page.get_by_role('button', name='Review invitation').click()
        page.get_by_role('button', name='Accept invitation').click()
        expect(page.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        expect(peer.get_by_role('heading', name='Your ledger is ready')).to_be_visible()
        expect(peer.get_by_label('Business name')).to_have_count(0)
        page.reload()
        assert context.request.get(origin + '/api/v1/session').json()['entity']['role'] == 'editor'
        with repo.database.transaction(read_only=True) as connection:
            assert connection.scalar(text('SELECT count(*) FROM workspace_bootstraps WHERE user_id=:user'), {'user': invitee.user_id}) == 0
            assert connection.scalar(text('SELECT count(*) FROM memberships WHERE user_id=:user'), {'user': invitee.user_id}) == 1


@pytest.mark.parametrize('width', [1440, 390])
def test_workspace_switch_invite_demote_remove_and_logout(named_server, width, native_browser):
    origin, repo, alice, bob, a, b, _, _ = named_server
    database = repo.database
    with database.transaction(read_only=True) as connection:
        configuration_hash = connection.scalar(text("SELECT configuration_hash FROM identity_environments WHERE environment='test'"))
    sessions = PostgresSessionRepository(database, 'test', configuration_hash)
    alice_raw, _ = sessions.issue(verified())
    bob_raw, _ = sessions.issue(verified('bob'))
    repo.set_membership(b, alice.id, 'read_only')
    create_postgres_services(database, 'alpha').accounts.create('Private alpha account', 'Bank', '0', classification='Asset', parent_id='')
    with native_browser('chromium') as browser:
        first = browser.new_context(viewport={'width': width, 'height': 1000})
        second = browser.new_context(viewport={'width': width, 'height': 1000})
        for context, raw in ((first, alice_raw), (second, bob_raw)):
            context.add_cookies([{'name': 'tabula_dev_session', 'value': raw, 'url': origin, 'httpOnly': True, 'sameSite': 'Lax'}])
        owner, member = first.new_page(), second.new_page()
        owner.goto(origin + '/accounts')
        expect(owner.get_by_text('Private alpha account', exact=True)).to_be_visible()
        assert first.request.get(origin + '/api/v1/entities/unknown/accounts').status == 403
        owner.get_by_label('Select business').select_option('beta')
        owner.get_by_role('button', name='Discard draft and switch').click()
        expect(owner.get_by_text('Role: Read-only', exact=True)).to_be_visible()
        expect(owner).to_have_url(origin + '/')
        expect(owner.get_by_text('Private alpha account', exact=True)).to_have_count(0)
        owner.goto(origin + '/accounts')
        expect(owner.get_by_text('Private alpha account', exact=True)).to_have_count(0)
        owner.get_by_label('Select business').select_option('alpha')
        owner.get_by_role('button', name='Discard draft and switch').click()
        expect(owner.get_by_text('Role: Owner', exact=True)).to_be_visible()
        expect(owner).to_have_url(origin + '/')
        owner.get_by_role('complementary').get_by_role('link', name='Business & members').click()
        owner.get_by_label('Recipient email').fill('shared@example.test')
        owner.get_by_label('Invitation role').select_option('editor')
        owner.get_by_role('button', name='Create invitation', exact=True).click()
        credential_input = owner.get_by_label('New invitation credential')
        expect(credential_input).to_be_visible()
        credential = credential_input.input_value()
        owner.get_by_role('button', name='Dismiss credential').click()
        member.goto(origin + '/workspace')
        member.get_by_label('Invitation credential', exact=True).fill(credential)
        member.get_by_role('button', name='Review invitation').click()
        member.get_by_role('button', name='Accept invitation').click()
        expect(member.get_by_label('Select business')).to_be_visible()
        # Confirmed acceptance now selects the explicitly reviewed business.
        expect(member.get_by_label('Select business')).to_have_value('alpha')
        expect(member.get_by_text('Role: Editor', exact=True)).to_be_visible()
        owner.reload()
        editor = owner.locator('section.surface').filter(has=owner.get_by_text('Current role: Editor · active', exact=True))
        editor.get_by_label('Member role').select_option('read_only')
        editor.get_by_role('button', name='Review membership change').click()
        owner.get_by_role('button', name='Confirm membership change', exact=True).click()
        expect(owner.get_by_text('Current role: Read-only · active', exact=True)).to_be_visible()
        member.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(member.get_by_text('Role: Read-only', exact=True)).to_be_visible()
        read_only = owner.locator('section.surface').filter(has=owner.get_by_text('Current role: Read-only · active', exact=True))
        read_only.get_by_label('Active membership').uncheck()
        read_only.get_by_role('button', name='Review membership change').click()
        owner.get_by_role('button', name='Confirm membership change', exact=True).click()
        expect(owner.get_by_text('Current role: Read-only · inactive', exact=True)).to_be_visible()
        member.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(member.get_by_text('Role: Owner', exact=True)).to_be_visible()
        assert second.request.get(origin + '/api/v1/entities/alpha/accounts').status == 403
        assert_accessible(owner)
        assert_accessible(member)
        assert owner.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        peer = first.new_page()
        peer.goto(origin)
        expect(peer.get_by_role('button', name='Sign out', exact=True)).to_be_visible()
        owner.get_by_role('button', name='Sign out', exact=True).click()
        expect(peer.get_by_role('link', name='Sign in', exact=True)).to_be_visible()
        assert second.request.get(origin + '/api/v1/session').status == 200


@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
@pytest.mark.parametrize('width', [1440, 320])
def test_same_name_businesses_remain_distinct_through_selection(named_server, width, engine, native_browser):
    origin, repo, alice, _, _, b, _, _ = named_server
    repo.set_membership(b, alice.id, 'read_only')
    name = 'Shared independent business ' * 4
    with repo.database.transaction() as connection:
        connection.execute(text("UPDATE business_entities SET name=:name WHERE id IN ('alpha','beta')"), {'name': name})
        configuration_hash = connection.scalar(text("SELECT configuration_hash FROM identity_environments WHERE environment='test'"))
    sessions = PostgresSessionRepository(repo.database, 'test', configuration_hash)
    raw, _ = sessions.issue(verified())
    sessions.select(raw, 'alpha')
    with native_browser(engine) as browser:
        context = browser.new_context(viewport={'width': width, 'height': 1000})
        context.add_cookies([{'name': 'tabula_dev_session', 'value': raw, 'url': origin, 'httpOnly': True, 'sameSite': 'Lax'}])
        page = context.new_page()
        # Isolate explicit selection from focus/periodic authority checks.
        page.add_init_script("Object.defineProperty(document, 'visibilityState', {get: () => 'hidden'})")
        page.goto(origin)
        selector = page.get_by_label('Select business')
        expect(selector.locator('option[value="alpha"]')).to_have_text(name.strip() + ' (reference: alpha) · USD · Owner')
        expect(selector.locator('option[value="beta"]')).to_have_text(name.strip() + ' (reference: beta) · USD · Read-only')
        selector.select_option('beta')
        dialog = page.get_by_role('alertdialog', name='Switch business')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text('Business reference: beta', exact=True)).to_be_visible()
        dialog.get_by_role('button', name='Keep current business').click()
        expect(selector).to_have_value('alpha')
        assert context.request.get(origin + '/api/v1/session').json()['entity']['id'] == 'alpha'
        selector.select_option('beta')
        page.get_by_role('button', name='Discard draft and switch').click()
        expect(page.get_by_text('Role: Read-only', exact=True)).to_be_visible()
        expect(page.get_by_text('Business reference: beta', exact=True)).to_be_visible()
        assert context.request.get(origin + '/api/v1/session').json()['entity']['id'] == 'beta'
        assert context.request.get(origin + '/api/v1/entities/unknown/accounts').status == 403
        page.goto(origin + '/businesses')
        businesses = page.get_by_role('region', name='Available businesses')
        expect(businesses.get_by_text(name.strip() + ' (reference: alpha)', exact=False)).to_be_visible()
        expect(businesses.get_by_text(name.strip() + ' (reference: beta)', exact=False)).to_be_visible()
        expect(businesses.get_by_text('Selected business', exact=True)).to_be_visible()
        assert_accessible(page)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('country,currency', [('US','USD'), ('CA','CAD')])
def test_beta_pending_operator_approval_and_pause_preserve_business(named_server, engine, width, native_browser, country, currency):
    from tabula_rasa.infrastructure.postgres.beta import PostgresBeta

    origin, repo, _, _, _, _, claims, info = named_server
    claims['sub'] = info['sub'] = 'beta-applicant'
    with repo.database.transaction(read_only=True) as c:
        configuration = c.scalar(text("SELECT configuration_hash FROM identity_environments WHERE environment='test'"))
    sessions = PostgresSessionRepository(repo.database, 'test', configuration)
    raw, operator = sessions.issue(verified('beta-operator'))
    with repo.database.transaction() as c:
        c.execute(text("""INSERT INTO beta_operator_grants VALUES
            (:user,'test',clock_timestamp()+interval '1 hour','synthetic-browser')"""), {'user':operator.user_id})
    beta = PostgresBeta(repo.database)
    def command(operation, **fields):
        revision = beta.operate(sessions, raw, {'operation':'inspect'})['program']['revision']
        return beta.operate(sessions, raw, dict(operation=operation, revision=revision,
            reason='synthetic-browser', approval_reference='synthetic-only', **fields))
    with native_browser(engine) as browser:
        context = browser.new_context(viewport={'width':width,'height':1000})
        configure_login(context, origin, claims)
        page = context.new_page()
        page.goto(origin)
        page.get_by_role('link', name='Sign in', exact=True).click()
        expect(page.get_by_role('heading',name='Invitation-only free beta')).to_be_visible()
        expect(page.get_by_text('New admissions are paused.',exact=False)).to_be_visible()
        page.get_by_role('button',name='Create your first business',exact=True).click()
        page.get_by_label('Business name').fill('Synthetic beta business')
        page.get_by_label('Business country and currency').select_option(country)
        page.get_by_role('button',name='Review defaults').click()
        page.get_by_role('button',name='Create business',exact=True).click()
        expect(page.get_by_text('Enrollment: pending.',exact=False)).to_be_visible()
        assert_accessible(page)
        state = beta.operate(sessions,raw,{'operation':'inspect'})
        admission = state['admissions'][0]['id']
        assert state['counts']=={'businesses':0,'users':0}
        command('approve',admission_id=admission)
        command('configure',stage='initial',business_cap=3,user_cap=None,
                creation_paused=False,invitations_paused=False,suspended=False)
        page.reload()
        expect(page.get_by_role('region',name='Beta enrollment').get_by_text(
            f'Synthetic beta business: approved. Book currency: {currency}.',exact=True)).to_be_visible()
        page.get_by_role('button',name='Create your first business',exact=True).click()
        expect(page.get_by_text('Enrollment: approved.',exact=False)).to_be_visible()
        page.get_by_label('Business name').fill('Synthetic beta business')
        page.get_by_label('Business country and currency').select_option(country)
        page.get_by_role('button',name='Review defaults').click()
        page.get_by_role('button',name='Create business',exact=True).click()
        expect(page.get_by_role('heading',name='Your ledger is ready')).to_be_visible()
        expect(page.get_by_text('Book currency: ' + currency, exact=True)).to_be_visible()
        command('configure',stage='initial',business_cap=3,user_cap=None,
                creation_paused=True,invitations_paused=True,suspended=False)
        page.goto(origin+'/businesses')
        expect(page.get_by_text('New admissions are paused.',exact=False)).to_be_visible()
        expect(page.get_by_role('region',name='Available businesses').get_by_role('listitem')).to_have_count(1)
        assert_accessible(page)
        if evidence_root := os.environ.get('TABULA_RASA_BROWSER_EVIDENCE'):
            target=Path(evidence_root)
            target.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(target/f'{engine}-{width}-beta-paused.png'),full_page=True)
        session=context.request.get(origin+'/api/v1/session').json()
        ledger=context.request.get(origin+'/api/v1/entities/'+session['entity']['id']+'/accounts')
        assert ledger.status==200
        handoff=context.request.post(origin+'/api/v1/entities/'+session['entity']['id']+'/exports/handoff',
            headers={'Origin':origin,'X-CSRF-Token':session['csrf_token']},data={'scope':'metadata_only'})
        assert handoff.status==200
