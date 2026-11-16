"""Native complete exports use only two synthetic entity-local ledgers."""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import event, text

from tabula_rasa.application.entity_export import EntityExportService, ExportError
from tabula_rasa.bookkeeping_export.accounting import ledger_snapshot, normalized_accounting
from tabula_rasa.bookkeeping_export.format import canonical_json, content_digest, validate_artifact
from tabula_rasa.bookkeeping_export.schema import DOMAINS, json_schema
from tabula_rasa.errors import StorageError, StorageNotFoundError
from tabula_rasa.infrastructure.postgres.composition import create_postgres_export_service
from tabula_rasa.infrastructure.postgres.entity_export import PostgresEntityExportRepository
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.runtime import Database
from tests.postgres._support import account, mixed_pair, expense_references
from tests.postgres.core_support import seed_core, expense
from tests.postgres.test_reporting_sources import seed_source_history


def representative(database, *, currencies=("USD", "USD")):
    alpha, beta = seed_core(database, currencies=currencies)
    for scope, services in (("alpha", alpha), ("beta", beta)):
        services.vehicles.create("Fleet van")
        with database.transaction() as connection:
            account(connection, scope, "child", parent="bank")
            account(connection, scope, "debt", classification="Liability")
            account(connection, scope, "capital", classification="Equity")
            for identity, balance in (("bank", "123.45"), ("debt", "-23.45"), ("capital", "-100.00")):
                connection.execute(text("UPDATE accounts SET opening_balance=:balance WHERE entity_id=:scope AND id=:id"),
                                   {"scope": scope, "id": identity, "balance": balance})
            connection.execute(text("""INSERT INTO payees(entity_id,id,incarnation_id,name,name_key)
                VALUES (:scope,'past','past',:name,:name)"""), {"scope": scope, "name": scope + " historic payee"})
        services.transactions.create(transaction_id="ordinary", **expense(payee_id="past", description=scope + " expense"))
        services.transactions.create(transaction_id="income", **expense(kind="Income", amount="100", category_id="sales"))
        services.transactions.create(transaction_id="transfer", **expense(kind="Transfer", amount="7", category_id="", to_account_id="bank"))
        services.journals.create(entry_id="journal", txn_date="2026-10-05", description=scope + " journal", postings=[
            {"id": "ordinary:1", "target_type": "category", "target_id": "office", "side": "Debit", "amount": "5"},
            {"id": "ordinary:2", "target_type": "account", "target_id": "cash", "side": "Credit", "amount": "5"},
        ])
        with database.transaction() as connection:
            seed_source_history(connection, scope)
            connection.execute(text("""INSERT INTO categories(entity_id,id,incarnation_id,name,name_key,kind,parent_id)
                VALUES (:scope,'office-child','office-child','Child expense','child expense','Expense','office')"""), {"scope": scope})
            connection.execute(text("""UPDATE mixed_use_sources SET total_expense=37.50,business_use_percentage=33.33333
                WHERE entity_id=:scope AND id='mixed'"""), {"scope": scope})
            for table, owner, identity in (("schedule_tags", "schedule_id", "ordinary-schedule"),
                                           ("asset_tags", "asset_id", "asset")):
                connection.execute(text(f"INSERT INTO {table}(entity_id,{owner},tag_id) VALUES (:scope,:id,'client')"),
                                   {"scope": scope, "id": identity})
            connection.execute(text("UPDATE payees SET active=false WHERE entity_id=:scope"), {"scope": scope})
            connection.execute(text("UPDATE tags SET active=false WHERE entity_id=:scope"), {"scope": scope})
            connection.execute(text("UPDATE accounts SET active=false WHERE entity_id=:scope AND id='cash'"), {"scope": scope})
            connection.execute(text("""INSERT INTO mileage_records(entity_id,id,incarnation_id,trip_date,miles,
                start_location,destination,business_purpose) VALUES (:scope,'trip','trip','2026-10-05',1.2,'Office','Client','Work')"""),
                {"scope": scope})
            connection.execute(text("INSERT INTO mileage_tags(entity_id,mileage_id,tag_id) VALUES (:scope,'trip','client')"), {"scope": scope})
            connection.execute(text("""INSERT INTO import_batches(entity_id,id,incarnation_id,source_kind,format_version,status)
                VALUES (:scope,'batch','batch','csv','1','complete')"""), {"scope": scope})
            for position, kind, entry, mileage in ((1, "entry", "ordinary", None), (2, "mileage", None, "trip")):
                connection.execute(text("""INSERT INTO import_records(entity_id,batch_id,source_position,record_kind,entry_id,mileage_id,fingerprint)
                    VALUES (:scope,'batch',:position,:kind,:entry,:mileage,'synthetic-fingerprint')"""),
                    {"scope": scope, "position": position, "kind": kind, "entry": entry, "mileage": mileage})
            connection.execute(text("""INSERT INTO migration_runs(entity_id,id,incarnation_id,source_profile_key,source_workbook_key,
                source_kind,manifest_version,mapping_version,source_snapshot_sha256,extracted_at,status)
                VALUES (:scope,'run','run',:profile,:workbook,'current_workbook','1','1',:digest,'2026-10-05T12:00:00Z','validated')"""),
                {"scope": scope, "profile": scope + "-profile", "workbook": scope + "-workbook", "digest": "a" * 64})
            connection.execute(text("""INSERT INTO migration_units(entity_id,run_id,unit_key,unit_kind,unit_sha256,status,loaded_at)
                VALUES (:scope,'run','entry','simple_entry',:digest,'loaded','2026-10-05T12:01:00Z')"""), {"scope": scope, "digest": "b" * 64})
            connection.execute(text("""INSERT INTO migration_id_mappings(entity_id,run_id,source_record_kind,source_id,
                target_record_kind,target_id,mapping_reason,source_updated_at)
                VALUES (:scope,'run','Transactions','original','entry','ordinary','preserved','2026-10-04T12:00:00.123456Z')"""), {"scope": scope})
    return alpha, beta


