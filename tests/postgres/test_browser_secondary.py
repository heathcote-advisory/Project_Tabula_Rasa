"""Compiled browser secondary workflows against native isolated PostgreSQL."""

from dataclasses import replace
import re

from playwright.sync_api import expect
import pytest

from tabula_rasa.http.context import TrustedDevelopmentContext
from tests.postgres.secondary_support import asset, mixed
from tests.postgres.browser_quality_support import assert_accessible
from tests.postgres.test_browser_baselines import commercial_server as commercial_server
from tests.postgres.test_browser_baselines import document_store as document_store
from tests.postgres.test_browser_workflows import capture
from tests.postgres.test_http_baselines import commercial as commercial, snapshot


@pytest.fixture
def browser_resolver(request):
    resolver = TrustedDevelopmentContext('alpha', 'csrf', entity_ids=('beta',))
    if getattr(request, 'param', '') == 'read_only':
        resolver.contexts = tuple(replace(context, capabilities=frozenset({'bookkeeping:read'})) for context in resolver.contexts)
    return resolver


@pytest.mark.parametrize('browser_resolver', ['read_only'], indirect=True)
def test_read_only_secondary_details_show_saved_terms_without_commands(database, commercial, commercial_server, native_browser):
    alpha, _, _, _ = commercial
    offset = alpha.accounts.create('Accumulated depreciation', 'Other', classification='Asset')['id']
    alpha.assets.create(asset_id='asset', **asset(offset))
    alpha.mixed_use.create(source_id='source', linked_transaction_id='projection', **mixed())
    alpha.recurring.create_expense(schedule_id='schedule', name='Subscription', txn_date='2026-01-01', recurrence_unit='month',
                                  account_id='cash', category_id='office', amount='10.00')
    before = snapshot(database)
    with native_browser('chromium') as browser:
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        for path, field, value in [('assets/asset', 'Cost basis', '100.00'), ('mixed-use/source', 'Total expense', '100.00'),
                                   ('recurring/schedule', 'Amount', '10.00')]:
            page.goto(commercial_server + '/' + path)
            expect(page.get_by_label(field, exact=True)).to_have_value(value)
            expect(page.get_by_label(field, exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='Save changes', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='Delete', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='Post due depreciation', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='Pause schedule', exact=True)).to_have_count(0)
            assert_accessible(page)
        response = page.request.post(commercial_server + '/api/v1/entities/alpha/recurring/reconcile',
                                     headers={'Origin': commercial_server, 'X-CSRF-Token': 'csrf'}, data={'through_date': '2026-02-01'})
        assert response.status == 403
        assert snapshot(database) == before


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
def test_browser_recurring_and_mixed_source_lifecycle(database, commercial, commercial_server, width, engine, native_browser):
    alpha, beta, _, _ = commercial
    with native_browser(engine) as browser:
        page = browser.new_page(viewport={'width': width, 'height': 1000 if width > 760 else 844})
        page.goto(commercial_server + '/recurring/new')
        page.get_by_label('Schedule name', exact=True).fill('Browser subscription')
        page.get_by_label('Start date', exact=True).fill('2026-01-01')
        page.get_by_label('Amount', exact=True).fill('10.00')
        page.get_by_label('From account', exact=True).select_option('cash')
        page.get_by_label('Expense category', exact=True).select_option('office')
        assert page.get_by_label('Repeat every', exact=True).input_value() == '1'
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/recurring')) as created:
            page.get_by_role('button', name='Save recurring expense', exact=True).click()
        assert created.value.status == 201
        schedule_id = created.value.json()['id']
        expect(page.get_by_role('status').filter(has_text='Record created.')).to_be_visible()
        page.get_by_role('button', name='Back to records', exact=True).click()
        before = snapshot(database)
        page.get_by_role('link', name='Manage Browser subscription', exact=True).click()
        expect(page.get_by_role('cell', name='initial', exact=True)).to_be_visible()
        assert snapshot(database) == before
        page.get_by_role('button', name='Pause schedule', exact=True).click()
        page.get_by_role('button', name='Confirm pause schedule', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Schedule pause completed.')).to_be_visible()
        assert alpha.recurring.get(schedule_id)['status'] == 'paused'
        page.get_by_role('button', name='Back to records', exact=True).click()
        page.get_by_role('link', name='Manage Browser subscription', exact=True).click()
        page.get_by_role('button', name='Resume schedule', exact=True).click()
        page.get_by_role('button', name='Confirm resume schedule', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Schedule resume completed.')).to_be_visible()
        page.get_by_role('button', name='Back to records', exact=True).click()
        page.get_by_label('Generate through', exact=True).fill('2026-02-01')
        page.get_by_role('button', name='Generate due occurrences', exact=True).click()
        page.get_by_role('button', name='Confirm generation', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Generated 1 occurrence(s).')).to_be_visible()
        assert len(alpha.recurring.detail(schedule_id)['transactions']) == 2
        capture(page, f'recurring-{width}-{engine}')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.goto(commercial_server + '/mixed-use/new')
        page.get_by_label('Total expense', exact=True).fill('100.00')
        page.get_by_label('Business use %', exact=True).fill('33.33333')
        page.get_by_label('Allocation rationale', exact=True).fill('Business allocation')
        page.get_by_label('From account', exact=True).select_option('cash')
        page.get_by_label('Expense category', exact=True).select_option('office')
        page.get_by_label('Description / memo', exact=True).fill('Browser allocation')
        expect(page.get_by_role('status').filter(has_text='Ledger result: 33.33')).to_be_visible()
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/mixed-use')) as created_source:
            page.get_by_role('button', name='Save mixed-use expense', exact=True).click()
        assert created_source.value.status == 201
        source_id = created_source.value.json()['id']
        expect(page.get_by_role('status').filter(has_text='Record created.')).to_be_visible()
        page.get_by_role('button', name='Back to records', exact=True).click()
        page.get_by_role('link', name='Manage ' + created_source.value.json()['txn_date'] + ' · Browser allocation', exact=True).click()
        page.get_by_label('Total expense', exact=True).fill('200.00')
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
        assert alpha.mixed_use.get(source_id)['allocated_amount'] == '66.67'
        capture(page, f'mixed-use-{width}-{engine}')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.goto(commercial_server + '/transactions')
        page.get_by_label('Search transactions', exact=True).fill(created_source.value.json()['linked_transaction_id'])
        expect(page.get_by_role('status').filter(has_text='1 matching entries')).to_be_visible()
        projection = page.get_by_role('row').filter(has=page.get_by_role('rowheader', name='Expense · Mixed-use', exact=True))
        expect(projection.get_by_role('rowheader', name='Expense · Mixed-use', exact=True)).to_be_visible()
        expect(projection.get_by_role('button', name=re.compile(r'^Edit '))).to_have_count(0)
        expect(projection.get_by_role('link')).to_have_count(1)
        projection.get_by_role('link', name='Manage mixed-use expense', exact=True).click()
        page.get_by_role('link', name='Manage ' + created_source.value.json()['txn_date'] + ' · Browser allocation', exact=True).click()
        page.get_by_role('button', name='Delete', exact=True).click()
        page.get_by_role('button', name='Confirm delete', exact=True).click()
        expect(page.get_by_role('heading', name='No records to edit', exact=True)).to_be_visible()
        assert alpha.mixed_use.sources() == [] and beta.recurring.list() == [] and beta.mixed_use.sources() == []


def test_dropped_write_and_background_read_keep_loaded_draft(database, commercial, commercial_server, native_browser):
    alpha, _, _, _ = commercial
    alpha.mixed_use.create(source_id='source', linked_transaction_id='projection', **mixed())
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/mixed-use/source')
        page.get_by_label('Allocation rationale', exact=True).fill('Preserved local draft')
        before = snapshot(database)
        calls = []

        def unavailable(route):
            calls.append(route.request.method)
            route.abort()

        page.route('**/api/v1/entities/alpha/mixed-use/source', unavailable)
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('Your draft is preserved')
        expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_enabled()
        assert calls.count('PATCH') == 1 and 'GET' in calls
        expect(page.get_by_label('Allocation rationale', exact=True)).to_have_value('Preserved local draft')
        expect(page.get_by_role('status').filter(has_text='Changes saved.')).to_have_count(0)
        assert snapshot(database) == before


@pytest.mark.parametrize('resource,field,property_name,heading,list_heading', [
    ('assets', 'Notes', 'notes', 'Asset detail', 'Assets'),
    ('mixed-use', 'Allocation rationale', 'allocation_rationale', 'Mixed-use expense', 'Mixed-use expenses'),
    ('recurring', 'Schedule name', 'name', 'Recurring schedule', 'Recurring expenses'),
])
def test_secondary_cached_revisit_waits_for_fresh_record_and_rejects_failed_read(database, commercial, commercial_server,
                                                                              resource, field, property_name, heading, list_heading, native_browser):
    alpha, _, _, _ = commercial
    if resource == 'assets':
        offset = alpha.accounts.create('Accumulated depreciation', 'Other', classification='Asset')['id']
        row = alpha.assets.create(asset_id='asset', **asset(offset))
    elif resource == 'mixed-use':
        row = alpha.mixed_use.create(source_id='source', linked_transaction_id='projection', **mixed())
    else:
        row = alpha.recurring.create_expense(schedule_id='schedule', name='Subscription', txn_date='2026-01-01', recurrence_unit='month',
                                            account_id='cash', category_id='office', amount='10.00')['schedule']

    def update():
        if resource == 'assets':
            return alpha.assets.update(row['id'], expected={'baseline': row['baseline']}, notes='Other client edit')
        if resource == 'mixed-use':
            return alpha.mixed_use.update(row['id'], expected_source={'baseline': row['baseline']}, allocation_rationale='Other client edit')
        return alpha.recurring.update(row['id'], expected_schedule={'baseline': row['baseline']}, name='Other client edit')

    endpoint = '**/api/v1/entities/alpha/' + resource + '/' + row['id']
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + '/' + resource + '/' + row['id'])
        expect(page.get_by_label(field, exact=True)).to_have_value(row[property_name])
        page.get_by_role('button', name='Back to records', exact=True).click()
        # Commit the list view before going back so this is a new detail visit.
        expect(page.get_by_role('heading', name=list_heading, exact=True)).to_be_visible()
        updated = update()
        waiting = []
        page.route(endpoint, lambda route: waiting.append(route))
        page.go_back()
        expect(page.get_by_role('heading', name=heading, exact=True)).to_be_visible()
        expect(page.get_by_label(field, exact=True)).to_have_count(0)
        expect(page.get_by_role('status').filter(has_text='Loading')).to_be_visible()
        assert len(waiting) == 1
        waiting.pop().continue_()
        expect(page.get_by_label(field, exact=True)).to_have_value('Other client edit')
        page.unroute(endpoint)
        page.get_by_label(field, exact=True).fill('Fresh visit draft')
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200 and saved.value.request.post_data_json['baseline'] == updated['baseline']
        expect(page.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
        page.get_by_role('button', name='Back to records', exact=True).click()
        expect(page.get_by_role('heading', name=list_heading, exact=True)).to_be_visible()
        before = snapshot(database)
        page.route(endpoint, lambda route: route.abort())
        page.go_back()
        expect(page.get_by_role('alert')).to_be_visible()
        expect(page.get_by_label(field, exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name='Save changes', exact=True)).to_have_count(0)
        assert snapshot(database) == before


@pytest.mark.parametrize('width', [1440, 390, 320])
def test_secondary_forms_and_details_are_accessible(commercial, commercial_server, width, native_browser):
    alpha, _, _, _ = commercial
    offset = alpha.accounts.create('Accumulated depreciation', 'Other', classification='Asset')['id']
    alpha.assets.create(asset_id='asset', **asset(offset))
    alpha.mixed_use.create(source_id='source', linked_transaction_id='projection', **mixed())
    alpha.recurring.create_expense(schedule_id='schedule', name='Subscription', txn_date='2026-01-01', recurrence_unit='month',
                                  account_id='cash', category_id='office', amount='10.00')
    with native_browser('chromium') as browser:
        page = browser.new_page(viewport={'width': width, 'height': 1000 if width > 760 else 844})
        for path, label in [('assets/new', 'Asset name'), ('mixed-use/new', 'Total expense'), ('recurring/new', 'Schedule name'),
                            ('assets/asset', 'Notes'), ('mixed-use/source', 'Allocation rationale'), ('recurring/schedule', 'Schedule name')]:
            page.goto(commercial_server + '/' + path)
            expect(page.get_by_label(label, exact=True)).to_be_visible()
            assert_accessible(page)


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
def test_browser_asset_posting_confirmation_saved_history_and_repeat_prevention(database, commercial, commercial_server, width, engine, native_browser):
    alpha, beta, _, _ = commercial
    offset = alpha.accounts.create('Accumulated depreciation', 'Other', classification='Asset')['id']
    with native_browser(engine) as browser:
        page = browser.new_page(viewport={'width': width, 'height': 1000 if width > 760 else 844})
        page.goto(commercial_server + '/assets/new')
        page.get_by_label('Asset name', exact=True).fill('Browser equipment')
        page.get_by_label('Asset type', exact=True).select_option('Computers & office equipment')
        page.get_by_label('Acquisition date', exact=True).fill('2026-01-01')
        page.get_by_label('Depreciation starts', exact=True).fill('2026-01-01')
        page.get_by_label('Cost basis', exact=True).fill('100.00')
        page.get_by_label('Useful life (quarters)', exact=True).fill('4')
        page.get_by_label('Depreciation expense category', exact=True).select_option('office')
        page.get_by_label('Accumulated depreciation account', exact=True).select_option(offset)
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/assets')) as created:
            page.get_by_role('button', name='Save asset', exact=True).click()
        assert created.value.status == 201
        asset_id = created.value.json()['id']
        expect(page.get_by_role('status').filter(has_text='Record created.')).to_be_visible()
        page.get_by_role('button', name='Back to records', exact=True).click()
        page.get_by_role('link', name='Manage Browser equipment', exact=True).click()
        expect(page.get_by_role('cell', name='Pending', exact=True)).to_have_count(4)
        page.get_by_label('Post depreciation through', exact=True).fill('2026-06-30')
        before = snapshot(database)
        page.get_by_role('button', name='Post due depreciation', exact=True).click()
        expect(page.get_by_role('alertdialog', name='Post due depreciation', exact=True)).to_be_visible()
        page.get_by_role('button', name='Keep record', exact=True).click()
        assert snapshot(database) == before
        page.get_by_role('button', name='Post due depreciation', exact=True).click()
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/post')) as posted:
            page.get_by_role('button', name='Confirm post due depreciation', exact=True).click()
        assert posted.value.status == 200 and posted.value.json()['posted_count'] == 2
        expect(page.get_by_role('status').filter(has_text='Posted 2 depreciation period(s).')).to_be_visible()
        expect(page.get_by_role('cell', name='Posted', exact=True)).to_have_count(2)
        capture(page, f'assets-{width}-{engine}')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.get_by_role('button', name='Back to records', exact=True).click()
        page.get_by_role('link', name='Manage Browser equipment', exact=True).click()
        expect(page.get_by_label('Cost basis', exact=True)).to_be_disabled()
        expect(page.get_by_label('Accumulated depreciation account', exact=True)).to_be_disabled()
        expect(page.get_by_role('button', name='Delete', exact=True)).to_have_count(0)
        page.get_by_label('Post depreciation through', exact=True).fill('2026-06-30')
        page.get_by_role('button', name='Post due depreciation', exact=True).click()
        page.get_by_role('button', name='Confirm post due depreciation', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Posted 0 depreciation period(s).')).to_be_visible()
        assert len(alpha.assets.detail(asset_id)['history']) == 2 and beta.assets.list() == []
        page.goto(commercial_server + '/transactions')
        expect(page.get_by_role('link', name='Manage in Assets', exact=True)).to_have_count(2)
        page.get_by_role('link', name='Manage in Assets', exact=True).first.click()
        expect(page.get_by_role('link', name='Manage Browser equipment', exact=True)).to_be_visible()


@pytest.mark.parametrize('resource,field', [('mixed-use', 'Allocation rationale'), ('recurring', 'Schedule name'), ('assets', 'Notes')])
def test_secondary_two_browser_drafts_keep_baselines_and_review_conflicts(database, commercial, commercial_server, resource, field, native_browser):
    alpha, _, _, _ = commercial
    if resource == 'mixed-use':
        row = alpha.mixed_use.create(source_id='source', linked_transaction_id='projection', **mixed())
    elif resource == 'recurring':
        row = alpha.recurring.create_expense(schedule_id='schedule', name='Subscription', txn_date='2026-01-01', recurrence_unit='month',
                                            account_id='cash', category_id='office', amount='10.00')['schedule']
    else:
        offset = alpha.accounts.create('Accumulated depreciation', 'Other', classification='Asset')['id']
        row = alpha.assets.create(asset_id='asset', **asset(offset))
    with native_browser('chromium') as browser:
        a, b = browser.new_context(), browser.new_context(viewport={'width': 390, 'height': 844})
        first, second = a.new_page(), b.new_page()
        for page in (first, second):
            page.goto(commercial_server + '/' + resource + '/' + row['id'])
            expect(page.get_by_label(field, exact=True)).to_be_visible()
        second.get_by_label(field, exact=True).fill('Second draft')
        first.get_by_label(field, exact=True).fill('First commit')
        first.get_by_role('button', name='Save changes', exact=True).click()
        expect(first.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
        before = snapshot(database)
        with second.expect_response(lambda response: response.request.method == 'PATCH') as stale:
            second.get_by_role('button', name='Save changes', exact=True).click()
        assert stale.value.status == 409 and stale.value.json()['error']['code'] == 'stale'
        assert stale.value.request.post_data_json['baseline'] == row['baseline']
        assert snapshot(database) == before
        expect(second.get_by_label(field, exact=True)).to_have_value('Second draft')
        second.get_by_role('button', name='Review latest record', exact=True).click()
        second.get_by_role('button', name='Keep draft', exact=True).click()
        expect(second.get_by_label(field, exact=True)).to_have_value('Second draft')
        second.get_by_role('button', name='Review latest record', exact=True).click()
        second.get_by_role('button', name='Discard draft and reload', exact=True).click()
        expect(second.get_by_label(field, exact=True)).to_have_value('First commit')
        second.get_by_label(field, exact=True).fill('Reviewed draft')
        second.get_by_role('button', name='Save changes', exact=True).click()
        expect(second.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
