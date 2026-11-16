"""Assemble application services from explicit, persistence-neutral contracts."""

from dataclasses import dataclass

from .contracts import LedgerSetupRepository
from .documents import DocumentStore, ReceiptRepository, ReceiptService
from .ledger_setup import LedgerSetupService
from .mileage import MileageService
from .mileage_contracts import MileageRepository
from .reporting import ReportingService
from .reporting_contracts import LedgerQueryRepository
from .contracts import (
    AccountRepository,
    CategoryRepository,
    PayeeRepository,
    TagRepository,
)
from .reference_data import AccountService, CategoryService, PayeeService, TagService, VehicleService
from .imports import TransactionImportRepository, TransactionImportService
from .accounting_contracts import TransactionRepository, JournalRepository
from .transactions import TransactionService
from .journals import JournalService

from .assets import AssetService
from .asset_contracts import AssetRepository
from .mixed_use import MixedUseService
from .mixed_use_contracts import MixedUseRepository
from .recurring import RecurringService
from .recurring_contracts import RecurringRepository
from .periods import PeriodService


@dataclass(frozen=True)
class ApplicationServices:
    """Services for one explicitly selected business profile.

    The profile key identifies the internal application's configuration scope;
    it is not commercial user authorization. Add domain services as their
    vertical slices are extracted rather than mirroring the storage facade.
    """

    profile_key: str
    ledger: LedgerSetupService
    accounts: AccountService | None = None
    categories: CategoryService | None = None
    imports: TransactionImportService | None = None
    payees: PayeeService | None = None
    tags: TagService | None = None
    vehicles: VehicleService | None = None
    transactions: TransactionService | None = None
    journals: JournalService | None = None
    reporting: ReportingService | None = None
    receipts: ReceiptService | None = None
    mileage: MileageService | None = None
    assets: AssetService | None = None
    mixed_use: MixedUseService | None = None
    recurring: RecurringService | None = None
    periods: PeriodService | None = None


def assemble_application_services(
    profile_key: str,
    *,
    ledger_repository: LedgerSetupRepository,
    reporting_repository: LedgerQueryRepository | None = None,
    receipt_repository: ReceiptRepository | None = None,
    document_store: DocumentStore | None = None,
    mileage_repository: MileageRepository | None = None,
    account_repository: AccountRepository | None = None,
    category_repository: CategoryRepository | None = None,
    import_repository: TransactionImportRepository | None = None,
    payee_repository: PayeeRepository | None = None,
    tag_repository: TagRepository | None = None,
    vehicle_repository: TagRepository | None = None,
    transaction_repository: TransactionRepository | None = None,
    journal_repository: JournalRepository | None = None,
    asset_repository: AssetRepository | None = None,
    mixed_use_repository: MixedUseRepository | None = None,
    recurring_repository: RecurringRepository | None = None,
) -> ApplicationServices:
    """Compose the service graph without constructing infrastructure clients."""
    if not isinstance(profile_key, str) or not profile_key.strip():
        raise ValueError("Application services require a selected business profile.")
    payees = PayeeService(payee_repository) if payee_repository is not None else None
    tags = TagService(tag_repository) if tag_repository is not None else None
    if transaction_repository is not None and (payees is None or tags is None):
        raise ValueError("Transaction services require payee and tag repositories.")
    if (receipt_repository is None) != (document_store is None):
        raise ValueError("Receipt services require both metadata and document storage.")
    transactions = (
        TransactionService(transaction_repository, payees=payees, tags=tags)
        if transaction_repository is not None
        else None
    )
    journals = (
        JournalService(journal_repository) if journal_repository is not None else None
    )
    if asset_repository is not None and journals is None:
        raise ValueError("Asset services require the journal repository.")
    if mixed_use_repository is not None and transactions is None:
        raise ValueError("Mixed-use services require the transaction repository.")
    mixed_use = (
        MixedUseService(mixed_use_repository, transactions=transactions)
        if mixed_use_repository is not None
        else None
    )
    if recurring_repository is not None and (transactions is None or mixed_use is None):
        raise ValueError(
            "Recurring services require transaction and mixed-use repositories."
        )
    return ApplicationServices(
        profile_key=profile_key,
        ledger=LedgerSetupService(ledger_repository),
        reporting=(
            ReportingService(reporting_repository)
            if reporting_repository is not None
            else None
        ),
        receipts=(
            ReceiptService(profile_key, receipt_repository, document_store)
            if receipt_repository is not None and document_store is not None
            else None
        ),
        mileage=(
            MileageService(mileage_repository)
            if mileage_repository is not None
            else None
        ),
        accounts=AccountService(account_repository)
        if account_repository is not None
        else None,
        categories=CategoryService(category_repository)
        if category_repository is not None
        else None,
        imports=TransactionImportService(import_repository)
        if import_repository is not None
        else None,
        payees=payees,
        tags=tags,
        vehicles=VehicleService(vehicle_repository) if vehicle_repository is not None else None,
        transactions=transactions,
        journals=journals,
        assets=AssetService(asset_repository, journals=journals)
        if asset_repository is not None
        else None,
        mixed_use=mixed_use,
        recurring=RecurringService(
            recurring_repository, transactions=transactions, mixed_use=mixed_use
        )
        if recurring_repository is not None
        else None,
    )
