"""Shared browser journey guards fail before unsafe fixture work and hide payloads."""

import traceback
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tests.postgres import browser_journey as journey_support

ORIGIN = 'http://127.0.0.1:8000'


def test_missing_document_store_stops_before_any_browser_create():
    context = Mock()
    context.request.get.side_effect = [
        SimpleNamespace(status=200, json=lambda: {'entity': {'id': 'alpha-synthetic'}, 'trusted_development': True,
            'capabilities': ['bookkeeping:read', 'bookkeeping:write'], 'csrf_token': 'csrf'}),
        SimpleNamespace(status=503),
    ]
    with pytest.raises(journey_support.JourneyError) as result:
        journey_support.journey(context, Mock(), origin=ORIGIN, entity='alpha-synthetic', marker='synthetic')
    assert result.value.operation == 'preflight'
    context.new_page.assert_not_called()
    assert [call[0] for call in context.request.method_calls] == ['get', 'get']


@pytest.mark.parametrize('session', [
    {'entity': {'id': 'live'}, 'trusted_development': True, 'capabilities': ['bookkeeping:read', 'bookkeeping:write']},
    {'entity': {'id': 'alpha-synthetic'}, 'trusted_development': False, 'capabilities': ['bookkeeping:read', 'bookkeeping:write']},
    {'entity': {'id': 'alpha-synthetic'}, 'trusted_development': True, 'capabilities': ['bookkeeping:read']},
])
def test_unexpected_identity_stops_before_provider_inventory(session):
    context = Mock()
    context.request.get.return_value = SimpleNamespace(status=200, json=lambda: session)
    with pytest.raises(journey_support.JourneyError, match='synthetic context'):
        journey_support.preflight(context, 'alpha-synthetic', ORIGIN)
    assert context.request.get.call_count == 1


def test_nested_operation_preserves_label_without_exception_payload():
    with pytest.raises(journey_support.JourneyError) as result:
        with journey_support.operation('receipt_attach'), journey_support.operation('receipt_download'):
            raise RuntimeError('secret URL, private receipt and raw response')
    assert result.value.operation == 'receipt_download'
    assert 'secret' not in str(result.value)
    assert result.value.__suppress_context__
    assert 'secret URL' not in ''.join(traceback.format_exception(result.value))
    assert journey_support.JourneyError('ignored', operation='private payload').operation is None


def test_preflight_returns_only_selected_scope_and_csrf_headers():
    context = Mock()
    context.request.get.side_effect = [
        SimpleNamespace(status=200, json=lambda: {'entity': {'id': 'alpha-synthetic'}, 'trusted_development': True,
            'capabilities': ['bookkeeping:read', 'bookkeeping:write'], 'csrf_token': 'csrf'}),
        SimpleNamespace(status=200),
    ]
    assert journey_support.preflight(context, 'alpha-synthetic', ORIGIN) == (
        '/api/v1/entities/alpha-synthetic/', {'Origin': ORIGIN, 'X-CSRF-Token': 'csrf'})


def test_failure_status_alone_does_not_establish_baseline_contract():
    response = SimpleNamespace(status=409, json=lambda: {'error': {'code': 'duplicate'}})
    with pytest.raises(journey_support.JourneyError, match='wrong failure contract'):
        journey_support.rejected(response, 409, 'stale')


def test_reference_create_rejects_failed_response_before_accepting_success_ui(monkeypatch):
    page = MagicMock()
    page.expect_response.return_value.__enter__.return_value.value = SimpleNamespace(status=422)
    success = Mock()
    monkeypatch.setattr('playwright.sync_api.expect', success)
    with pytest.raises(journey_support.JourneyError, match='reference_accounts_submit') as failure:
        with journey_support.operation('reference_setup'):
            journey_support.create_reference(page, 'accounts', 'Synthetic account')
    assert failure.value.operation == 'reference_accounts_submit'
    success.assert_not_called()
