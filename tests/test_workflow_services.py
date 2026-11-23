"""Workflow rules and orchestration work with persistence-free repositories."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest

from tabula_rasa.asset import ASSET_TYPES
from tabula_rasa.errors import StorageConflictError, StorageValidationError
from tabula_rasa.recurrence import occurrence_id
from tests.workflow_service_fakes import Workflows
from tests.application_import_probe import assert_application_runs_without_infrastructure


def asset_data(**changes):
    return dict(
        name="Camera",
        asset_type=ASSET_TYPES[0],
        acquisition_date="2026-01-01",
        depreciation_start_date="2026-01-31",
        cost_basis="10",
        salvage_value="0",
        useful_life_quarters=3,
        depreciation_expense_account_id="expense",
        accumulated_depreciation_account_id="offset",
        **changes,
    )


def mixed_data(**changes):
    return {
        "source_id": "source",
        "linked_transaction_id": "projection",
        "txn_date": "2026-01-31",
        "total_expense": "123.45",
        "business_use_percentage": "33.33333",
        "allocation_rationale": "Business travel",
        "account_id": "cash",
        "category_id": "expense",
        **changes,
    }


def schedule_data(**changes):
    return {
        "schedule_id": "schedule",
        "name": "Rent",
        "txn_date": "2026-01-31",
        "amount": "10",
        "account_id": "cash",
        "category_id": "expense",
        "recurrence_unit": "month",
        **changes,
    }


def test_asset_lifecycle_posting_boundary_and_accounting_invariants():
    workflows = Workflows()
    service = workflows.assets
    asset = service.create(asset_id="asset", **asset_data())
    spy = Mock(wraps=workflows.journal_service.prepare_depreciation)
    workflows.journal_service.prepare_depreciation = spy
    assert [p.scheduled_date for p in service.schedule(asset)] == [
        date(2026, 1, 31),
        date(2026, 4, 30),
        date(2026, 7, 31),
    ]
    posted = service.post_due("asset", "2026-07-31", expected=asset)
    assert [record["amount"] for record in posted] == ["3.33", "3.33", "3.34"]
    assert sum(Decimal(record["amount"]) for record in posted) == Decimal("10")
    assert {record["account_id"] for record in posted} == {"offset"}
    assert {record["source_id"] for record in posted} == {"asset"}
    assert spy.call_count == 1
    assert service.post_due("asset", "2026-07-31", expected=asset) == []
    with pytest.raises(StorageConflictError, match="locked"):
        service.update("asset", expected=asset, cost_basis="12")
    with pytest.raises(StorageConflictError, match="cannot be deleted"):
        service.delete("asset", expected=asset)
    with pytest.raises(StorageConflictError, match="Retirement"):
        service.retire("asset", "2026-04-30", expected=asset)
    retired = service.retire("asset", "2026-07-31", expected=asset)
    assert retired["status"] == "retired"
    assert workflows.transactions.accounts["cash"]["classification"] == "Asset"


def test_asset_overrides_stale_records_and_zero_amount_events():
    workflows = Workflows()
    asset = workflows.assets.create(asset_id="asset", **asset_data())
    with pytest.raises(StorageValidationError, match="due or past"):
        workflows.assets.override(
            "asset", 2, 0, "Historical", as_of_date=date(2026, 1, 31), expected=asset
        )
    overridden = workflows.assets.override(
        "asset", 1, 0, "Historical", as_of_date=date(2026, 1, 31), expected=asset
    )
    with pytest.raises(StorageConflictError, match="changed"):
        workflows.assets.update("asset", expected=asset, name="Stale")
    posted = workflows.assets.post_due("asset", "2026-01-31", expected=overridden)
    assert len(posted) == 1 and posted[0]["amount"] == "0.00"
    with pytest.raises(StorageConflictError, match="locked"):
        workflows.assets.remove_override("asset", 1, expected=overridden)


def test_mixed_use_uses_transaction_service_and_preserves_source_relationship():
    workflows = Workflows()
    spy = Mock(wraps=workflows.transaction_service.prepare_source_projection)
    workflows.transaction_service.prepare_source_projection = spy
    source = workflows.mixed_use.create(**mixed_data())
    assert source["allocated_amount"] == "41.15"
    assert spy.call_count == 1
    projection = workflows.transactions.records["projection"]
    assert projection["amount"] == "41.15" and projection["source_id"] == "source"
    assert projection["description"] == "Allocation rationale: Business travel"
    with pytest.raises(StorageValidationError):
        workflows.transaction_service.delete("projection", expected=projection)
    updated = workflows.mixed_use.update(
        "source", expected=source, business_use_percentage="50", description="Revised"
    )
    assert updated["allocated_amount"] == "61.72"
    assert workflows.transactions.records["projection"]["amount"] == "61.72"
    with pytest.raises(StorageConflictError):
        workflows.mixed_use.delete("source", expected=source)
    workflows.mixed_use.delete("source", expected=updated)
    assert workflows.mixed_use.list() == [] and workflows.transactions.records == {}


def test_mixed_use_rejects_invalid_inputs_before_inline_reference_creation():
    workflows = Workflows()
    with pytest.raises(StorageValidationError):
        workflows.mixed_use.create(
            **mixed_data(
                business_use_percentage="101", new_payee_name="Must not be saved"
            )
        )
    assert workflows.transactions.payees.calls == 0
    assert (
        workflows.transactions.records == {}
        and workflows.mixed_repository.records == {}
    )
    source = workflows.mixed_use.create(**mixed_data())
    with pytest.raises(StorageValidationError, match="cannot be edited"):
        workflows.mixed_use.update(
            "source", expected=source, linked_transaction_id="other"
        )
    with pytest.raises(StorageValidationError, match="provenance"):
        workflows.mixed_use.update(
            "source", expected=source, recurring_schedule_id="other"
        )


def test_recurring_crud_reconciliation_stable_identity_and_pause_resume():
    workflows = Workflows()
    service = workflows.recurring
    schedule = service.create(**schedule_data())
    spy = Mock(wraps=workflows.transaction_service.create_occurrence)
    workflows.transaction_service.create_occurrence = spy
    generated = service.reconcile(as_of=date(2026, 3, 31))["generated"]
    assert [record["txn_date"] for record in generated] == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
    ]
    assert spy.call_count == 3
    assert generated[0]["id"] == occurrence_id(
        "schedule", date(2026, 1, 31), "generated"
    )
    assert service.reconcile(as_of="2026-03-31")["generated"] == []
    assert service.get("schedule")["next_due_date"] == "2026-04-30"
    paused = service.pause("schedule", expected_schedule=schedule)
    assert service.reconcile(as_of="2026-04-30")["generated"] == []
    resumed = service.resume("schedule", expected_schedule=paused)
    assert len(service.reconcile(as_of="2026-04-30")["generated"]) == 1
    ended = service.end("schedule", expected_schedule=resumed)
    with pytest.raises(StorageValidationError, match="Ended"):
        service.update("schedule", expected=ended, status="active")


def test_recurring_mixed_generation_failure_isolation_and_duplicate_prevention():
    workflows = Workflows()
    service = workflows.recurring
    service.create(
        **schedule_data(
            schedule_id="mixed",
            source_kind="mixed_use",
            total_expense="20",
            business_use_percentage="50",
            allocation_rationale="Business travel",
        )
    )
    service.create(**schedule_data(schedule_id="bad", account_id="cash"))
    workflows.recurring_repository.records["bad"]["account_id"] = "missing"
    spy = Mock(wraps=workflows.mixed_use.create_occurrence)
    workflows.mixed_use.create_occurrence = spy
    result = service.reconcile(as_of="2026-01-31")
    assert result["attention"] == {"bad": "validation"}
    assert len(result["generated"]) == 1 and spy.call_count == 1
    source = result["generated"][0]
    projection = workflows.transactions.records[source["linked_transaction_id"]]
    assert (
        projection["amount"] == "10.00"
        and projection["recurring_schedule_id"] == "mixed"
    )
    assert service.reconcile(as_of="2026-01-31")["generated"] == []
    duplicate = deepcopy(projection)
    duplicate["id"] = "duplicate"
    workflows.transactions.records["duplicate"] = duplicate
    assert service.reconcile(as_of="2026-02-28")["attention"]["mixed"] == "validation"
    assert len(workflows.mixed_repository.records) == 1


@pytest.mark.parametrize("kind", ["assets", "mixed_use"])
def test_service_failures_propagate_without_new_retries(kind):
    workflows = Workflows()
    repository = (
        workflows.asset_repository if kind == "assets" else workflows.mixed_repository
    )
    repository.error = RuntimeError("Synthetic failure")
    with pytest.raises(RuntimeError) as error:
        getattr(workflows, kind).create(
            **(asset_data() if kind == "assets" else mixed_data())
        )
    assert error.value is repository.error and repository.calls == 1


def test_workflow_services_are_isolated_between_two_profiles():
    north, south = Workflows(), Workflows()
    for workflows in (north, south):
        workflows.assets.create(asset_id="same-asset", **asset_data())
        workflows.recurring.create(**schedule_data())
        workflows.mixed_use.create(**mixed_data())
    north.assets.post_due("same-asset", "2026-01-31")
    north.recurring.reconcile(as_of="2026-01-31")
    north.mixed_use.delete("source")
    assert len(south.mixed_use.list()) == 1
    assert south.assets.post_due("same-asset", "2026-01-31")
    assert south.recurring.get("schedule")["latest_current_occurrence_date"] == ""
    assert len(south.transactions.records) == 2


def test_all_workflow_rules_run_with_infrastructure_imports_blocked():
    assert_application_runs_without_infrastructure("""
from tests.test_workflow_services import (
    test_asset_lifecycle_posting_boundary_and_accounting_invariants,
    test_mixed_use_uses_transaction_service_and_preserves_source_relationship,
    test_recurring_crud_reconciliation_stable_identity_and_pause_resume,
)
test_asset_lifecycle_posting_boundary_and_accounting_invariants()
test_mixed_use_uses_transaction_service_and_preserves_source_relationship()
test_recurring_crud_reconciliation_stable_identity_and_pause_resume()
""")
