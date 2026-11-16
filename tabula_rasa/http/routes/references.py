from fastapi import APIRouter, Request

from ..baselines import merge_baselines, required_baseline
from ..errors import ERROR_RESPONSES
from ..models.mutations import MutationResult, ProtectedMutation
from ..models.references import (
    Category, CategoryCreate, CategoryList, CategoryUpdate, ManagedCreate, ManagedList, ManagedMerge,
    ManagedReference, ManagedUpdate, category_response, managed_response,
)
from .services import Context, service_operation

categories = APIRouter(prefix='/api/v1/entities/{entity_id}/categories', tags=['Categories'], responses=ERROR_RESPONSES)


@categories.post('', response_model=Category, status_code=201, operation_id='create_category')
def create_category(entity_id: str, body: CategoryCreate, request: Request, context: Context):
    with service_operation(request, context, 'categories') as service:
        return category_response(service.create(**body.model_dump()))


@categories.get('', response_model=CategoryList, operation_id='list_categories')
def list_categories(entity_id: str, request: Request, context: Context):
    with service_operation(request, context, 'categories') as service:
        return CategoryList(items=[category_response(row) for row in service.list()])


@categories.patch('/{record_id}', response_model=Category, operation_id='update_category')
def update_category(entity_id: str, record_id: str, body: CategoryUpdate, request: Request, context: Context):
    expected = required_baseline(body.baseline)
    with service_operation(request, context, 'categories') as service:
        return category_response(service.update(record_id, expected=expected, **body.changes.model_dump(exclude_unset=True)))


@categories.delete('/{record_id}', response_model=MutationResult, operation_id='delete_category')
def delete_category(entity_id: str, record_id: str, body: ProtectedMutation, request: Request, context: Context):
    expected = required_baseline(body.baseline)
    with service_operation(request, context, 'categories') as service:
        service.delete(record_id, expected=expected)
        return MutationResult(status='fully_applied')


def managed_router(resource, *, allow_merge=True):
    # Payees and tags already share one managed-reference command contract.
    router = APIRouter(prefix=f'/api/v1/entities/{{entity_id}}/{resource}', tags=[resource.title()], responses=ERROR_RESPONSES)

    @router.post('', response_model=ManagedReference, status_code=201, operation_id=f'create_{resource[:-1]}')
    def create_reference(entity_id: str, body: ManagedCreate, request: Request, context: Context):
        with service_operation(request, context, resource) as service:
            return managed_response(service.create(**body.model_dump()))

    @router.get('', response_model=ManagedList, operation_id=f'list_{resource}')
    def list_references(entity_id: str, request: Request, context: Context):
        with service_operation(request, context, resource) as service:
            return ManagedList(items=[managed_response(row) for row in service.list(active_only=False)])

    @router.patch('/{record_id}', response_model=ManagedReference, operation_id=f'update_{resource[:-1]}')
    def update_reference(entity_id: str, record_id: str, body: ManagedUpdate, request: Request, context: Context):
        expected = required_baseline(body.baseline)
        with service_operation(request, context, resource) as service:
            return managed_response(service.update(record_id, expected=expected, **body.changes.model_dump(exclude_unset=True)))

    @router.delete('/{record_id}', response_model=MutationResult, operation_id=f'remove_{resource[:-1]}')
    def remove_reference(entity_id: str, record_id: str, body: ProtectedMutation, request: Request, context: Context):
        expected = required_baseline(body.baseline)
        with service_operation(request, context, resource) as service:
            return MutationResult(**service.remove(record_id, expected=expected))

    if allow_merge:
        @router.post('/{record_id}/merge', response_model=MutationResult, operation_id=f'merge_{resource}')
        def merge_reference(entity_id: str, record_id: str, body: ManagedMerge, request: Request, context: Context):
            expected = merge_baselines(record_id, body.source_ids, body.baselines)
            with service_operation(request, context, resource) as service:
                return MutationResult(**service.merge(record_id, body.source_ids, expected=expected))

    return router


payees = managed_router('payees')
tags = managed_router('tags')

vehicles = managed_router('vehicles', allow_merge=False)
