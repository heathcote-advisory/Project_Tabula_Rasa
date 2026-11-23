"""Accounting workflows execute with neutral in-memory repositories."""

from copy import deepcopy
from datetime import date

import pytest

from tabula_rasa.errors import StorageConflictError, StorageValidationError
from tests.accounting_service_fakes import Transactions, Journals
from tests.application_import_probe import assert_application_runs_without_infrastructure


def expense(**changes):
    return {
        "txn_date": "2026-10-04",
        "kind": "Expense",
        "amount": "10.125",
        "account_id": "cash",
        "category_id": "expense",
        **changes,
    }


def postings(**changes):
    return [
        {
            "id": "debit",
            "target_type": "category",
            "target_id": "expense",
            "debit": "10",
            **changes,
        },
        {"id": "credit", "target_type": "account", "target_id": "cash", "credit": "10"},
    ]


def test_transaction_crud_inline_references_and_stale_expectations():
    repository = Transactions()
    service = repository.service()
    record = service.create(
        transaction_id="stable",
        **expense(new_payee_name=" Vendor ", new_tag_name=" Tax "),
    )
    assert record["id"] == "stable" and record["amount"] == "10.12"
    assert record["payee_id"] == "payee-1" and record["tag_ids"] == ["tag-1"]
    assert record["payee"] == "Vendor" and record["receipt_references"] == []
    assert repository.payees.calls == repository.tags.calls == 1
    with pytest.raises(StorageConflictError):
        service.create(
            transaction_id="stable", **expense(new_payee_name="Must not create")
        )
    assert repository.payees.calls == 1
    changed = service.update("stable", expected=record, description=" Revised ")
    assert changed["description"] == "Revised"
    assert (
        changed["id"] == record["id"] and changed["created_at"] == record["created_at"]
    )
    with pytest.raises(StorageConflictError):
        service.update(
            "stable", expected=record, new_payee_name="Must not create", payee_id=""
        )
    with pytest.raises(StorageConflictError):
        service.delete("stable", expected=record)
    assert repository.payees.calls == 1
    service.delete("stable", expected=changed)
    assert service.list() == []


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"amount": "-1"}, "greater than zero"),
        ({"txn_date": "invalid"}, "YYYY-MM-DD"),
        ({"account_id": "unknown"}, "account does not exist"),
        ({"category_id": "income"}, "Category kind"),
        ({"payee_id": "unknown"}, "Payee does not exist"),
        ({"tag_ids": ["unknown"]}, "Tag does not exist"),
        ({"receipt_references": "invalid"}, "receipt references"),
    ],
)
def test_transaction_validation_prevents_record_and_inline_writes(changes, message):
    repository = Transactions()
    with pytest.raises(StorageValidationError, match=message):
        repository.service().create(
            **expense(new_tag_name="Must not create", **changes)
        )
    assert repository.records == {} and repository.tags.calls == 0


def test_historical_inactive_references_can_be_retained_but_not_assigned():
    repository = Transactions()
    service = repository.service()
    record = service.create(
        transaction_id="stable", **expense(new_payee_name="Vendor", new_tag_name="Tax")
    )
    repository.accounts["cash"]["active"] = False
    repository.payees.records["payee-1"]["active"] = "FALSE"
    repository.tags.records["tag-1"]["active"] = "FALSE"
    assert (
        service.update("stable", expected=record, description="Retained")["payee_id"]
        == "payee-1"
    )
    with pytest.raises(StorageValidationError, match="inactive"):
        service.create(**expense(payee_id="payee-1", tag_ids=["tag-1"]))
    with pytest.raises(StorageValidationError, match="Tag is inactive"):
        service.create(**expense(account_id="bank", tag_ids=["tag-1"]))


def test_legacy_payee_text_is_rejected_on_create_and_update():
    repository = Transactions()
    service = repository.service()
    with pytest.raises(StorageValidationError, match="stored payee"):
        service.create(**expense(payee="Unmanaged vendor"))
    record = service.create(
        transaction_id="stable", **expense(new_payee_name="Managed vendor")
    )
    with pytest.raises(StorageValidationError, match="stored payee"):
        service.update("stable", expected=record, payee="Unmanaged vendor")
    assert repository.records["stable"] == record


def test_provenance_is_reserved_and_generated_records_remain_guarded():
    repository = Transactions()
    service = repository.service()
    for changes in (
        {"recurring_schedule_id": "schedule"},
        {"source_type": "mixed_use_expense", "source_id": "source"},
        {"source_type": "depreciation", "source_id": "asset"},
    ):
        with pytest.raises(StorageValidationError):
            service.create(**expense(**changes))
    assert repository.calls == 0
    original = service.create(transaction_id="stable", **expense())
    repository.records["stable"].update(
        recurring_schedule_id="schedule",
        recurring_scheduled_date="2026-10-04",
        recurring_source_kind="transaction",
        recurring_occurrence_type="generated",
    )
    record = deepcopy(repository.records["stable"])
    with pytest.raises(StorageValidationError, match="cannot be changed"):
        service.update("stable", expected=record, recurring_schedule_id="different")
    revised = service.update("stable", expected=record, description="Allowed")
    assert revised["recurring_schedule_id"] == "schedule"
    assert revised["created_at"] == original["created_at"]
    repository.records["stable"].update(
        source_type="mixed_use_expense", source_id="source"
    )
    for operation in (
        lambda: service.update("stable", description="Blocked"),
        lambda: service.delete("stable"),
    ):
        with pytest.raises(StorageValidationError, match="Mixed-use projections"):
            operation()


