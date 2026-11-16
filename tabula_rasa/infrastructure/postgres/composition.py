"""Opt-in repository service graph; cutover controls select production authority."""

from dataclasses import replace

from tabula_rasa.application.periods import PeriodService
from tabula_rasa.application.services import assemble_application_services
from .assets import PostgresAssetRepository
from .mixed_use import PostgresMixedUseRepository
from .recurring import PostgresRecurringRepository
from .recurring_jobs import DurableRecurringService
from .periods import PostgresPeriodRepository
from .accounting import PostgresTransactionRepository, PostgresJournalRepository
from .documents import PostgresReceiptRepository
from .imports import PostgresTransactionImportRepository
from .mileage import PostgresMileageRepository
from .reporting import PostgresLedgerQueryRepository
from .reference_data import (
    PostgresLedgerSetupRepository, PostgresAccountRepository, PostgresCategoryRepository,
    PostgresPayeeRepository, PostgresTagRepository, PostgresVehicleRepository,
)


def create_postgres_services(database, entity_id, *, document_store=None, document_store_kind=None):
    """Compose one explicit entity using the shared infrastructure operation.

    The caller resolves authorized entity context and owns database lifecycle.
    This does not activate a backend, create an entity or migrate its schema.
    """
    ledger = PostgresLedgerSetupRepository(database, entity_id)
    ledger.ledger_name  # Reject an unknown entity before returning a service graph.
    if (document_store is None) != (document_store_kind is None):
        raise ValueError('Supply both the authorized DocumentStore and its stable store kind.')
    services = assemble_application_services(
        entity_id, ledger_repository=ledger,
        account_repository=PostgresAccountRepository(database, entity_id),
        category_repository=PostgresCategoryRepository(database, entity_id),
        payee_repository=PostgresPayeeRepository(database, entity_id),
        tag_repository=PostgresTagRepository(database, entity_id),
        vehicle_repository=PostgresVehicleRepository(database, entity_id),
        transaction_repository=PostgresTransactionRepository(database, entity_id),
        journal_repository=PostgresJournalRepository(database, entity_id),
        asset_repository=PostgresAssetRepository(database, entity_id),
        mixed_use_repository=PostgresMixedUseRepository(database, entity_id),
        import_repository=PostgresTransactionImportRepository(database, entity_id),
        mileage_repository=PostgresMileageRepository(database, entity_id),
        receipt_repository=PostgresReceiptRepository(
            database, entity_id, store_kind=document_store_kind, store_scope_key=document_store.scope_key,
        ) if document_store is not None else None,
        document_store=document_store,
        reporting_repository=PostgresLedgerQueryRepository(database, entity_id),
    )
    # Initial creation/conversion uses these same service preparation owners;
    # reconciliation generates through the same transaction/mixed services.
    recurring = PostgresRecurringRepository(
        database, entity_id, transactions=services.transactions, mixed_use=services.mixed_use,
    )
    return replace(services, periods=PeriodService(PostgresPeriodRepository(database, entity_id)),
                   recurring=DurableRecurringService(recurring,
                   transactions=services.transactions, mixed_use=services.mixed_use))


def create_postgres_export_service(database, entity_id):
    """Compose operator portability without Sheets, DocumentStore or UI setup."""
    from tabula_rasa.application.entity_export import EntityExportService
    from .entity_export import PostgresEntityExportRepository
    return EntityExportService(PostgresEntityExportRepository(database, entity_id))