def test_complete_native_export_retains_every_domain_and_reproduces_accounting(database):
    from jsonschema import Draft202012Validator, FormatChecker
    alpha, beta = representative(database)
    for scope, services, other in (("alpha", alpha, "beta"), ("beta", beta, "alpha")):
        artifact = create_postgres_export_service(database, scope).export()
        assert artifact["manifest"]["status"] == "complete"
        assert set(artifact["domains"]) == set(DOMAINS)
        assert all(artifact["manifest"]["counts"].values())
        assert validate_artifact(artifact, entity_id=scope) == artifact
        Draft202012Validator(json_schema(), format_checker=FormatChecker()).validate(artifact)
        assert artifact["manifest"]["entity_id"] == scope
        # Schema revision names are deployment metadata, not tenant identifiers.
        payload = canonical_json(artifact["domains"])
        assert other not in payload
        assert artifact["domains"]["mileage_records"][0]["miles"] == "1.2"
        assert artifact["domains"]["mixed_use_sources"][0]["business_use_percentage"] == "33.33333"
        assert artifact["domains"]["migration_id_mappings"][0]["source_updated_at"] == "2026-10-04T12:00:00.123456Z"
        assert len(artifact["domains"]["documents"]) == 4  # Includes unlinked recovery metadata.
        assert artifact["manifest"]["document_bytes"] == "external"
        historical = ledger_snapshot(artifact["domains"])
        live = services.reporting.read_ledger()
        assert historical == live
        assert normalized_accounting(historical) == normalized_accounting(live)
        assert services.reporting.period_report(historical).totals == {"income": 100.0, "expenses": 38.0, "net": 62.0}
        assert {row["id"]: row["balance"] for row in services.reporting.account_balances(historical)} == {
            "cash": 63.0, "bank": 130.45, "child": 0.0, "debt": -23.45, "capital": -100.0, "accumulated": -8.0,
        }
        assert services.reporting.balance_sheet(historical)["difference"] == 0
        again = create_postgres_export_service(database, scope).export()
        assert artifact["domains"] == again["domains"] and artifact["manifest"]["content_sha256"] == again["manifest"]["content_sha256"]
        assert artifact["manifest"]["snapshot_at"] != again["manifest"]["snapshot_at"]


@pytest.mark.parametrize("state", ["missing_projection", "projection_mismatch"])
def test_supported_problem_states_are_retained_without_repair(database, state):
    with database.transaction() as connection:
        for scope in ("alpha", "beta"):
            expense_references(connection, scope)
            mixed_pair(connection, scope, state=state)
    for scope in ("alpha", "beta"):
        artifact = create_postgres_export_service(database, scope).export()
        assert artifact["manifest"]["status"] == "complete_with_diagnostics"
        assert artifact["manifest"]["diagnostics"] == [{"domain": "mixed_use_sources", "record": 0, "code": state}]
        assert artifact["domains"]["mixed_use_sources"][0]["relationship_state"] == state
        assert validate_artifact(artifact, entity_id=scope) == artifact


