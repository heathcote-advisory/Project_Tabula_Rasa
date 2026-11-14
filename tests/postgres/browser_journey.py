"""Shared browser/HTTP/PostgreSQL journey for disposable synthetic test fixtures."""

from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path


ENGINES = ('chromium', 'firefox', 'webkit')
WIDTHS = (1440, 390)
RECEIPT_TIMEOUT_MS = 30_000
OPERATIONS = frozenset({
    'preflight', 'reference_setup', 'expense_create',
    'expense_edit', 'stale_edit', 'stale_delete', 'missing_baseline', 'reload',
    'receipt_open', 'receipt_attach', 'receipt_download', 'receipt_bytes',
    'receipt_unlink', 'receipt_relink', 'receipt_final_unlink', 'income_create',
    'transfer_create', 'journal_create', 'journal_edit', 'ledger', 'reports',
    'transaction_delete', 'journal_delete', 'page_close',
} | {
    f'reference_{resource}_{step}'
    for resource in ('accounts', 'categories', 'payees', 'tags')
    for step in ('prepare', 'click', 'response', 'confirm')
})


class JourneyError(Exception):
    """Bounded test diagnostics; never provider responses or draft values."""

    def __init__(self, message, *, operation=None):
        super().__init__(message)
        self.operation = operation if operation in OPERATIONS else None


@contextmanager
def operation(name):
    """Label a gate step and suppress browser/provider exception details."""
    if name not in OPERATIONS:
        raise JourneyError('Unknown browser journey operation.')
    failed_operation = name
    try:
        yield
    except Exception as error:
        if isinstance(error, JourneyError) and error.operation is not None:
            failed_operation = error.operation
    else:
        return
    raise JourneyError(f'Browser journey operation failed at {failed_operation}. Inspect the disposable fixture.',
                     operation=failed_operation) from None


def checked(response, status=200):
    if response.status != status:
        raise JourneyError('A required browser journey operation failed; inspect the disposable fixture.')
    return response


def rejected(response, status, code):
    if checked(response, status).json().get('error', {}).get('code') != code:
        raise JourneyError('The protected mutation returned the wrong failure contract.')


def preflight(context, expected_entity, origin):
    session = checked(context.request.get('/api/v1/session')).json()
    if (session.get('entity', {}).get('id') != expected_entity or not session.get('trusted_development')
            or not {'bookkeeping:read', 'bookkeeping:write'}.issubset(session.get('capabilities', []))):
        raise JourneyError('The gate requires the fixed, writable synthetic context.')
    prefix = '/api/v1/entities/' + expected_entity + '/'
    if context.request.get(prefix + 'documents').status != 200:
        raise JourneyError('Essential receipt storage is unavailable. Configure the synthetic fixture DocumentStore.')
    return prefix, {'Origin': origin, 'X-CSRF-Token': session['csrf_token']}


def create_reference(page, resource, name, *, kind=None):
    from playwright.sync_api import expect

    with operation(f'reference_{resource}_prepare'):
        page.goto('/' + resource + '/new')
        page.get_by_label('Account name' if resource == 'accounts' else 'Category name' if resource == 'categories' else 'Name', exact=True).fill(name)
        if kind:
            page.get_by_label('Kind', exact=True).select_option(kind)
    with operation(f'reference_{resource}_response'):
        noun = 'category' if resource == 'categories' else resource[:-1]
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/' + resource)) as created:
            with operation(f'reference_{resource}_click'):
                page.get_by_role('button', name='Create ' + noun, exact=True).click()
        row = checked(created.value, 201).json()
    with operation(f'reference_{resource}_confirm'):
        # A response event precedes the app's form reset and dirty-guard cleanup.
        expect(page.get_by_role('status').filter(has_text='Record created.')).to_be_visible()
        return row['id']


def create_transaction(page, marker, kind, amount, account, category='', *, destination='', payee='', tag=''):
    page.goto('/transactions/new')
    page.get_by_label('Type', exact=True).select_option(kind)
    page.get_by_label('Date', exact=True).fill('2026-10-01')
    page.get_by_label('Amount', exact=True).fill(amount)
    page.get_by_label('To account' if kind == 'Income' else 'From account', exact=True).select_option(account)
    if kind == 'Transfer':
        page.get_by_label('To account', exact=True).select_option(destination)
    else:
        page.get_by_label('Category', exact=True).select_option(category)
    if payee:
        page.get_by_label('Payee / customer', exact=True).select_option(payee)
    if tag:
        page.get_by_label('Tags', exact=True).select_option(tag)
    page.get_by_label('Description / memo', exact=True).fill(marker + ' ' + kind)
    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/transactions')) as saved:
        page.get_by_role('button', name='Save transaction', exact=True).click()
    return checked(saved.value, 201).json()


