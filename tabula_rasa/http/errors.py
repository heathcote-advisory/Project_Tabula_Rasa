"""Allowlisted errors only: never return/log provider or submitted text."""

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from tabula_rasa.errors import (
    StorageConflictError, StorageError, StorageNotFoundError, StorageValidationError,
)
from .models.common import ErrorCode, ErrorDetail, ErrorEnvelope, FieldError
from tabula_rasa.application.mileage import MileageValidationError
from tabula_rasa.application.documents import DocumentError, DocumentNotFoundError, DocumentValidationError
from tabula_rasa.application.identity import IdentityStorageUnavailable


ERRORS = {
    'beta_pending': (403, 'Invitation-only beta approval is required for this business and named user. Ask your operator to review enrollment.'),
    'beta_paused': (409, 'New beta admissions are paused. Existing authorized access and export remain available. Retry after your operator resumes admissions.'),
    'beta_capacity': (409, 'The approved beta cohort is at capacity. Ask your operator to review enrollment before retrying.'),
    'beta_eligibility': (422, 'Business eligibility requires U.S./USD or Canada/CAD. New beta books require operator approval.'),
    'period_closed': (409, 'This accounting period is closed. Use an authorized reversal in an open month or ask the owner to unlock it.'),
    'reauthentication_required': (403, 'Sign in again to continue. Re-enter any invitation credential after login.'),
    'invitation_invalid': (403, 'This invitation is unavailable for this signed-in user.'),
    'invitation_used': (409, 'This invitation has already been redeemed, declined or revoked. Ask for a new invitation.'),
    'invitation_expired': (410, 'This invitation has expired. Ask for a new invitation.'),
    "validation": (422, "Check the submitted fields."),
    "not_found": (404, "The requested resource was not found."),
    "conflict": (409, "The operation conflicts with the current record."),
    "duplicate": (409, "A matching record already exists."),
    "reference": (422, "A referenced record is unavailable or still in use."),
    "invariant": (422, "The operation violates a bookkeeping rule."),
    "required": (422, "A required value is missing."),
    "baseline_required": (422, "Reload the record before submitting changes."),
    "baseline_invalid": (422, "The loaded record baseline is invalid. Reload the record."),
    "stale": (409, "The record changed since it was loaded. Review the latest record."),
    "concurrent_write": (409, "Another operation changed the record. Review before retrying."),
    "unauthorized": (401, "Sign in to continue."),
    "forbidden": (403, "You do not have access to this operation."),
    "unavailable": (503, "The service is unavailable. Check the record before retrying a save."),
    "unexpected": (500, "The operation could not be completed."),
    "method_not_allowed": (405, "This method is not supported."),
}


class TransportError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def error_response(request, code, *, fields=None):
    if code not in ERRORS:
        code = "unexpected"
    status, message = ERRORS[code]
    request.state.failure_code = code
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=ErrorCode(code), message=message, fields=fields),
        request_id=request.state.request_id,
    )
    return JSONResponse(envelope.model_dump(mode="json", exclude_none=True), status_code=status)


def failure_code(error):
    if isinstance(error, DocumentNotFoundError):
        return 'not_found'
    if isinstance(error, (MileageValidationError, DocumentValidationError)):
        return "validation"
    if isinstance(error, DocumentError):
        return 'unavailable'
    if isinstance(error, TransportError):
        return error.code
    if isinstance(error, StorageNotFoundError):
        return "not_found"
    if isinstance(error, StorageConflictError):
        return error.code if error.code in {"duplicate", "stale", "concurrent_write", 'period_closed'} else "conflict"
    if isinstance(error, StorageValidationError):
        return error.code if error.code in {
            "reference", "invariant", "required", "baseline_required", "baseline_invalid",
        } else "validation"
    if isinstance(error, StorageError):
        return "unavailable"
    # Infrastructure exceptions are translated at the composition boundary.
    return "unexpected"


def mark_postgres_failure(request, *, exhausted):
    request.state.telemetry_provider = "postgresql"
    request.state.provider_status = "exhausted" if exhausted else "unavailable"


def mark_identity_storage_failure(request, error):
    if isinstance(error, IdentityStorageUnavailable):
        mark_postgres_failure(request, exhausted=error.exhausted)


async def handle_error(request, error):
    if isinstance(error, DocumentError):
        request.state.telemetry_provider = "documents"
    elif isinstance(error, StorageError) and not isinstance(error, (StorageValidationError, StorageConflictError, StorageNotFoundError)):
        mark_postgres_failure(request, exhausted=error.code == "database_exhausted")
    return error_response(request, failure_code(error))


async def handle_validation(request, error: RequestValidationError):
    body_field = getattr(request.scope.get('route'), 'body_field', None)
    body_model = getattr(getattr(body_field, 'field_info', None), 'annotation', None)
    body_fields = getattr(body_model, 'model_fields', {})
    if {'baseline', 'baselines'} & body_fields.keys() and any(
        item.get('loc') == ('body',) and item['type'] == 'missing' for item in error.errors()
    ):
        return error_response(request, 'baseline_required')
    baseline_errors = [item for item in error.errors()
                       if len(item.get('loc', ())) >= 2 and item['loc'][0] == 'body'
                       and item['loc'][1] in {'baseline', 'baselines'}]
    if baseline_errors:
        missing = any(item['type'] == 'missing' or item.get('input') is None for item in baseline_errors)
        return error_response(request, 'baseline_required' if missing else 'baseline_invalid')
    # Locations can contain client-supplied keys (extra fields): allow only
    # declared transport field names. Never echo input, context, or raw messages.
    allowed = {
        "id", "name", "type", "opening_balance", "classification", "parent_id", "active_only",
        "active", "kind", "baseline", "baselines", "source_ids", "txn_date", "amount",
        "account_id", "to_account_id", "category_id", "payee_id", "tag_ids", "description",
        "reference", "postings", "target_type", "target_id", "side", "trip_date", "miles",
        "vehicle", "start_location", "destination", "business_purpose", "notes",
        "new_payee_name", "new_tag_name", "filename", "document_id", "round_trip", "return_miles", "start", "end",
        "total_expense", "business_use_percentage", "allocation_rationale", "source_id", "linked_transaction_id",
        "schedule_id", "source_kind", "recurrence_unit", "recurrence_interval", "start_date", "end_date",
        "asset_id", "asset_type", "acquisition_date", "depreciation_start_date", "cost_basis", "salvage_value",
        "useful_life_quarters", "depreciation_expense_account_id", "accumulated_depreciation_account_id",
        "source_transaction_id", "status", "retired_date", "through_date", "as_of_date", "sequence", "reason",
    }
    fields = [
        FieldError(field=str(item["loc"][-1]), code="invalid", message="Check this value.")
        for item in error.errors()
        if item.get("loc") and item["loc"][-1] in allowed
    ]
    return error_response(request, "validation", fields=fields or None)


async def handle_http_error(request, error: HTTPException):
    code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized", 403: "forbidden"}.get(
        error.status_code, "unexpected",
    )
    return error_response(request, code)


ERROR_RESPONSES = {
    status: {"model": ErrorEnvelope, "description": description}
    for status, description in [
        (401, "Identity required"), (403, "Operation denied"), (404, "Resource not found"),
        (409, "Duplicate or concurrent change"), (422, "Transport or domain validation"),
        (503, "Dependency unavailable"), (500, "Unexpected failure"),
    ]
}