def test_missing_historical_mapping_target_is_inspectable(database):
    representative(database)
    with database.transaction() as connection:
        connection.execute(text("""INSERT INTO migration_id_mappings(entity_id,run_id,source_record_kind,source_id,
            target_record_kind,target_id,mapping_reason)
            VALUES ('alpha','run','Transactions','deleted','entry','deleted','preserved')"""))
    artifact = create_postgres_export_service(database, "alpha").export()
    assert artifact["manifest"]["diagnostics"] == [{"domain": "migration_id_mappings", "record": 0, "code": "historical_target_absent"}]


@pytest.mark.parametrize("corruption,code", [
    ("domain", "domain_coverage"), ("foreign_row", "entity_scope"), ("reference", "relationship"),
    ("balance", "accounting"), ("count", "domain_counts"), ("checksum", "checksum"),
    ("precision", "record_format"), ("unknown_field", "record_format"), ("diagnostics", "diagnostics"),
])
def test_artifact_validation_rejects_incomplete_or_unrepresentable_content(database, corruption, code):
    seed_core(database)
    artifact = create_postgres_export_service(database, "alpha").export()
    if corruption == "domain":
        del artifact["domains"]["mileage_tags"]
    elif corruption == "foreign_row":
        artifact["domains"]["accounts"][0]["entity_id"] = "beta"
    elif corruption == "reference":
        artifact["domains"]["accounts"][0]["parent_id"] = "foreign-only"
    elif corruption == "balance":
        # Use a valid native entry, then corrupt its independent serialized lines.
        from tests.postgres._support import simple_entry
        with database.transaction() as connection:
            simple_entry(connection)
        artifact = create_postgres_export_service(database, "alpha").export()
        artifact["domains"]["postings"][0]["amount"] = "9.00"
    elif corruption == "count":
        artifact["manifest"]["counts"]["accounts"] += 1
    elif corruption == "checksum":
        artifact["manifest"]["content_sha256"] = "b" * 64
    elif corruption == "precision":
        artifact["domains"]["accounts"][0]["opening_balance"] = "0.001"
    elif corruption == "unknown_field":
        artifact["domains"]["accounts"][0]["oauth_token"] = "private-sentinel"
    else:
        artifact["manifest"]["diagnostics"].append({"domain": "accounts", "record": 0, "code": "ignored"})
    # Rehash modified content to prove relational/semantic validation is independent.
    if corruption != "checksum":
        artifact["manifest"]["content_sha256"] = content_digest(artifact["domains"])
    with pytest.raises(ExportError, match=code) as caught:
        validate_artifact(artifact, entity_id="alpha")
    assert "private-sentinel" not in str(caught.value) and caught.value.__context__ is None


def test_repository_and_service_cannot_silently_omit_a_supported_domain(database):
    seed_core(database)
    snapshot = PostgresEntityExportRepository(database, "alpha").read_export()
    class Incomplete:
        def read_export(self):
            return replace(snapshot, domains={name: rows for name, rows in snapshot.domains.items() if name != "documents"})
    with pytest.raises(ExportError, match="domain_coverage"):
        EntityExportService(Incomplete()).export()


def test_export_requires_current_committed_snapshot_and_known_entity(database):
    seed_core(database)
    with pytest.raises(StorageNotFoundError):
        create_postgres_export_service(database, "absent").export()
    for read_only in (False, True):
        with pytest.raises(StorageError, match="could not be confirmed") as caught:
            with database.transaction(read_only=read_only):
                create_postgres_export_service(database, "alpha").export()
        assert caught.value.code == "boundary_mismatch"
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE accounts ADD COLUMN unsupported_bookkeeping_setting text"))
    with pytest.raises(ExportError, match="unsupported_database_schema"):
        create_postgres_export_service(database, "alpha").export()


def test_export_reads_form_one_snapshot_across_concurrent_committed_write(database):
    alpha, _ = representative(database)
    other = Database(DatabaseSettings(database.engine.url))
    fired = False
    def after_read(connection, cursor, statement, parameters, context, many):
        nonlocal fired
        if not fired and "FROM accounts WHERE entity_id" in statement:
            fired = True
            with other.transaction() as writer:
                writer.execute(text("UPDATE accounts SET opening_balance=124.45 WHERE entity_id='alpha' AND id='bank'"))
                writer.execute(text("UPDATE postings SET amount=22.50 WHERE entity_id='alpha' AND entry_id='ordinary'"))
    event.listen(database.engine, "after_cursor_execute", after_read)
    try:
        artifact = create_postgres_export_service(database, "alpha").export()
    finally:
        event.remove(database.engine, "after_cursor_execute", after_read)
        other.dispose()
    assert fired
    historical = ledger_snapshot(artifact["domains"])
    assert {row.id: row.opening_balance for row in historical.accounts}["bank"] == Decimal("123.45")
    assert {row.id: row.amount for row in historical.transactions}["ordinary"] == Decimal("12.50")
    live = alpha.reporting.read_ledger()
    assert {row.id: row.opening_balance for row in live.accounts}["bank"] == Decimal("124.45")
    assert {row.id: row.amount for row in live.transactions}["ordinary"] == Decimal("22.50")


