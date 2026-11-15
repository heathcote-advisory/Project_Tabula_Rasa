"""Thin explicit commands for secondary workflows; GET never schedules jobs."""

from datetime import date
from contextlib import contextmanager

from fastapi import APIRouter, Request

from tabula_rasa.errors import StorageValidationError
from tabula_rasa.mixed_use import MixedUseAllocationError
from tabula_rasa.application.receipt_context import mixed_use_receipt_service
from ..baselines import required_baseline
from ..errors import ERROR_RESPONSES
from ..models.mutations import MutationResult, ProtectedMutation
from ..models.documents import DocumentList, document_response
from ..models.secondary import (
    Allocation, AllocationRequest, Asset, AssetCreate, AssetDetail, AssetList, AssetOverride,
    AssetPost, AssetPostResult, AssetTypes, AssetUpdate, MixedUse, MixedUseCreate, MixedUseList,
    MixedUseUpdate, MixedUseDocumentLink, Recurring, RecurringCreate, RecurringDetail, RecurringList, RecurringUpdate,
    ExpenseRecurringConversion,
    ReconcileRequest, ReconcileResult, asset_detail_response, asset_response, mixed_response,
    occurrence_response, recurring_response,
)
from .services import Context, service_operation

router = APIRouter(prefix='/api/v1/entities/{entity_id}', tags=['Secondary bookkeeping'], responses=ERROR_RESPONSES)


@router.get('/mixed-use', response_model=MixedUseList, operation_id='list_mixed_use')
def list_mixed_use(entity_id: str, request: Request, context: Context):
    with service_operation(request, context, 'mixed_use') as service:
        return MixedUseList(items=[mixed_response(row) for row in service.sources(fresh=True)])


@router.post('/mixed-use/allocation', response_model=Allocation, operation_id='preview_allocation')
def preview_allocation(entity_id: str, body: AllocationRequest, request: Request, context: Context):
    with service_operation(request, context, 'mixed_use') as service:
        try:
            value = service.allocation(body.total_expense, body.business_use_percentage)
        except MixedUseAllocationError:
            raise StorageValidationError('Invalid allocation inputs') from None
        return Allocation(total_expense=value.total_expense, business_use_percentage=value.business_use_percentage,
                          allocated_amount=value.allocated_amount)


@router.post('/mixed-use', response_model=MixedUse, status_code=201, operation_id='create_mixed_use')
def create_mixed_use(entity_id: str, body: MixedUseCreate, request: Request, context: Context):
    with service_operation(request, context, 'mixed_use') as service:
        return mixed_response(service.create(**body.model_dump()))


@router.get('/mixed-use/{record_id}', response_model=MixedUse, operation_id='get_mixed_use')
def get_mixed_use(entity_id: str, record_id: str, request: Request, context: Context):
    with service_operation(request, context, 'mixed_use') as service:
        return mixed_response(service.get(record_id, fresh=True))


@router.patch('/mixed-use/{record_id}', response_model=MixedUse, operation_id='update_mixed_use')
def update_mixed_use(entity_id: str, record_id: str, body: MixedUseUpdate, request: Request, context: Context):
    expected = required_baseline(body.baseline)
    with service_operation(request, context, 'mixed_use') as service:
        return mixed_response(service.update(record_id, expected_source=expected, **body.changes.model_dump(exclude_unset=True)))


@router.post('/mixed-use/{record_id}/copy-draft', response_model=MixedUseCreate, operation_id='copy_mixed_use_draft')
def copy_mixed_use_draft(entity_id: str, record_id: str, request: Request, context: Context):
    with service_operation(request, context, 'mixed_use') as service:
        return MixedUseCreate(**service.copy_draft(record_id))


@router.delete('/mixed-use/{record_id}', response_model=MutationResult, operation_id='delete_mixed_use')
def delete_mixed_use(entity_id: str, record_id: str, body: ProtectedMutation, request: Request, context: Context):
    with service_operation(request, context, 'mixed_use') as service:
        service.delete(record_id, expected_source=required_baseline(body.baseline))
        return MutationResult(status='fully_applied')


@contextmanager
def mixed_receipts(request, context, record_id, expected):
    with service_operation(request, context, 'mixed_use') as sources, \
            service_operation(request, context, 'receipts') as receipts:
        source = sources.get(record_id, fresh=True)
        yield source, mixed_use_receipt_service(receipts, sources, record_id, expected)


