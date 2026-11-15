"""Native browser vertical slices with PostgreSQL and a synthetic byte adapter."""

from pathlib import Path
from threading import Event
from unittest.mock import patch

from playwright.sync_api import expect
import pytest

from tabula_rasa.application.documents import DocumentError
from tabula_rasa.infrastructure.postgres.documents import PostgresReceiptRepository
from tabula_rasa.http.context import TrustedDevelopmentContext
from tests.postgres.test_browser_baselines import commercial_server as commercial_server
from tests.postgres.test_http_mileage_documents import commercial as commercial, document_store as document_store
from tests.postgres.test_http_mileage_documents import TRIP, HEADERS, PREFIX


@pytest.fixture
def browser_resolver():
    return TrustedDevelopmentContext('alpha', 'csrf', entity_ids=('beta',))


def test_mileage_revisit_waits_for_fresh_detail_and_surfaces_deleted_record(commercial, commercial_server, native_browser):
    _, _, (client, _), _ = commercial
    created = client.post(PREFIX + 'mileage', headers=HEADERS, json=TRIP).json()['items'][0]
    path = '/mileage/' + created['id']
    endpoint = '**/api/v1/entities/alpha/mileage/' + created['id']
    with native_browser('chromium') as browser:
        page = browser.new_page()
        page.goto(commercial_server + path)
        expect(page.get_by_label('Notes', exact=True)).to_have_value(TRIP['notes'])
        page.get_by_role('button', name='Back to records', exact=True).click()
        # History can change before React commits the list and unmounts the editor.
        expect(page.get_by_role('heading', name='Mileage', exact=True)).to_be_visible()
        updated = client.patch(PREFIX + 'mileage/' + created['id'], headers=HEADERS,
                               json={'baseline': created['baseline'], 'changes': {**TRIP, 'notes': 'Other client edit'}})
        assert updated.status_code == 200
        waiting = []
        page.route(endpoint, lambda route: waiting.append(route))
        page.go_back()
        expect(page.get_by_role('heading', name='Mileage trip', exact=True)).to_be_visible()
        expect(page.get_by_label('Notes', exact=True)).to_have_count(0)
        expect(page.get_by_role('status').filter(has_text='Loading')).to_be_visible()
        assert len(waiting) == 1
        waiting.pop().continue_()
        expect(page.get_by_label('Notes', exact=True)).to_have_value('Other client edit')
        page.unroute(endpoint)
        page.get_by_label('Notes', exact=True).fill('Fresh visit draft')
        with page.expect_response(lambda response: response.request.method == 'PATCH') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        assert saved.value.request.post_data_json['baseline'] == updated.json()['baseline']
        expect(page.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
        page.get_by_role('button', name='Back to records', exact=True).click()
        expect(page.get_by_role('heading', name='Mileage', exact=True)).to_be_visible()
        deleted = client.request('DELETE', PREFIX + 'mileage/' + created['id'], headers=HEADERS,
                                json={'baseline': saved.value.json()['baseline']})
        assert deleted.status_code == 200
        page.go_back()
        expect(page.get_by_role('alert')).to_contain_text('The requested data could not be loaded.')
        expect(page.get_by_label('Notes', exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name='Save changes', exact=True)).to_have_count(0)


@pytest.mark.parametrize('width', [1440, 390])
def test_mileage_capture_round_trip_filter_export_detail_and_delete(commercial, commercial_server, width, native_browser):
    with native_browser('chromium') as browser:
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        page.goto(commercial_server + '/mileage')
        page.get_by_role('link', name='Add trip', exact=True).click()
        page.get_by_label('Trip date', exact=True).fill('2026-10-08')
        page.get_by_label('Business miles', exact=True).fill('12.4')
        page.get_by_label('Round trip', exact=True).check()
        expect(page.get_by_label('Return business miles')).to_have_value('12.4')
        page.get_by_label('Return business miles').fill('14.2')
        page.get_by_label('Starting location').fill('Office')
        page.get_by_label('Destination', exact=True).fill('Site')
        page.get_by_label('Business purpose').fill('Inspection')
        page.get_by_label('Notes', exact=True).fill('@SUM(1)')
        page.get_by_label('New tag name (optional)').fill('Inspection')
        page.get_by_role('button', name='Save mileage trip').click()
        expect(page.get_by_role('status').filter(has_text='Mileage saved: 2 trips.')).to_be_visible()
        page.get_by_role('link', name='View mileage log').click()
        page.get_by_label('From', exact=True).fill('2026-10-08')
        page.get_by_label('To', exact=True).fill('2026-10-08')
        page.get_by_label('Filter by tags').select_option(label='Inspection')
        expect(page.get_by_role('status').filter(has_text='Trips: 2 · Business miles: 26.6')).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('button', name='Download filtered mileage CSV').click()
        csv = Path(download.value.path()).read_text(encoding='utf-8')
        assert 'Inspection' in csv and '14.2' in csv and '12.4' in csv
        assert '"\'@SUM(1)"' in csv
        page.get_by_role('button', name='Edit 2026-10-08 · Site · 12.4 miles', exact=True).click()
        page.get_by_label('Notes', exact=True).fill('Reviewed')
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Changes saved.')).to_be_visible()
        page.get_by_role('button', name='Delete', exact=True).click()
        page.get_by_role('button', name='Confirm delete', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Record deleted.')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width', [1440, 390])
def test_receipt_upload_partial_link_retry_download_remove_and_recover(commercial, commercial_server, document_store, width, native_browser):
    with native_browser('chromium') as browser:
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        page.goto(commercial_server + '/transactions')
        page.get_by_role('link', name='Receipts for 2026-10-05 · Expense · 12.50', exact=True).click()
        expect(page.get_by_role('heading', name='No receipts attached')).to_be_visible()
        page.get_by_label('Receipt files').set_input_files({'name': 'receipt.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF synthetic receipt'})
        with patch.object(PostgresReceiptRepository, 'link', side_effect=DocumentError('Synthetic link failure')):
            page.get_by_role('button', name='Attach selected receipts').click()
            expect(page.get_by_role('alert').filter(has_text='1 unresolved.')).to_be_visible()
        assert document_store.upload_calls == 1
        page.get_by_role('button', name='Retry unresolved receipts').click()
        expect(page.get_by_role('status').filter(has_text='1 receipt attached.')).to_be_visible()
        assert document_store.upload_calls == 1
        expect(page.get_by_role('heading', name='No unreferenced receipts')).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('link', name='Download receipt.pdf', exact=True).click()
        assert Path(download.value.path()).read_bytes() == b'%PDF synthetic receipt'
        remove = page.get_by_role('button', name='Remove receipt.pdf', exact=True)
        remove.click()
        dialog = page.get_by_role('alertdialog', name='Remove receipt')
        keep = dialog.get_by_role('button', name='Keep receipt', exact=True)
        confirm = dialog.get_by_role('button', name='Confirm remove', exact=True)
        expect(dialog).to_have_attribute('aria-modal', 'true')
        expect(keep).to_be_focused()
        page.keyboard.press('Shift+Tab')
        expect(confirm).to_be_focused()
        page.keyboard.press('Tab')
        expect(keep).to_be_focused()
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        expect(remove).to_be_focused()
        remove.click()
        expect(page.get_by_role('alertdialog', name='Remove receipt')).to_contain_text('stored file remains')
        with patch.object(PostgresReceiptRepository, 'unlink', side_effect=DocumentError('Synthetic unlink failure')):
            page.get_by_role('button', name='Confirm remove', exact=True).click()
            expect(dialog).to_have_count(0)
            expect(page.get_by_role('alert').filter(has_text='The request could not be completed')).to_be_visible()
        expect(remove).to_be_focused()
        remove.click()
        page.get_by_role('button', name='Confirm remove', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Receipt reference removed. File retained.')).to_be_visible()
        page.get_by_role('button', name='Link receipt.pdf', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Receipt linked.')).to_be_visible()
        assert document_store.delete_calls == 0
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('link', name='Back to transactions').click()
        expect(page.get_by_role('table', name='Business ledger').get_by_label('1 receipt(s)', exact=True)).to_be_visible()


@pytest.mark.parametrize('width', [1440, 390])
def test_receipt_cancel_recovery_distinguishes_confirmed_and_unconfirmed_uploads(commercial, commercial_server, document_store, width, native_browser):
    with native_browser('chromium') as browser:
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        page.goto(commercial_server + '/transactions/expense/receipts')
        expect(page.get_by_role('heading', name='No receipts attached')).to_be_visible()
        files = page.get_by_label('Receipt files')
        files.set_input_files({'name': 'unselected.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF synthetic'})
        page.get_by_role('button', name='Cancel recovery').click()
        notice = page.get_by_role('alert').filter(has_text='Recovery cancelled.')
        expect(notice).to_contain_text('1 file has no confirmed upload')
        expect(notice).to_contain_text('before selecting those files again')
        expect(notice).not_to_contain_text('file remains')
        assert document_store.upload_calls == 0
        files.set_input_files([
            {'name': 'confirmed.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF confirmed'},
            {'name': 'failed.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF failed'},
        ])
        original_upload = document_store.upload

        def upload(profile, upload):
            if upload.original_filename == 'failed.pdf':
                raise DocumentError('Synthetic upload failure before create')
            return original_upload(profile, upload)

        with patch.object(document_store, 'upload', side_effect=upload), \
                patch.object(PostgresReceiptRepository, 'link', side_effect=DocumentError('Synthetic link failure')):
            page.get_by_role('button', name='Attach selected receipts').click()
            expect(page.get_by_role('alert').filter(has_text='2 unresolved.')).to_be_visible()
            partial = page.get_by_role('alert').filter(has_text='2 unresolved.')
            expect(partial).to_contain_text('1 confirmed uploaded file remains in Unreferenced receipts.')
            expect(partial).to_contain_text('1 file has no confirmed upload.')
        assert document_store.upload_calls == 1 and len(document_store.files) == 1
        page.get_by_role('button', name='Cancel recovery').click()
        expect(notice).to_contain_text('1 confirmed uploaded file remains in Unreferenced receipts.')
        expect(notice).to_contain_text('1 file has no confirmed upload')
        expect(page.get_by_role('status').filter(has_text='Recovery cancelled.')).to_have_count(0)
        expect(files).to_be_enabled()
        expect(page.get_by_role('button', name='Link confirmed.pdf')).to_be_visible()
        expect(page.get_by_role('link', name='Download failed.pdf')).to_have_count(0)
        page.get_by_role('button', name='Link confirmed.pdf').click()
        expect(page.get_by_role('status').filter(has_text='Receipt linked.')).to_be_visible()
        assert document_store.upload_calls == 1 and document_store.delete_calls == 0


def test_receipt_upload_progress_uncertain_retry_and_invalid_batch(commercial, commercial_server, document_store, native_browser):
    with native_browser('chromium') as browser:
        release = Event()
        original = document_store.upload
        def blocked_upload(*args):
            assert release.wait(10), 'Synthetic upload was not released.'
            return original(*args)
        try:
            page = browser.new_page(viewport={'width': 320, 'height': 844})
            page.goto(commercial_server + '/transactions/expense/receipts')
            expect(page.get_by_role('heading', name='No receipts attached')).to_be_visible()
            page.get_by_label('Receipt files').set_input_files([
                {'name': 'good.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF synthetic'},
                {'name': 'invalid.txt', 'mimeType': 'text/plain', 'buffer': b'unsupported'},
            ])
            expect(page.get_by_role('alert')).to_contain_text('Check the highlighted fields')
            assert document_store.upload_calls == 0
            page.get_by_label('Receipt files').set_input_files({'name': 'uncertain.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF synthetic'})
            document_store.fail_after_create = True
            with patch.object(document_store, 'upload', side_effect=blocked_upload):
                page.get_by_role('button', name='Attach selected receipts').click()
                expect(page.get_by_role('progressbar', name='Receipt upload progress')).to_be_visible()
                expect(page.get_by_label('Select business')).to_be_disabled()
                expect(page.get_by_label('Receipt files')).to_be_disabled()
                release.set()
                expect(page.get_by_role('alert').filter(has_text='1 unresolved.')).to_be_visible()
            assert document_store.upload_calls == 1 and len(document_store.files) == 1
            partial = page.get_by_role('alert').filter(has_text='1 unresolved.')
            expect(partial).to_contain_text('1 file has no confirmed upload.')
            expect(partial).not_to_contain_text('file remains')
            page.get_by_role('button', name='Retry unresolved receipts').click()
            expect(page.get_by_role('status').filter(has_text='1 receipt attached.')).to_be_visible()
            assert document_store.upload_calls == 1 and len(document_store.files) == 1
            expect(page.get_by_role('heading', name='No unreferenced receipts')).to_be_visible()
            with patch.object(document_store, 'fetch', side_effect=DocumentError('Synthetic download failure')):
                page.get_by_role('link', name='Download uncertain.pdf').click()
                expect(page.get_by_role('alert')).to_contain_text('request could not be completed')
            expect(page.get_by_role('heading', name='Receipts', exact=True)).to_be_visible()
            page.get_by_role('button', name='Remove uncertain.pdf', exact=True).click()
            page.get_by_role('button', name='Keep receipt', exact=True).click()
            expect(page.get_by_role('link', name='Download uncertain.pdf')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            release.set()
