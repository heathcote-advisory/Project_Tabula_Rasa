"""Compiled React -> production HTTP handlers -> real synthetic PostgreSQL."""

import os
from pathlib import Path
import socket
from threading import Thread
from time import monotonic, sleep

from playwright.sync_api import expect
import pytest
import uvicorn

from tabula_rasa.http.app import create_app
from tabula_rasa.http.composition import PostgresRuntime
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.http.context import RequestContext
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tests.postgres.test_http_baselines import commercial as commercial, loaded, snapshot

DIST = Path(__file__).resolve().parents[2] / 'web' / 'dist'


@pytest.fixture
def document_store():
    return None


@pytest.fixture
def browser_resolver():
    # Explicit loopback fixture: no named session/activity endpoint is composed.
    context = RequestContext('actor', 'org', 'alpha', frozenset({'bookkeeping:read', 'bookkeeping:write', 'bookkeeping:import'}),
                             'browser-scope', 'csrf', trusted_development=True)
    return lambda request: context


@pytest.fixture
def commercial_server(database, commercial, browser_resolver, document_store):
    if not (DIST / 'index.html').is_file():
        if os.environ.get('TABULA_RASA_REQUIRE_BROWSER_TESTS') == '1':
            pytest.fail('Build web before running required native browser tests.')
        pytest.skip('Build web to run native browser baseline tests.')
    runtime = PostgresRuntime(database, browser_resolver,
        service_factory=lambda context: create_postgres_services(database, context.entity_id,
            **({'document_store': document_store, 'document_store_kind': 'fake'} if document_store is not None else {})))
    runtime.close = lambda: None  # The synthetic DB fixture owns this pool.
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    app = create_app(settings=HttpSettings('test', origin), runtime_factory=lambda settings: runtime, static_dir=DIST)
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='critical',
                                         timeout_graceful_shutdown=2))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 5
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(0.01)
        assert server.started, 'Synthetic HTTP server did not start.'
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), 'Synthetic HTTP server did not stop.'


def edit_label(resource, row):
    if 'name' in row:
        return 'Edit ' + row['name']
    if resource == 'transactions':
        return f"Edit {row['txn_date']} · {row['description'] or row['kind']} · {row['amount']}"
    if resource == 'journals':
        return f"Edit {row['txn_date']} · {row['description'] or 'Journal'}"
    return f"Edit {row['trip_date']} · {row['destination']} · {row['miles']} miles"