@router.get('/mixed-use/{record_id}/receipts', response_model=DocumentList, operation_id='mixed_use_documents')
def mixed_use_documents(entity_id: str, record_id: str, request: Request, context: Context):
    with mixed_receipts(request, context, record_id, {}) as (source, receipts):
        return DocumentList(items=[document_response(doc) for doc in
                                  receipts.references(source['linked_transaction_id'], fresh=True)])


@router.post('/mixed-use/{record_id}/receipts', response_model=MixedUse, operation_id='link_mixed_use_document')
def link_mixed_use_document(entity_id: str, record_id: str, body: MixedUseDocumentLink, request: Request, context: Context):
    expected = required_baseline(body.baseline)
    with mixed_receipts(request, context, record_id, expected) as (source, receipts):
        return mixed_response(receipts.link_document(source['linked_transaction_id'], body.document_id))


@router.delete('/mixed-use/{record_id}/receipts/{document_id}', response_model=MixedUse, operation_id='unlink_mixed_use_document')
def unlink_mixed_use_document(entity_id: str, record_id: str, document_id: str, body: ProtectedMutation,
                             request: Request, context: Context):
    expected = required_baseline(body.baseline)
    with mixed_receipts(request, context, record_id, expected) as (source, receipts):
        return mixed_response(receipts.unlink_document(source['linked_transaction_id'], document_id))


@router.get('/recurring', response_model=RecurringList, operation_id='list_recurring')
def list_recurring(entity_id: str, request: Request, context: Context):
    with service_operation(request, context, 'recurring') as service:
        return RecurringList(items=[recurring_response(row) for row in service.list(fresh=True)])


@router.post('/recurring', response_model=Recurring, status_code=201, operation_id='create_recurring')
def create_recurring(entity_id: str, body: RecurringCreate, request: Request, context: Context):
    with service_operation(request, context, 'recurring') as service:
        values = body.model_dump()
        mixed = values.pop('source_kind') == 'mixed_use'
        if body.source_kind not in {'transaction', 'mixed_use'}:
            raise StorageValidationError('Invalid source kind')
        result = (service.create_mixed_expense if mixed else service.create_expense)(**values)
        # Confirmed initial operation already returns its committed schedule.
        # Derived next/latest dates are read-only decorations on subsequent GET.
        return recurring_response(dict(result['schedule'], next_due_date='',
                                       latest_current_occurrence_date=result['transaction']['recurring_scheduled_date'],
                                       attention_message=''))


@router.post('/recurring/reconcile', response_model=ReconcileResult, operation_id='reconcile_recurring')
def reconcile_recurring(entity_id: str, body: ReconcileRequest, request: Request, context: Context):
    with service_operation(request, context, 'recurring') as service:
        result = service.reconcile(as_of=body.through_date)
        return ReconcileResult(generated_count=len(result['generated']), attention_count=len(result['attention']),
                               queued_count=result.get('queued_count', 0), execution_mode=result.get('execution_mode', 'explicit'))


@router.post('/recurring/from-transaction/{record_id}', response_model=RecurringDetail,
             status_code=201, operation_id='convert_expense_to_recurring')
def convert_expense_to_recurring(entity_id: str, record_id: str, body: ExpenseRecurringConversion,
                                request: Request, context: Context):
    expected = required_baseline(body.baseline)
    with service_operation(request, context, 'recurring') as service:
        values = body.model_dump(exclude={'baseline', 'changes'})
        result = service.from_transaction(record_id, expected_transaction=expected,
                                          **values, **body.changes.model_dump())
        schedule = recurring_response(dict(result['schedule'], next_due_date='',
                                           latest_current_occurrence_date=result['transaction']['recurring_scheduled_date'],
                                           attention_message=''))
        return RecurringDetail(schedule=schedule, occurrences=[occurrence_response(result['transaction'])],
                               mixed_sources=[])


@router.get('/recurring/{record_id}', response_model=RecurringDetail, operation_id='get_recurring')
def get_recurring(entity_id: str, record_id: str, request: Request, context: Context):
    with service_operation(request, context, 'recurring') as service:
        detail = service.detail(record_id, fresh=True)
        return RecurringDetail(can_delete=detail['can_delete'], schedule=recurring_response(detail['schedule']),
                               occurrences=[occurrence_response(row) for row in detail['transactions']],
                               mixed_sources=[mixed_response(row) for row in detail['mixed_sources']])


