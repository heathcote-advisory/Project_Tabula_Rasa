"""Explicit public projections; application services own all calculations."""

from dataclasses import asdict
from decimal import Decimal

from tabula_rasa.hierarchy import hierarchy_paths

from .accounts import DecimalString
from .common import DTO


def money(value):
    return format(Decimal(str(value)), '.2f')


class LedgerLine(DTO):
    target: str
    side: str
    amount: DecimalString
    payee: str
    description: str


class LedgerItem(DTO):
    id: str
    record_id: str
    entry_type: str
    txn_date: str
    kind: str
    amount: DecimalString
    account: str = ''
    to_account: str = ''
    category: str = ''
    payee: str = ''
    tags: list[str]
    description: str = ''
    reference: str = ''
    receipt_count: int = 0
    source_type: str = ''
    recurring_schedule_id: str = ''
    postings: list[LedgerLine]


class LedgerList(DTO):
    items: list[LedgerItem]


def ledger_response(query):
    accounts = hierarchy_paths([asdict(row) for row in query.snapshot.accounts])
    categories = hierarchy_paths([asdict(row) for row in query.snapshot.categories])
    rows = []
    for entry in query.entries:
        common = dict(id=entry.id, txn_date=entry.date.isoformat(), kind=entry.kind, amount=money(entry.amount))
        if entry.transaction is not None:
            row = entry.transaction
            rows.append(LedgerItem(**common, record_id=row.id, entry_type='transaction',
                account=accounts.get(row.account_id, 'Unknown'), to_account=accounts.get(row.to_account_id, ''),
                category=categories.get(row.category_id, ''), payee=row.payee, tags=list(row.tags),
                description=row.description, reference=row.reference, receipt_count=row.receipt_count, source_type=row.source_type,
                recurring_schedule_id=row.recurring_schedule_id, postings=[]))
        else:
            row = entry.journal
            rows.append(LedgerItem(**common, record_id=row.id, entry_type='journal',
                description=row.description, account=f'Multiple accounts · {len(row.postings)} postings', tags=[],
                postings=[LedgerLine(target=(accounts if line.target_type == 'account' else categories).get(line.target_id, 'Unknown'),
                    side=line.side, amount=money(line.amount), payee=line.payee, description=line.description) for line in row.postings]))
    return LedgerList(items=rows)


class ProfitLoss(DTO):
    income: DecimalString
    expenses: DecimalString
    net: DecimalString


class BalanceRow(DTO):
    id: str
    name: str
    type: str
    parent_id: str
    balance: DecimalString
    direct_balance: DecimalString
    rollup_balance: DecimalString


class BalanceSheet(DTO):
    assets: list[BalanceRow]
    liabilities: list[BalanceRow]
    equity: list[BalanceRow]
    current_earnings: DecimalString
    total_assets: DecimalString
    total_liabilities: DecimalString
    total_equity: DecimalString
    difference: DecimalString


class AccountBalance(DTO):
    parent_id: str = ''
    id: str
    name: str
    classification: str
    active: bool
    balance: DecimalString


class ReportSummary(DTO):
    profit_loss: ProfitLoss
    balance_sheet: BalanceSheet
    account_balances: list[AccountBalance]


def report_response(service, snapshot, *, start, end):
    period = service.period_report(snapshot, start=start, end=end, decimal_values=True)
    sheet = service.balance_sheet(snapshot, as_of=end, decimal_values=True)
    sections = {name: [BalanceRow(**{key: money(value) if key in {'balance', 'direct_balance', 'rollup_balance'} else value
                                    for key, value in row.items()}) for row in sheet[name]]
                for name in ('assets', 'liabilities', 'equity')}
    parents = {item.id: item.parent_id or '' for item in snapshot.accounts}
    return ReportSummary(profit_loss=ProfitLoss(**{key: money(value) for key, value in period.totals.items()}),
        balance_sheet=BalanceSheet(**sections, **{name: money(sheet[name]) for name in BalanceSheet.model_fields if name not in sections}),
        account_balances=[AccountBalance(id=row['id'], name=row['name'], classification=row['classification'],
            active=row['active'], parent_id=parents.get(row['id'], ''), balance=money(row['balance'])) for row in service.account_balances(snapshot, decimal_values=True)])