def test_private_metadata_remains_resolvable_without_exporting_document_bytes(database):
    from tests.postgres.test_document_repository import setup
    from tests.test_application_documents import prepared
    alpha, _, store = setup(database)
    workflow = {"transaction_id": "txn", "receipts": prepared(content=b"synthetic-private-document-bytes")}
    alpha.receipts.process_recovery(workflow)
    artifact = create_postgres_export_service(database, "alpha").export()
    document, = artifact["domains"]["documents"]
    assert store.fetch("alpha", document["storage_key"]) == b"synthetic-private-document-bytes"
    assert document["store_scope_key"] == store.scope_key and document["store_kind"] == "fake"
    assert "synthetic-private-document-bytes" not in canonical_json(artifact)
    assert artifact["domains"]["document_links"][0]["document_id"] == document["id"]


def test_operator_exports_without_sheets_and_excludes_control_plane_data(database, tmp_path, monkeypatch, capsys):
    from scripts import export_postgres_entity as command
    from tabula_rasa.bookkeeping_export.files import read_artifact
    from tabula_rasa.storage import GoogleSheetsStorage
    seed_core(database)
    with database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE synthetic_auth_data(entity_id text, oauth_token text, membership text)"))
        connection.execute(text("INSERT INTO synthetic_auth_data VALUES ('alpha','synthetic-secret-sentinel','owner')"))

    def no_sheets(*args, **kwargs):
        raise AssertionError("Sheets must not be accessed")
    monkeypatch.setattr(GoogleSheetsStorage, "from_secrets", no_sheets)
    monkeypatch.setattr(GoogleSheetsStorage, "from_secrets_read_only", no_sheets)
    monkeypatch.setenv("TABULA_RASA_DATABASE_URL", database.engine.url.render_as_string(hide_password=False))
    output = tmp_path / "entity.bookkeeping.json"
    assert command.main(["--entity", "alpha", "--output", str(output)]) == 0
    assert "synthetic-secret-sentinel" not in output.read_text(encoding="utf-8")
    assert validate_artifact(read_artifact(output, entity_id="alpha"), entity_id="alpha")
    assert capsys.readouterr().out.strip() == '{"status": "complete", "format_version": 2, "diagnostic_count": 0}'


def test_missing_domain_schema_fails_without_exposing_private_table_names(database):
    seed_core(database)
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE accounts RENAME TO synthetic_private_account_table"))
    with pytest.raises(ExportError, match="unsupported_database_schema") as caught:
        create_postgres_export_service(database, "alpha").export()
    assert caught.value.__context__ is None


def test_native_export_preserves_supported_money_extremes_and_calendar_dates(database):
    seed_core(database)
    with database.transaction() as connection:
        connection.execute(text("""UPDATE accounts SET opening_balance=-99999999999999999999999999.99
            WHERE entity_id='alpha' AND id='cash'"""))
        from tests.postgres._support import simple_entry
        simple_entry(connection)
        connection.execute(text("UPDATE entries SET txn_date='2024-02-29' WHERE entity_id='alpha'"))
    artifact = create_postgres_export_service(database, "alpha").export()
    assert {row["id"]: row["opening_balance"] for row in artifact["domains"]["accounts"]}["cash"] == "-99999999999999999999999999.99"
    assert artifact["domains"]["entries"][0]["txn_date"] == "2024-02-29"


def test_offline_validation_rejects_receipt_on_depreciation_entry_for_each_entity(database):
    representative(database)
    for scope in ("alpha", "beta"):
        artifact = create_postgres_export_service(database, scope).export()
        assert validate_artifact(artifact, entity_id=scope) == artifact
        depreciation = next(row for row in artifact["domains"]["entries"] if row["depreciation_asset_id"])
        link = next(row for row in artifact["domains"]["document_links"] if row["entry_id"] == "ordinary")
        link["entry_id"] = depreciation["id"]
        artifact["manifest"]["content_sha256"] = content_digest(artifact["domains"])
        with pytest.raises(ExportError, match="relationship"):
            validate_artifact(artifact, entity_id=scope)