@pytest.mark.parametrize('resource,field', [
    ('accounts', 'Account name'), ('categories', 'Category name'), ('payees', 'Name'), ('tags', 'Name'),
    ('transactions', 'Description / memo'), ('journals', 'Description / memo'), ('mileage', 'Notes'),
])
def test_two_real_forms_keep_original_baseline_and_explicitly_review_conflicts(database, commercial, commercial_server, resource, field, native_browser):
    _, _, clients, ids = commercial
    row = loaded(clients[0], resource, ids[resource])
    with native_browser('chromium') as browser:
        a = browser.new_context(viewport={'width': 1440, 'height': 1000})
        b = browser.new_context(viewport={'width': 390, 'height': 844})
        first, second = a.new_page(), b.new_page()
        for page in (first, second):
            page.goto(commercial_server + '/' + resource)
            page.get_by_role('button', name=edit_label(resource, row), exact=True).click()
            expect(page.get_by_label(field, exact=True)).to_be_visible()
        # Drafts are independently loaded before either writer commits.
        second.get_by_label(field, exact=True).fill('Client B draft')
        first.get_by_label(field, exact=True).fill('Client A commit')
        with first.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            first.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        before = snapshot(database)
        with second.expect_response(lambda response: response.request.method == 'PATCH') as stale:
            second.get_by_role('button', name='Save changes', exact=True).click()
        assert stale.value.status == 409 and stale.value.json()['error']['code'] == 'stale'
        expect(second.get_by_role('alert')).to_contain_text('Your draft is preserved')
        expect(second.get_by_label(field, exact=True)).to_have_value('Client B draft')
        original_token = stale.value.request.post_data_json['baseline']
        assert original_token == row['baseline']
        remove_label = 'Deactivate' if resource == 'accounts' else 'Delete'
        second.get_by_role('button', name=remove_label, exact=True).click()
        with second.expect_response(lambda response: response.request.method == ('POST' if resource == 'accounts' else 'DELETE')) as deleted:
            second.get_by_role('button', name='Confirm ' + remove_label.lower(), exact=True).click()
        assert deleted.value.status == 409 and deleted.value.json()['error']['code'] == 'stale'
        assert deleted.value.request.post_data_json['baseline'] == original_token
        body = dict(stale.value.request.post_data_json)
        del body['baseline']
        missing = b.request.patch(commercial_server + '/api/v1/entities/alpha/' + resource + '/' + ids[resource],
            headers={'Origin': commercial_server, 'X-CSRF-Token': 'csrf'}, data=body)
        assert missing.status == 422 and missing.json()['error']['code'] == 'baseline_required'
        assert snapshot(database) == before
        second.get_by_role('button', name='Review latest record', exact=True).click()
        expect(second.get_by_role('alertdialog', name='Reload record')).to_be_visible()
        expect(second.get_by_label(field, exact=True)).to_have_value('Client B draft')
        second.get_by_role('button', name='Discard draft and reload', exact=True).click()
        expect(second.get_by_label(field, exact=True)).to_have_value('Client A commit')
        expect(second.get_by_role('alert')).to_have_count(0)
        second.get_by_label(field, exact=True).fill('Client B reviewed')
        with second.expect_response(lambda response: response.request.method == 'PATCH') as reviewed:
            second.get_by_role('button', name='Save changes', exact=True).click()
        assert reviewed.value.status == 200
        assert reviewed.value.request.post_data_json['baseline'] != original_token
        expect(second.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
        assert second.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        if resource == 'transactions':
            second.get_by_label(field, exact=True).fill('Unsaved navigation draft')
            second.get_by_role('link', name='Journals', exact=True).click()
            expect(second.get_by_role('alertdialog', name='Unsaved changes')).to_be_visible()
            second.get_by_role('button', name='Keep editing', exact=True).click()
            expect(second.get_by_label(field, exact=True)).to_have_value('Unsaved navigation draft')
            second.get_by_role('link', name='Journals', exact=True).click()
            second.get_by_role('button', name='Discard draft and leave', exact=True).click()
            second.get_by_role('button', name='Edit 2026-10-05 · Loaded', exact=True).click()
            expect(second.get_by_label(field, exact=True)).to_have_value('Loaded')


@pytest.mark.parametrize('resource', ['payees', 'tags'])
def test_real_merge_form_keeps_target_and_source_baselines(database, commercial, commercial_server, resource, native_browser):
    alpha, _, clients, ids = commercial
    service = getattr(alpha, resource)
    service.create('Merge target')
    service.create('Second source')
    original = loaded(clients[0], resource, ids[resource])
    with native_browser('chromium') as browser:
        first, second = browser.new_context().new_page(), browser.new_context().new_page()
        first.goto(commercial_server + '/' + resource)
        first.get_by_role('button', name='Edit ' + original['name'], exact=True).click()
        second.goto(commercial_server + '/' + resource + '/merge')
        second.get_by_role('button', name='Merge ' + resource, exact=True).click()
        second.get_by_label('Merge target', exact=True).check()
        second.get_by_label('Keep this record').select_option(label='Merge target')
        expect(second.get_by_label('Merge target', exact=True)).to_have_count(0)
        second.get_by_role('button', name='Confirm merge', exact=True).click()
        expect(second.get_by_role('alert')).to_contain_text('Check the highlighted fields and try again.')
        second.get_by_label(original['name'], exact=True).check()
        second.get_by_label('Second source', exact=True).check()
        second.get_by_label('Keep this record').select_option(label='Second source')
        expect(second.get_by_label('Second source', exact=True)).to_have_count(0)
        expect(second.get_by_label(original['name'], exact=True)).to_be_checked()
        second.get_by_label('Keep this record').select_option(label='Merge target')
        expect(second.get_by_label('Second source', exact=True)).not_to_be_checked()
        second.get_by_label('Second source', exact=True).check()
        first.get_by_label('Name', exact=True).fill('Other committed source')
        first.get_by_role('button', name='Save changes', exact=True).click()
        expect(first.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
        before = snapshot(database)
        with second.expect_response(lambda response: response.request.method == 'POST') as stale:
            second.get_by_role('button', name='Confirm merge', exact=True).click()
        assert stale.value.status == 409 and stale.value.json()['error']['code'] == 'stale'
        assert stale.value.request.post_data_json['baselines'][original['id']] == original['baseline']
        expect(second.get_by_label(original['name'], exact=True)).to_be_checked()
        assert snapshot(database) == before
        second.get_by_role('button', name='Review latest record', exact=True).click()
        second.get_by_role('button', name='Discard draft and reload', exact=True).click()
        expect(second.get_by_label('Other committed source', exact=True)).not_to_be_checked()
        second.get_by_label('Keep this record').select_option(label='Merge target')
        second.get_by_label('Other committed source', exact=True).check()
        second.get_by_label('Second source', exact=True).check()
        with second.expect_response(lambda response: response.request.method == 'POST') as merged:
            second.get_by_role('button', name='Confirm merge', exact=True).click()
        assert merged.value.status == 200 and merged.value.json()['count'] == '2'
        assert merged.value.request.post_data_json['baselines'][original['id']] != original['baseline']
        expect(second.get_by_role('status').filter(has_text='Records merged.')).to_be_visible()
        assert len(service.list()) == 1


@pytest.mark.parametrize('kind,category', [('Expense', 'office'), ('Income', 'sales'), ('Transfer', '')])
def test_real_transaction_editor_switches_kind_without_hidden_references(commercial, commercial_server, kind, category, native_browser):
    from tests.postgres.core_support import expense

    alpha, _, clients, _ = commercial
    initial_kind = 'Expense' if kind == 'Transfer' else 'Transfer'
    row = alpha.transactions.create(transaction_id='switch-kind', **expense(kind=initial_kind,
        category_id='office' if initial_kind == 'Expense' else '', description='Kind switch',
        to_account_id='bank' if initial_kind == 'Transfer' else ''))
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/transactions')
        page.get_by_role('button', name=edit_label('transactions', row), exact=True).click()
        page.get_by_label('Type', exact=True).select_option(kind)
        if category:
            page.get_by_label('Category', exact=True).select_option(category)
        else:
            page.get_by_label('To account', exact=True).select_option('bank')
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        body = saved.value.request.post_data_json
        assert body['baseline'] == row['baseline']
        assert body['changes']['category_id'] == category
        assert body['changes']['to_account_id'] == ('bank' if kind == 'Transfer' else '')
        actual = loaded(clients[0], 'transactions', row['id'])
        assert actual['kind'] == kind and actual['category_id'] == category
        assert actual['to_account_id'] == ('bank' if kind == 'Transfer' else '')


def test_real_journal_target_changes_require_explicit_selection(commercial, commercial_server, native_browser):
    _, _, clients, ids = commercial
    row = loaded(clients[0], 'journals', ids['journals'])
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/journals')
        page.get_by_role('button', name=edit_label('journals', row), exact=True).click()
        target = page.get_by_label('Target', exact=True).first
        target_type = page.get_by_label('Account or category', exact=True).first
        for kind in ('account', 'category', 'account'):
            target_type.select_option(kind)
            expect(target).to_have_value('')
            target.select_option('bank' if kind == 'account' else 'office')
        page.get_by_role('button', name='Add posting', exact=True).click()
        expect(page.get_by_label('Target', exact=True).nth(2)).to_have_value('')
        expect(page.get_by_label('Target', exact=True).nth(2).locator('option:checked')).to_have_text('Choose target')
        page.get_by_role('button', name='Remove posting 3', exact=True).click()
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == row['baseline']
        actual = loaded(clients[0], 'journals', row['id'])
        assert [line['id'] for line in actual['postings']] == [line['id'] for line in row['postings']]
        assert actual['postings'][0]['target_type'] == 'account'
        assert actual['postings'][0]['target_id'] == 'bank'


def test_account_deactivation_requires_confirmation_after_reactivation(commercial, commercial_server, native_browser):
    alpha, _, clients, ids = commercial
    alpha.accounts.deactivate(ids['accounts'])
    def read_account():
        response = clients[0].get('/api/v1/entities/alpha/accounts', params={'active_only': False})
        assert response.status_code == 200
        return next(item for item in response.json()['items'] if item['id'] == ids['accounts'])

    row = read_account()
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/accounts')
        page.get_by_role('button', name=edit_label('accounts', row), exact=True).click()
        page.get_by_label('Active', exact=True).check()
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == row['baseline']
        expect(page.get_by_label('Active', exact=True)).to_have_count(0)
        assert read_account()['active']
        page.get_by_role('button', name='Deactivate', exact=True).click()
        expect(page.get_by_role('alertdialog', name='Deactivate record')).to_be_visible()
        assert read_account()['active']
        page.get_by_role('button', name='Keep record', exact=True).click()
        page.get_by_role('button', name='Deactivate', exact=True).click()
        with page.expect_response(lambda response: response.request.method == 'POST') as removed:
            page.get_by_role('button', name='Confirm deactivate', exact=True).click()
        assert removed.value.status == 200
        assert removed.value.request.post_data_json['baseline'] == saved.value.json()['baseline']
        assert not read_account()['active']


@pytest.mark.parametrize('resource', ['transactions', 'journals', 'mileage'])
def test_edit_choices_preserve_only_field_specific_inactive_references(commercial, commercial_server, resource, native_browser):
    alpha, _, clients, ids = commercial
    journal = loaded(clients[0], 'journals', ids['journals'])
    journal['postings'][0]['payee_id'] = ids['payees']
    clients[0].patch('/api/v1/entities/alpha/journals/' + ids['journals'],
        headers={'Origin': 'http://testserver', 'X-CSRF-Token': 'csrf'},
        json={'baseline': journal['baseline'], 'changes': {'txn_date': journal['txn_date'], 'description': journal['description'], 'postings': journal['postings']}}).raise_for_status()
    alpha.accounts.deactivate('cash')
    alpha.accounts.deactivate('bank')
    alpha.payees.update(ids['payees'], active=False)
    alpha.tags.update(ids['tags'], active=False)
    unrelated_payee = alpha.payees.create('Unused payee', active=False)
    unrelated_tag = alpha.tags.create('Unused tag', active=False)
    row = loaded(clients[0], resource, ids[resource])
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/' + resource)
        page.get_by_role('button', name=edit_label(resource, row), exact=True).click()
        if resource == 'transactions':
            account = page.get_by_label('From account', exact=True)
            expect(account.locator('option[value="cash"]')).to_have_count(1)
            expect(account.locator('option[value="bank"]')).to_have_count(0)
            page.get_by_label('Type', exact=True).select_option('Transfer')
            destination = page.get_by_label('To account', exact=True)
            expect(destination.locator('option[value="cash"]')).to_have_count(0)
            expect(destination.locator('option[value="bank"]')).to_have_count(0)
            page.get_by_label('Type', exact=True).select_option('Expense')
            payee = page.get_by_label('Payee / customer', exact=True)
            expect(payee.locator(f'option[value="{ids["payees"]}"]')).to_have_count(1)
            expect(payee.locator(f'option[value="{unrelated_payee["id"]}"]')).to_have_count(0)
        elif resource == 'journals':
            target = page.get_by_label('Target', exact=True)
            expect(target.nth(1).locator('option[value="cash"]')).to_have_count(1)
            expect(target.nth(1).locator('option[value="bank"]')).to_have_count(0)
            payee = page.get_by_label('Payee / customer', exact=True)
            expect(payee.first.locator(f'option[value="{ids["payees"]}"]')).to_have_count(1)
            expect(payee.nth(1).locator(f'option[value="{ids["payees"]}"]')).to_have_count(0)
            page.get_by_role('button', name='Add posting', exact=True).click()
            expect(target.nth(2).locator('option[value="cash"]')).to_have_count(0)
            expect(payee.nth(2).locator(f'option[value="{ids["payees"]}"]')).to_have_count(0)
            page.get_by_role('button', name='Remove posting 3', exact=True).click()
        if resource != 'journals':
            tags = page.get_by_label('Tags', exact=True)
            expect(tags.locator(f'option[value="{ids["tags"]}"]')).to_have_count(1)
            expect(tags.locator(f'option[value="{unrelated_tag["id"]}"]')).to_have_count(0)
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == row['baseline']
        actual = loaded(clients[0], resource, row['id'])
        if resource == 'transactions':
            assert actual['account_id'] == row['account_id'] and actual['payee_id'] == row['payee_id']
        if resource != 'journals':
            assert actual['tag_ids'] == row['tag_ids']
        else:
            assert actual['postings'] == row['postings']


@pytest.mark.parametrize('resource', ['accounts', 'categories'])
def test_parent_choices_follow_two_level_compatibility(commercial, commercial_server, resource, native_browser):
    alpha, _, clients, ids = commercial
    if resource == 'accounts':
        child = alpha.accounts.create('Hierarchy child', 'Other', '0', classification='Asset', parent_id='cash')
        sibling = alpha.accounts.create('Other child', 'Other', '0', classification='Asset', parent_id='cash')
        other = alpha.accounts.create('Other group root', 'Other', '0', classification='Liability')
        field, group, new_group = 'Parent account', 'Classification', 'Liability'
    else:
        child = alpha.categories.create('Hierarchy child', 'Expense', parent_id='office')
        sibling = alpha.categories.create('Other child', 'Expense', parent_id='office')
        other = alpha.categories.create('Other group root', 'Income')
        field, group, new_group = 'Parent category', 'Kind', 'Income'
    root = loaded(clients[0], resource, ids[resource])
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/' + resource)
        page.get_by_role('button', name=edit_label(resource, root), exact=True).click()
        expect(page.get_by_label(field, exact=True).locator('option')).to_have_count(1)
        page.get_by_role('button', name='Back to records', exact=True).click()
        page.get_by_role('button', name=edit_label(resource, child), exact=True).click()
        parent = page.get_by_label(field, exact=True)
        expect(parent.locator(f'option[value="{child["id"]}"]')).to_have_count(0)
        expect(parent.locator(f'option[value="{sibling["id"]}"]')).to_have_count(0)
        expect(parent.locator(f'option[value="{other["id"]}"]')).to_have_count(0)
        expect(parent.locator(f'option[value="{root["id"]}"]')).to_have_count(1)
        page.get_by_label(group, exact=True).select_option(new_group)
        expect(parent).to_have_value('')
        expect(parent.locator(f'option[value="{root["id"]}"]')).to_have_count(0)
        expect(parent.locator(f'option[value="{other["id"]}"]')).to_have_count(1)
        parent.select_option(other['id'])
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == child['baseline']
        assert loaded(clients[0], resource, child['id'])['parent_id'] == other['id']


@pytest.mark.parametrize('account_type,classification', [
    ('Bank', 'Asset'), ('Cash', 'Asset'), ('Credit card', 'Liability'),
    ('Short-term debt', 'Liability'), ('Equity', 'Equity'),
])
def test_fixed_account_type_changes_align_classification(commercial, commercial_server, account_type, classification, native_browser):
    alpha, _, clients, _ = commercial
    previous = 'Liability' if classification == 'Asset' else 'Asset'
    parent = alpha.accounts.create('Previous parent', 'Other', '0', classification=previous)
    row = alpha.accounts.create('Subtype switch', 'Other', '0', classification=previous, parent_id=parent['id'])
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/accounts')
        page.get_by_role('button', name=edit_label('accounts', row), exact=True).click()
        page.get_by_label('Type', exact=True).select_option(account_type)
        expect(page.get_by_label('Classification', exact=True)).to_have_value(classification)
        expect(page.get_by_label('Classification', exact=True).locator('option')).to_have_count(1)
        expect(page.get_by_label('Parent account', exact=True)).to_have_value('')
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == row['baseline']
        actual = loaded(clients[0], 'accounts', row['id'])
        assert actual['type'] == account_type and actual['classification'] == classification
        assert not actual['parent_id']
        page.get_by_label('Type', exact=True).select_option('Other')
        expect(page.get_by_label('Classification', exact=True).locator('option')).to_have_count(3)
        expect(page.get_by_label('Classification', exact=True)).to_have_value(classification)


@pytest.mark.parametrize('resource', ['payees', 'tags'])
def test_managed_deactivation_confirms_original_baseline_and_retains_stale_draft(database, commercial, commercial_server, resource, native_browser):
    alpha, _, clients, ids = commercial
    row = loaded(clients[0], resource, ids[resource])
    with native_browser('chromium') as browser:
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.goto(commercial_server + '/' + resource)
        page.get_by_role('button', name=edit_label(resource, row), exact=True).click()
        page.get_by_label('Name', exact=True).fill('Deactivation draft')
        page.get_by_label('Active', exact=True).uncheck()
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.get_by_role('alertdialog', name='Deactivate record')).to_be_visible()
        assert loaded(clients[0], resource, row['id'])['active']
        page.get_by_role('button', name='Keep editing', exact=True).click()
        expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_focused()
        expect(page.get_by_label('Name', exact=True)).to_have_value('Deactivation draft')
        expect(page.get_by_label('Active', exact=True)).not_to_be_checked()
        getattr(alpha, resource).update(row['id'], name='Other writer')
        before = snapshot(database)
        page.get_by_role('button', name='Save changes', exact=True).click()
        with page.expect_response(lambda response: response.request.method == 'PATCH') as stale:
            page.get_by_role('button', name='Confirm deactivate', exact=True).click()
        assert stale.value.status == 409
        assert stale.value.request.post_data_json['baseline'] == row['baseline']
        assert snapshot(database) == before
        expect(page.get_by_role('alertdialog', name='Deactivate record')).to_have_count(0)
        expect(page.get_by_role('alert')).to_contain_text('Your draft is preserved')
        expect(page.get_by_label('Name', exact=True)).to_have_value('Deactivation draft')
        page.get_by_role('button', name='Review latest record', exact=True).click()
        page.get_by_role('button', name='Discard draft and reload', exact=True).click()
        expect(page.get_by_label('Name', exact=True)).to_have_value('Other writer')
        page.get_by_label('Active', exact=True).uncheck()
        page.get_by_role('button', name='Save changes', exact=True).click()
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Confirm deactivate', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] != row['baseline']
        assert not loaded(clients[0], resource, row['id'])['active']
        page.get_by_label('Active', exact=True).check()
        with page.expect_response(lambda response: response.request.method == 'PATCH') as reactivated:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert reactivated.value.status == 200
        expect(page.get_by_role('alertdialog', name='Deactivate record')).to_have_count(0)
        assert loaded(clients[0], resource, row['id'])['active']



def test_transfer_destination_excludes_source_and_clears_on_source_change(commercial, commercial_server, native_browser):
    from tests.postgres.core_support import expense

    alpha, _, clients, _ = commercial
    row = alpha.transactions.create(transaction_id='transfer-choices', **expense(kind='Transfer',
        category_id='', to_account_id='bank', description='Transfer choices'))
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/transactions')
        page.get_by_role('button', name=edit_label('transactions', row), exact=True).click()
        source = page.get_by_label('From account', exact=True)
        destination = page.get_by_label('To account', exact=True)
        expect(destination.locator('option[value="cash"]')).to_have_count(0)
        expect(destination).to_have_value('bank')
        source.select_option('bank')
        expect(destination).to_have_value('')
        expect(destination.locator('option[value="bank"]')).to_have_count(0)
        destination.select_option('cash')
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == row['baseline']
        actual = loaded(clients[0], 'transactions', row['id'])
        assert actual['account_id'] == 'bank' and actual['to_account_id'] == 'cash'


@pytest.mark.parametrize('resource', ['accounts', 'categories'])
def test_hierarchy_root_choices_preserve_children_compatibility(commercial, commercial_server, resource, native_browser):
    alpha, _, clients, ids = commercial
    service = getattr(alpha, resource)
    if resource == 'accounts':
        root = service.create('Protected root', 'Other', '0', classification='Asset')
        child = service.create('Protected child', 'Other', '0', classification='Asset', parent_id=root['id'])
        group, expected_group = 'Classification', 'Asset'
        name = 'Account name'
    else:
        root = service.create('Protected root', 'Expense')
        child = service.create('Protected child', 'Expense', parent_id=root['id'])
        group, expected_group = 'Kind', 'Expense'
        name = 'Category name'
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/' + resource)
        page.get_by_role('button', name=edit_label(resource, root), exact=True).click()
        expect(page.get_by_label(group, exact=True).locator('option')).to_have_count(1)
        expect(page.get_by_label(group, exact=True)).to_have_value(expected_group)
        if resource == 'accounts':
            types = page.get_by_label('Type', exact=True)
            assert types.locator('option').evaluate_all('(options) => options.map(option => option.value)') == ['Bank', 'Cash', 'Other']
            types.select_option('Bank')
            expect(page.get_by_label(group, exact=True)).to_have_value('Asset')
        page.get_by_label(name, exact=True).fill('Renamed root')
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == root['baseline']
        assert loaded(clients[0], resource, child['id'])['parent_id'] == root['id']
        service.update(child['id'], parent_id=ids[resource])
        page.reload()
        page.get_by_role('button', name='Edit Renamed root', exact=True).click()
        if resource == 'accounts':
            page.get_by_label('Type', exact=True).select_option('Other')
        expect(page.get_by_label(group, exact=True).locator('option')).to_have_count(3 if resource == 'accounts' else 2)



@pytest.mark.parametrize('kind,category', [('Expense', 'office'), ('Income', 'sales')])
def test_income_expense_switch_clears_category_payload_before_selection(database, commercial, commercial_server, kind, category, native_browser):
    from tests.postgres.core_support import expense

    alpha, _, clients, _ = commercial
    initial = 'Income' if kind == 'Expense' else 'Expense'
    row = alpha.transactions.create(transaction_id='category-switch', **expense(kind=initial,
        category_id='sales' if initial == 'Income' else 'office', description='Category switch'))
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/transactions')
        page.get_by_role('button', name=edit_label('transactions', row), exact=True).click()
        page.get_by_label('Type', exact=True).select_option(kind)
        expect(page.get_by_label('Category', exact=True)).to_have_value('')
        before = snapshot(database)
        with page.expect_response(lambda response: response.request.method == 'PATCH') as rejected:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert rejected.value.status == 422
        assert rejected.value.request.post_data_json['changes']['category_id'] == ''
        assert snapshot(database) == before
        page.get_by_label('Category', exact=True).select_option(category)
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == row['baseline']
        assert loaded(clients[0], 'transactions', row['id'])['category_id'] == category


def test_new_payee_replaces_loaded_payee_and_existing_selection_clears_new_name(commercial, commercial_server, native_browser):
    _, _, clients, ids = commercial
    row = loaded(clients[0], 'transactions', ids['transactions'])
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/transactions')
        page.get_by_role('button', name=edit_label('transactions', row), exact=True).click()
        selected = page.get_by_label('Payee / customer', exact=True)
        new_name = page.get_by_label('New payee name (optional)', exact=True)
        expect(selected).to_have_value(ids['payees'])
        new_name.fill('Replacement payee')
        expect(selected).to_have_value('')
        selected.select_option(ids['payees'])
        expect(new_name).to_have_value('')
        new_name.fill('Replacement payee')
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        payload = saved.value.request.post_data_json
        assert payload['baseline'] == row['baseline']
        assert payload['changes']['payee_id'] == ''
        assert payload['changes']['new_payee_name'] == 'Replacement payee'
        actual = loaded(clients[0], 'transactions', row['id'])
        assert actual['payee_id'] != ids['payees']
        payees = clients[0].get('/api/v1/entities/alpha/payees').json()['items']
        assert next(payee for payee in payees if payee['id'] == actual['payee_id'])['name'] == 'Replacement payee'
