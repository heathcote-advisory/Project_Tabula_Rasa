"""Reporting behavior through a native, persistence-free application port."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, getcontext, localcontext

import pytest

from tabula_rasa.application.reporting import ReportingService
from tabula_rasa.application.reporting_contracts import (
    LedgerFilter,
    LedgerJournal,
    LedgerPosting,
    LedgerSnapshot,
    LedgerTransaction,
    ReportingAccount,
    ReportingCategory,
)
from tests.application_import_probe import assert_application_runs_without_infrastructure


class FakeLedgerQueryRepository:
    def __init__(self, snapshot: LedgerSnapshot) -> None:
        self.snapshot = snapshot
        self.reads: list[bool] = []
        self.error: Exception | None = None

    def read_ledger(self, *, force_refresh: bool = False) -> LedgerSnapshot:
        self.reads.append(force_refresh)
        if self.error is not None:
            raise self.error
        return self.snapshot


@pytest.mark.parametrize("amount", ["90071992547409.99", "99999999999999999999999999.99"])
def test_http_report_projection_preserves_cents_and_large_aggregate_totals(amount):
    from tabula_rasa.http.models.reporting import report_response

    large = Decimal(amount)
    snapshot = LedgerSnapshot(
        accounts=(
            ReportingAccount("bank", "Bank", "Bank", "Asset", large, True),
            ReportingAccount("child", "Child", "Bank", "Asset", Decimal("0"), True, "bank"),
            ReportingAccount("capital", "Capital", "Equity", "Equity", large.copy_negate(), True),
        ),
        categories=(ReportingCategory("sales", "Sales", "Income"), ReportingCategory("office", "Office", "Expense")),
        transactions=(
            LedgerTransaction("income", date(2026, 7, 1), "Income", large, "bank", category_id="sales"),
            LedgerTransaction("expense", date(2026, 7, 1), "Expense", Decimal("0.01"), "bank", category_id="office"),
            LedgerTransaction("transfer", date(2026, 7, 1), "Transfer", Decimal("0.02"), "bank", to_account_id="child"),
        ),
        journals=(LedgerJournal("income-journal", date(2026, 7, 1), (
            LedgerPosting("debit", "account", "bank", "Debit", large, 1),
            LedgerPosting("credit", "category", "sales", "Credit", large, 2),
        )),),
    )
    original_precision = getcontext().prec
    response = report_response(ReportingService(FakeLedgerQueryRepository(snapshot)), snapshot, start=None, end=None)
    assert getcontext().prec == original_precision
    with localcontext(prec=60):
        income, net, assets, bank = (format(value, ".2f") for value in (large * 2, large * 2 - Decimal("0.01"), large * 3 - Decimal("0.01"), large * 3 - Decimal("0.03")))
    assert response.profit_loss.model_dump() == {"income": income, "expenses": "0.01", "net": net}
    assert response.balance_sheet.total_assets == response.balance_sheet.total_equity == assets
    assert response.balance_sheet.current_earnings == net
    assert response.balance_sheet.difference == "0.00"
    parent = next(row for row in response.balance_sheet.assets if row.id == "bank")
    assert parent.direct_balance == bank and parent.rollup_balance == assets
    assert {row.id: row.balance for row in response.account_balances} == {"bank": bank, "child": "0.02", "capital": "-" + amount}


@pytest.fixture
def ledger() -> LedgerSnapshot:
    return LedgerSnapshot(
        accounts=(
            ReportingAccount("bank", "Checking", "Bank", "Asset", Decimal("100"), True),
            ReportingAccount("savings", "Savings", "Bank", "Asset", Decimal("0"), False),
            ReportingAccount("capital", "Capital", "Equity", "Equity", Decimal("-100"), True),
        ),
        categories=(
            ReportingCategory("sales", "Sales", "Income"),
            ReportingCategory("operations", "Operations", "Expense"),
            ReportingCategory("office", "Office", "Expense", "operations"),
        ),
        transactions=(
            LedgerTransaction(
                "income", date(2026, 6, 30), "Income", Decimal("50"), "bank",
                category_id="sales", tag_ids=("client",),
            ),
            LedgerTransaction(
                "expense", date(2026, 7, 1), "Expense", Decimal("20"), "bank",
                category_id="office", tag_ids=("internal", "client"),
            ),
            LedgerTransaction(
                "transfer", date(2026, 7, 2), "Transfer", Decimal("30"), "bank",
                to_account_id="savings",
            ),
        ),
        journals=(LedgerJournal(
            "split", date(2026, 7, 3), (
                LedgerPosting("debit", "category", "office", "Debit", Decimal("15"), 1),
                LedgerPosting("credit", "account", "bank", "Credit", Decimal("15"), 2),
            ), description="Journal expense",
        ),),
    )


def test_structured_queries_keep_complete_journals_and_transfer_sides(ledger) -> None:
    repository = FakeLedgerQueryRepository(ledger)
    service = ReportingService(repository)
    query = service.query_ledger()
    assert [entry.id for entry in query.entries] == ["journal:split", "transfer", "expense", "income"]
    assert query.entries[0].journal is ledger.journals[0]
    assert query.entries[0].amount == Decimal("15")
    assert len(query.entries[0].journal.postings) == 2
    assert repository.reads == [False]

    def ids(filters):
        return [entry.id for entry in service.query_ledger(filters, snapshot=ledger).entries]

    assert ids(LedgerFilter(account_ids=("savings",))) == ["transfer"]
    assert ids(LedgerFilter(category_ids=("office",))) == ["journal:split", "expense"]
    assert ids(LedgerFilter(kind="Journal")) == ["journal:split"]
    assert ids(LedgerFilter(tag_ids=("missing", "client"))) == ["expense", "income"]
    assert ids(LedgerFilter(
        start=date(2026, 7, 1), end=date(2026, 7, 1),
        account_ids=("bank",), category_ids=("sales",), tag_ids=("client",),
    )) == ["expense"]
    assert ids(LedgerFilter(start=date(2026, 8, 1), end=date(2026, 7, 1))) == []
    assert repository.reads == [False]  # Reusing a snapshot performs no extra reads.


def test_current_reports_preserve_transfers_hierarchy_and_independent_as_of(ledger) -> None:
    service = ReportingService(FakeLedgerQueryRepository(ledger))
    period = service.period_report(ledger, start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert period.totals == {"income": 0.0, "expenses": 35.0, "net": -35.0}
    assert period.monthly == ({"month": "2026-07", "income": 0.0, "expenses": 35.0},)
    assert [(row["category"], row["layer"], row["direct_total"], row["rollup_total"])
            for row in period.expense_categories] == [
        ("Operations", 1, 0.0, 35.0), ("Office", 2, 35.0, 35.0),
    ]
    balances = {row["id"]: row["balance"] for row in service.account_balances(ledger)}
    assert balances == {"bank": 85.0, "savings": 30.0, "capital": -100.0}
    statement = service.balance_sheet(ledger, as_of=date(2026, 6, 30))
    assert statement["total_assets"] == 150.0
    assert statement["total_equity"] == 150.0
    assert statement["current_earnings"] == 50.0
    assert statement["difference"] == 0.0
    assert service.balance_sheet(ledger)["difference"] == 0.0


def test_empty_ledgers_and_reversed_period_preserve_empty_results(ledger) -> None:
    service = ReportingService(FakeLedgerQueryRepository(LedgerSnapshot()))
    assert service.query_ledger().entries == ()
    report = service.period_report(LedgerSnapshot())
    assert report.totals == {"income": 0.0, "expenses": 0.0, "net": 0.0}
    assert report.monthly == report.expense_categories == ()
    assert service.balance_sheet(LedgerSnapshot())["difference"] == 0.0
    reversed_report = service.period_report(ledger, start=date(2026, 8, 1), end=date(2026, 7, 1))
    assert reversed_report.totals == report.totals


def test_repository_failures_propagate_once_and_profiles_share_no_snapshot(ledger) -> None:
    north_repository = FakeLedgerQueryRepository(ledger)
    south_repository = FakeLedgerQueryRepository(LedgerSnapshot(transactions=(
        LedgerTransaction("income", date(2026, 7, 1), "Income", Decimal("200"), "bank",
                          category_id="sales"),
    )))
    north, south = ReportingService(north_repository), ReportingService(south_repository)
    assert north.query_ledger(force_refresh=True).entries[-1].amount == Decimal("50")
    assert south.query_ledger().entries[0].amount == Decimal("200")
    assert north_repository.reads == [True]
    assert south_repository.reads == [False]
    failure = RuntimeError("Synthetic read failure")
    south_repository.error = failure
    with pytest.raises(RuntimeError) as raised:
        south.query_ledger()
    assert raised.value is failure
    assert south_repository.reads == [False, False]
    assert len(north.query_ledger().entries) == 4


def test_reporting_import_and_fake_query_need_no_ui_or_provider() -> None:
    assert_application_runs_without_infrastructure("""
from tabula_rasa.application.reporting import ReportingService
from tabula_rasa.application.reporting_contracts import LedgerSnapshot
class Repository:
    def read_ledger(self, *, force_refresh=False):
        return LedgerSnapshot()
service = ReportingService(Repository())
assert service.query_ledger().entries == ()
assert service.period_report(service.read_ledger()).totals['net'] == 0
""")
