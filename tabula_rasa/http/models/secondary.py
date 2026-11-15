"""Secondary bookkeeping transport contracts; rules remain service-owned."""

from typing import Literal

from pydantic import Field, model_serializer

from tabula_rasa.reference_data import normalize_tag_ids
from .accounts import DecimalString
from .common import DTO
from .mutations import ProtectedMutation


class ExpenseFields(DTO):
    txn_date: str
    account_id: str
    category_id: str
    payee_id: str = ''
    tag_ids: list[str] = Field(default_factory=list)
    description: str = ''
    reference: str = ''
    new_payee_name: str = ''
    new_tag_name: str = ''


class MixedUseChanges(ExpenseFields):
    total_expense: DecimalString
    business_use_percentage: DecimalString
    allocation_rationale: str


class MixedUseCreate(MixedUseChanges):
    source_id: str
    linked_transaction_id: str
    copied_from_source_id: str = ''


class MixedUse(MixedUseChanges):
    id: str
    linked_transaction_id: str
    allocated_amount: DecimalString
    ledger_status: str
    recurring_schedule_id: str
    recurring_scheduled_date: str
    recurring_occurrence_type: str
    baseline: str


class MixedUseList(DTO):
    items: list[MixedUse]


class MixedUseUpdate(ProtectedMutation):
    changes: MixedUseChanges


class MixedUseDocumentLink(ProtectedMutation):
    document_id: str


class AllocationRequest(DTO):
    total_expense: DecimalString
    business_use_percentage: DecimalString


class Allocation(DTO):
    total_expense: DecimalString
    business_use_percentage: DecimalString
    allocated_amount: DecimalString


class RecurringChanges(DTO):
    name: str
    recurrence_unit: str
    recurrence_interval: str = '1'
    end_date: str = ''
    amount: str = '0.00'
    total_expense: str = ''
    business_use_percentage: str = ''
    allocation_rationale: str = ''
    account_id: str
    category_id: str
    payee_id: str = ''
    tag_ids: list[str] = Field(default_factory=list)
    description: str = ''
    reference: str = ''


class RecurringCreate(RecurringChanges):
    schedule_id: str
    start_date: str
    source_kind: str = 'transaction'


class Recurring(RecurringChanges):
    id: str
    start_date: str
    source_kind: str
    status: str
    next_due_date: str
    latest_current_occurrence_date: str
    attention_code: str
    attention_message: str
    baseline: str


class RecurringList(DTO):
    items: list[Recurring]


class RecurringUpdate(ProtectedMutation):
    changes: RecurringChanges


class OrdinaryExpenseChanges(ExpenseFields):
    amount: DecimalString


class ExpenseRecurringConversion(ProtectedMutation):
    schedule_id: str
    name: str
    recurrence_unit: str
    recurrence_interval: str = '1'
    end_date: str = ''
    changes: OrdinaryExpenseChanges


class Occurrence(DTO):
    id: str
    txn_date: str
    amount: DecimalString
    recurring_schedule_id: str
    recurring_scheduled_date: str
    recurring_occurrence_type: str
    source_type: str = ''
    source_id: str = ''


class RecurringDetail(DTO):
    can_delete: bool = False
    schedule: Recurring
    occurrences: list[Occurrence]
    mixed_sources: list[MixedUse]


class ReconcileRequest(DTO):
    through_date: str


class ReconcileResult(DTO):
    queued_count: int = 0
    execution_mode: Literal['explicit', 'worker'] = 'explicit'
    generated_count: int
    attention_count: int

    @model_serializer(mode='wrap')
    def preserve_explicit_response(self, handler):
        result = handler(self)
        if self.execution_mode == 'explicit':
            result.pop('queued_count', None)
            result.pop('execution_mode', None)
        return result


class AssetFields(DTO):
    name: str
    asset_type: str
    notes: str = ''
    acquisition_date: str
    depreciation_start_date: str
    cost_basis: DecimalString
    salvage_value: DecimalString = '0.00'
    useful_life_quarters: str
    depreciation_expense_account_id: str = ''
    accumulated_depreciation_account_id: str = ''
    payee_id: str = ''
    tag_ids: list[str] = Field(default_factory=list)
    reference: str = ''
    source_transaction_id: str = ''


class AssetCreate(AssetFields):
    asset_id: str


class AssetChanges(AssetFields):
    status: str = 'active'
    retired_date: str = ''


class Asset(AssetChanges):
    id: str
    depreciation_method: str
    baseline: str


class AssetList(DTO):
    items: list[Asset]


class AssetTypes(DTO):
    items: list[str]


class AssetUpdate(ProtectedMutation):
    changes: AssetChanges


class DepreciationPeriod(DTO):
    sequence: int
    scheduled_date: str
    depreciation_amount: DecimalString
    cumulative_depreciation: DecimalString
    remaining_book_value: DecimalString
    is_override: bool
    override_reason: str
    posting_status: str
    journal_entry_id: str


class AssetDetail(DTO):
    asset: Asset
    schedule: list[DepreciationPeriod]
    history: list[Occurrence]
    terms_locked: bool
    posting_accounts_locked: bool
    can_delete: bool


class AssetPost(ProtectedMutation):
    through_date: str


class AssetPostResult(DTO):
    posted_count: int
    entry_ids: list[str]


class AssetOverride(ProtectedMutation):
    amount: DecimalString
    reason: str
    as_of_date: str


def project(model, row):
    values = {name: row[name] for name in model.model_fields if name in row}
    if 'tag_ids' in values:
        values['tag_ids'] = normalize_tag_ids(values['tag_ids'])
    return model(**values)


def recurring_response(row):
    return project(Recurring, row)


def mixed_response(row):
    return project(MixedUse, row)


def asset_response(row):
    return project(Asset, row)


def occurrence_response(row):
    # Depreciation history has source provenance but no recurring provenance.
    values = {name: row.get(name, '') for name in Occurrence.model_fields}
    return Occurrence(**values)


def asset_detail_response(detail):
    periods = [DepreciationPeriod(
        sequence=period.sequence, scheduled_date=period.scheduled_date.isoformat(),
        depreciation_amount=format(period.depreciation_amount, '.2f'),
        cumulative_depreciation=format(period.cumulative_depreciation, '.2f'),
        remaining_book_value=format(period.remaining_book_value, '.2f'),
        is_override=period.is_override, override_reason=period.override_reason,
        posting_status=period.posting_status, journal_entry_id=period.journal_entry_id,
    ) for period in detail['schedule']]
    return AssetDetail(asset=asset_response(detail['asset']), schedule=periods,
                       history=[occurrence_response(row) for row in detail['history']],
                       **{field: detail[field] for field in ('terms_locked', 'posting_accounts_locked', 'can_delete')})