def test_bulk_metadata_and_transfer_posting_contract():
    repository = Transactions()
    service = repository.service()
    first = service.create(transaction_id="first", **expense())
    second = service.create(transaction_id="second", **expense())
    tag = repository.tags.create("Tax")
    with pytest.raises(StorageValidationError, match="unique"):
        service.bulk_update(
            ["first", "second"],
            expected=[first, second],
            changes={"tag_ids": [tag["id"], tag["id"]]},
        )
    result = service.bulk_update(
        ["first", "second"], expected=[first, second], changes={"tag_ids": [tag["id"]]}
    )
    assert result["count"] == "2" and all(
        record["tag_ids"] == [tag["id"]] for record in service.list()
    )
    with pytest.raises(StorageConflictError):
        service.bulk_delete(["first", "second"], expected=[first, second])
    service.bulk_delete(["first", "second"], expected=service.list())
    assert service.list() == []
    transfer = service.create(
        transaction_id="transfer", **expense(kind="Transfer", to_account_id="bank")
    )
    assert transfer["category_id"] == ""
    lines = service.postings()
    assert [
        (line["target_type"], line["target_id"], line["side"]) for line in lines
    ] == [("account", "bank", "Debit"), ("account", "cash", "Credit")]
    with pytest.raises(StorageValidationError, match="homogeneous"):
        service.bulk_update(
            ["transfer"], expected=[transfer], changes={"category_id": "expense"}
        )
    with pytest.raises(StorageValidationError, match="different"):
        service.create(**expense(kind="Transfer", to_account_id="cash"))


def test_journal_crud_replay_line_identity_and_stale_snapshots():
    repository = Journals()
    service = repository.service()
    record = service.create(
        entry_id="journal",
        txn_date=date(2026, 10, 4),
        description=" Memo ",
        postings=postings(),
    )
    assert record["description"] == "Memo" and record["txn_date"] == "2026-10-04"
    assert [line["id"] for line in record["postings"]] == ["debit", "credit"]
    assert [line["position"] for line in record["postings"]] == [1, 2]
    assert all(line["parent_entry_id"] == "journal" for line in record["postings"])
    assert (
        service.create(
            entry_id="journal",
            txn_date="2026-10-04",
            description="Memo",
            postings=postings(),
        )
        == record
    )
    updated = service.update("journal", expected=record, description="Changed")
    assert updated["postings"] == record["postings"]
    assert service.get("journal", fresh=True) == updated
    for operation in (
        lambda: service.update("journal", expected=record, description="Stale"),
        lambda: service.delete("journal", expected=record),
    ):
        with pytest.raises(StorageConflictError):
            operation()
    service.delete("journal", expected=updated)
    assert service.list() == []


@pytest.mark.parametrize(
    "lines",
    [
        postings(amount="2", side="Debit"),
        [*postings()[:1], {**postings()[1], "credit": "11"}],
        postings(parent_entry_id="other"),
        [{**postings()[0], "id": "credit"}, postings()[1]],
        [None, postings()[1]],
    ],
)
def test_invalid_journals_never_reach_the_repository(lines):
    repository = Journals()
    with pytest.raises(StorageValidationError):
        repository.service().create(
            entry_id="journal", txn_date="2026-10-04", postings=lines
        )
    assert repository.calls == 0 and repository.records == {}


def test_journal_fresh_reference_and_global_posting_identity_rules():
    repository = Journals()
    service = repository.service()
    record = service.create(
        entry_id="journal", txn_date="2026-10-04", postings=postings()
    )
    with pytest.raises(StorageConflictError, match="Posting ID"):
        service.create(entry_id="other", txn_date="2026-10-04", postings=postings())
    repository.references.accounts["cash"]["active"] = False
    assert (
        service.update("journal", expected=record, description="Retained")["postings"]
        == record["postings"]
    )
    with pytest.raises(StorageValidationError, match="inactive"):
        service.create(
            entry_id="new",
            txn_date="2026-10-04",
            postings=[{**line, "id": "new-" + line["id"]} for line in postings()],
        )
    with pytest.raises(StorageValidationError, match="category does not exist"):
        service.create(
            entry_id="missing",
            txn_date="2026-10-04",
            postings=postings(target_id="missing"),
        )


@pytest.mark.parametrize("kind", ["transaction", "journal"])
def test_failures_propagate_without_service_retry(kind):
    repository = Transactions() if kind == "transaction" else Journals()
    repository.error = RuntimeError("Synthetic failure")
    service = repository.service()
    with pytest.raises(RuntimeError) as error:
        if kind == "transaction":
            service.create(**expense())
        else:
            service.create(
                entry_id="journal", txn_date="2026-10-04", postings=postings()
            )
    assert error.value is repository.error and repository.calls == 1


def test_services_and_their_fake_workflows_import_without_infrastructure():
    assert_application_runs_without_infrastructure("""
from tests.accounting_service_fakes import Transactions, Journals
service = Transactions().service()
record = service.create(transaction_id='stable', txn_date='2026-10-04', kind='Expense', amount='10', account_id='cash', category_id='expense', new_payee_name='Vendor', new_tag_name='Tax')
updated = service.update('stable', expected=record, description='Revised')
service.delete('stable', expected=updated)
journals = Journals().service()
lines = [dict(id='debit', target_type='category', target_id='expense', debit='10'), dict(id='credit', target_type='account', target_id='cash', credit='10')]
entry = journals.create(entry_id='journal', txn_date='2026-10-04', postings=lines)
updated = journals.update('journal', expected=entry, description='Revised')
journals.delete('journal', expected=updated)
""")