def saved_schedule(row):
    return recurring_response(dict(row, next_due_date='', latest_current_occurrence_date='', attention_message=''))


@router.delete('/recurring/{record_id}', response_model=MutationResult, operation_id='delete_recurring')
def delete_recurring(entity_id: str, record_id: str, body: ProtectedMutation, request: Request, context: Context):
    with service_operation(request, context, 'recurring') as service:
        service.delete(record_id, expected_schedule=required_baseline(body.baseline))
        return MutationResult(status='fully_applied')


@router.patch('/recurring/{record_id}', response_model=Recurring, operation_id='update_recurring')
def update_recurring(entity_id: str, record_id: str, body: RecurringUpdate, request: Request, context: Context):
    with service_operation(request, context, 'recurring') as service:
        return saved_schedule(service.update(record_id, expected_schedule=required_baseline(body.baseline),
                                             **body.changes.model_dump(exclude_unset=True)))


@router.post('/recurring/{record_id}/{transition}', response_model=Recurring, operation_id='transition_recurring')
def transition_recurring(entity_id: str, record_id: str, transition: str, body: ProtectedMutation, request: Request, context: Context):
    with service_operation(request, context, 'recurring') as service:
        commands = {'pause': service.pause, 'resume': service.resume, 'end': service.end}
        if transition not in commands:
            raise StorageValidationError('Invalid schedule transition')
        return saved_schedule(commands[transition](record_id, expected_schedule=required_baseline(body.baseline)))


@router.get('/assets/types', response_model=AssetTypes, operation_id='asset_types')
def asset_types(entity_id: str, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        return AssetTypes(items=list(service.types))


@router.get('/assets', response_model=AssetList, operation_id='list_assets')
def list_assets(entity_id: str, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        return AssetList(items=[asset_response(row) for row in service.list(fresh=True)])


@router.post('/assets', response_model=Asset, status_code=201, operation_id='create_asset')
def create_asset(entity_id: str, body: AssetCreate, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        return asset_response(service.create(**body.model_dump()))


@router.get('/assets/{record_id}', response_model=AssetDetail, operation_id='get_asset')
def get_asset(entity_id: str, record_id: str, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        return asset_detail_response(service.detail(record_id, fresh=True))


@router.patch('/assets/{record_id}', response_model=Asset, operation_id='update_asset')
def update_asset(entity_id: str, record_id: str, body: AssetUpdate, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        return asset_response(service.update(record_id, expected=required_baseline(body.baseline),
                                             **body.changes.model_dump(exclude_unset=True)))


@router.delete('/assets/{record_id}', response_model=MutationResult, operation_id='delete_asset')
def delete_asset(entity_id: str, record_id: str, body: ProtectedMutation, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        service.delete(record_id, expected=required_baseline(body.baseline))
        return MutationResult(status='fully_applied')


@router.post('/assets/{record_id}/post', response_model=AssetPostResult, operation_id='post_depreciation')
def post_depreciation(entity_id: str, record_id: str, body: AssetPost, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        rows = service.post_due(record_id, body.through_date, expected=required_baseline(body.baseline))
        return AssetPostResult(posted_count=len(rows), entry_ids=[row['id'] for row in rows])


@router.put('/assets/{record_id}/overrides/{sequence}', response_model=Asset, operation_id='override_depreciation')
def override_depreciation(entity_id: str, record_id: str, sequence: int, body: AssetOverride, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        from tabula_rasa.ledger_validation import iso_date
        as_of = min(date.fromisoformat(iso_date(body.as_of_date)), date.today())
        return asset_response(service.override(record_id, sequence, body.amount, body.reason,
                                               as_of_date=as_of,
                                               expected=required_baseline(body.baseline)))


@router.delete('/assets/{record_id}/overrides/{sequence}', response_model=Asset, operation_id='remove_depreciation_override')
def remove_depreciation_override(entity_id: str, record_id: str, sequence: int, body: ProtectedMutation, request: Request, context: Context):
    with service_operation(request, context, 'assets') as service:
        return asset_response(service.remove_override(record_id, sequence, expected=required_baseline(body.baseline)))