def open_transaction(page, row):
    page.goto('/transactions')
    page.get_by_role('button', name=f"Edit {row['txn_date']} · {row['description']} · {row['amount']}", exact=True).click()
    from playwright.sync_api import expect
    expect(page.get_by_label('Description / memo', exact=True)).to_have_value(row['description'])


def receipt_workflow(page, transaction_id, marker):
    """Attach, verify and retain synthetic receipt bytes through the real UI."""
    from playwright.sync_api import expect

    with operation('receipt_open'):
        page.goto('/transactions/' + transaction_id + '/receipts')
        content = b'%PDF-1.4\n% Tabula Rasa synthetic technical-alpha receipt\n%%EOF\n'
    with operation('receipt_attach'):
        page.get_by_label('Receipt files').set_input_files({'name': marker + '.pdf', 'mimeType': 'application/pdf', 'buffer': content})
        page.get_by_role('button', name='Attach selected receipts', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='1 receipt attached.')).to_be_visible(timeout=RECEIPT_TIMEOUT_MS)
    with operation('receipt_download'):
        with page.expect_download(timeout=RECEIPT_TIMEOUT_MS) as download:
            page.get_by_role('link', name='Download ' + marker + '.pdf', exact=True).click()
    with operation('receipt_bytes'):
        if Path(download.value.path()).read_bytes() != content:
            raise JourneyError('Receipt byte verification failed.')
    with operation('receipt_unlink'):
        page.get_by_role('button', name='Remove ' + marker + '.pdf', exact=True).click()
        page.get_by_role('button', name='Confirm remove', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Receipt reference removed. File retained.')).to_be_visible(timeout=RECEIPT_TIMEOUT_MS)
    with operation('receipt_relink'):
        page.get_by_role('button', name='Link ' + marker + '.pdf', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Receipt linked.')).to_be_visible(timeout=RECEIPT_TIMEOUT_MS)
    with operation('receipt_final_unlink'):
        page.get_by_role('button', name='Remove ' + marker + '.pdf', exact=True).click()
        page.get_by_role('button', name='Confirm remove', exact=True).click()
        expect(page.get_by_role('button', name='Link ' + marker + '.pdf', exact=True)).to_be_visible(timeout=RECEIPT_TIMEOUT_MS)


def delete_journal(page, *, origin, prefix, label):
    """Confirm the journal deletion response before ending the browser step."""
    from playwright.sync_api import expect

    with operation('journal_delete'):
        page.goto('/journals')
        page.get_by_role('button', name=label, exact=True).click()
        # Reference choices expand the editor and move its actions when ready.
        expect(page.get_by_label('Description / memo', exact=True)).to_be_visible()
        page.get_by_role('button', name='Delete', exact=True).click()
        with page.expect_response(lambda response: response.request.method == 'DELETE' and response.url.startswith(origin + prefix + 'journals/')) as deleted:
            page.get_by_role('button', name='Confirm delete', exact=True).click()
        checked(deleted.value)
        expect(page.get_by_role('status').filter(has_text='Record deleted.')).to_be_visible()


def journey(context, peer, *, origin, entity, marker):
    """Supported core path; accounting/validation remain owned by services."""
    from playwright.sync_api import expect

    with operation('preflight'):
        prefix, headers = preflight(context, entity, origin)
        before = checked(context.request.get(prefix + 'reports/summary')).json()
        page, other = context.new_page(), peer.new_page()
    with operation('reference_setup'):
        account = create_reference(page, 'accounts', marker + ' Bank')
        destination = create_reference(page, 'accounts', marker + ' Destination')
        expense_category = create_reference(page, 'categories', marker + ' Expense')
        income_category = create_reference(page, 'categories', marker + ' Income', kind='Income')
        payee = create_reference(page, 'payees', marker + ' Vendor')
        tag = create_reference(page, 'tags', marker + ' Tag')
    with operation('expense_create'):
        expense = create_transaction(page, marker, 'Expense', '10.25', account, expense_category, payee=payee, tag=tag)
    with operation('expense_edit'):
        open_transaction(page, expense)
        open_transaction(other, expense)
        page.get_by_label('Amount', exact=True).fill('11.25')
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        current = checked(saved.value).json()
    with operation('stale_edit'):
        other.get_by_label('Description / memo', exact=True).fill(marker + ' stale draft')
        with other.expect_response(lambda response: response.request.method == 'PATCH') as stale:
            other.get_by_role('button', name='Save changes', exact=True).click()
        rejected(stale.value, 409, 'stale')
        expect(other.get_by_role('alert')).to_contain_text('Your draft is preserved')
        expect(other.get_by_label('Description / memo', exact=True)).to_have_value(marker + ' stale draft')
    with operation('stale_delete'):
        other.get_by_role('button', name='Delete', exact=True).click()
        with other.expect_response(lambda response: response.request.method == 'DELETE') as stale_delete:
            other.get_by_role('button', name='Confirm delete', exact=True).click()
        rejected(stale_delete.value, 409, 'stale')
    with operation('missing_baseline'):
        path = prefix + 'transactions/' + expense['id']
        rejected(context.request.patch(path, headers=headers, data={'changes': saved.value.request.post_data_json['changes']}), 422, 'baseline_required')
        rejected(context.request.delete(path, headers=headers, data={}), 422, 'baseline_required')
    with operation('reload'):
        other.get_by_role('button', name='Review latest record', exact=True).click()
        other.get_by_role('button', name='Discard draft and reload', exact=True).click()
        expect(other.get_by_label('Amount', exact=True)).to_have_value('11.25')
        expect(other.get_by_label('Description / memo', exact=True)).to_have_value(current['description'])
    receipt_workflow(page, expense['id'], marker)
    with operation('income_create'):
        income = create_transaction(page, marker, 'Income', '30.00', account, income_category)
    with operation('transfer_create'):
        transfer = create_transaction(page, marker, 'Transfer', '3.00', account, destination=destination)
    with operation('journal_create'):
        page.goto('/journals/new')
        page.get_by_label('Date', exact=True).fill('2026-10-01')
        page.get_by_label('Description / memo', exact=True).fill(marker + ' Journal')
        page.get_by_label('Account or category', exact=True).nth(0).select_option('category')
        page.get_by_label('Target', exact=True).nth(0).select_option(expense_category)
        page.get_by_label('Target', exact=True).nth(1).select_option(account)
        page.get_by_label('Amount', exact=True).nth(0).fill('2.00')
        page.get_by_label('Amount', exact=True).nth(1).fill('1.00')
        expect(page.get_by_role('button', name='Save journal', exact=True)).to_be_disabled()
        page.get_by_label('Amount', exact=True).nth(1).fill('2.00')
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/journals')) as journal:
            page.get_by_role('button', name='Save journal', exact=True).click()
        checked(journal.value, 201)
    with operation('journal_edit'):
        page.get_by_role('button', name='Back to records', exact=True).click()
        page.get_by_role('button', name='Edit 2026-10-01 · ' + marker + ' Journal', exact=True).click()
        page.get_by_label('Description / memo', exact=True).fill(marker + ' Reviewed journal')
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
    with operation('ledger'):
        page.goto('/transactions')
        page.get_by_label('Search transactions', exact=True).fill(marker)
        expect(page.get_by_role('status').filter(has_text='4 matching entries')).to_be_visible()
        expect(page.get_by_role('rowheader', name='↳ Posting', exact=True)).to_have_count(2)
        if page.evaluate('document.documentElement.scrollWidth > window.innerWidth'):
            raise JourneyError('Core ledger overflows the viewport.')
    with operation('reports'):
        page.goto('/reports')
        expect(page.get_by_role('heading', name='Balance sheet', exact=True)).to_be_visible()
        after = checked(context.request.get(prefix + 'reports/summary')).json()
        for field, delta in (('income', '30.00'), ('expenses', '13.25'), ('net', '16.75')):
            if Decimal(after['profit_loss'][field]) - Decimal(before['profit_loss'][field]) != Decimal(delta):
                raise JourneyError('Core report accounting verification failed; inspect the synthetic run.')
        metrics = page.locator('dl.metrics').first.get_by_role('definition')
        for index, field in enumerate(('income', 'expenses', 'net')):
            expect(metrics.nth(index)).to_have_text(after['profit_loss'][field])
        if after['balance_sheet']['difference'] != '0.00':
            raise JourneyError('The accounting equation failed.')
        expect(page.get_by_role('status').filter(has_text='Assets = Liabilities + Equity.')).to_be_visible()
    with operation('transaction_delete'):
        # Only this run's known records are removed through freshly loaded forms.
        # A failed/uncertain operation stops; no broad cleanup or byte rollback.
        for row in (current, income, transfer):
            open_transaction(page, row)
            page.get_by_role('button', name='Delete', exact=True).click()
            page.get_by_role('button', name='Confirm delete', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='Record deleted.')).to_be_visible()
    delete_journal(page, origin=origin, prefix=prefix, label='Edit 2026-10-01 · ' + marker + ' Reviewed journal')
    with operation('page_close'):
        page.close()
        other.close()
