"""Controlled authority switching, rollback and real committed-write boundaries."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json

import pytest
from sqlalchemy import text

from scripts.cutover_bookkeeping import smoke_application
from tabula_rasa.composition import create_application
from tabula_rasa.business_profiles import BusinessProfileConfigurationError
from tabula_rasa.errors import StorageConflictError
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.cutover import PostgresCutoverController, CutoverError, database_identity
from tabula_rasa.infrastructure.postgres.migration import PostgresMigrationRunner
from tabula_rasa.infrastructure.postgres.runtime import Database, DatabaseError
from tabula_rasa.migration.model import Binding
from tests.migration_support import complete_plan


REFERENCE = "sha256:" + "a" * 64
REHEARSAL_EVIDENCE = {"source_freeze": REFERENCE, "schema_baseline": "https://github.com/Roklion/Project_Tabula_Rasa/pull/271"}
PRODUCTION_EVIDENCE = {**REHEARSAL_EVIDENCE, "transaction_integrity": REFERENCE,
                       "production_setup": REFERENCE, "backup_restore": REFERENCE, "point_in_time_recovery": REFERENCE}


def loaded(database, profile="alpha"):
    plan, _, _ = complete_plan(profile)
    PostgresMigrationRunner(database, secondary=True).execute(plan, dry_run=False)
    return plan


def settings_for(*profiles):
    return {"business_profiles": {profile: {"name": "Synthetic " + profile, "spreadsheet_id": "workbook-" + profile,
            "google_drive": {"receipt_folder": {"id": "folder-" + profile}}} for profile in profiles},
            "bookkeeping_backends": {profile: {"backend": "postgres", "entity_id": profile, "mode": "rehearsal"}
                                      for profile in profiles}}


def status(database, profile="alpha"):
    with database.transaction(read_only=True) as connection:
        return dict(connection.execute(text("SELECT * FROM bookkeeping_cutovers WHERE entity_id=:scope"),
                                       {"scope": profile}).mappings().one())


def test_rehearsed_cutover_composes_only_postgres_services_and_smokes_all_domains(database, monkeypatch):
    plans = {profile: loaded(database, profile) for profile in ("alpha", "beta")}
    controller = PostgresCutoverController(database)
    config = settings_for("alpha", "beta")
    def no_sheets(*args, **kwargs):
        raise AssertionError("PostgreSQL requests must never initialize or write Sheets.")
    monkeypatch.setattr("tabula_rasa.storage.GoogleSheetsStorage.from_secrets", no_sheets)
    for profile, plan in plans.items():
        prepared = controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
        assert prepared["state"] == "prepared" and not prepared["cutover_authorized"]
        assert controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE) == prepared
        with pytest.raises(CutoverError, match="not activated"):
            create_application(profile, config, database=database)
        activated = controller.activate(plan.snapshot.binding)
        assert activated["state"] == "postgres" and activated["rollback_available"]
        application = create_application(profile, config, database=database)
        assert application.workbook_admin is None and application.services.profile_key == profile
        assert application.services.receipts.document_scope == "folder-" + profile
        assert application.services.receipts.references("ordinary")[0].storage_key == "file-ordinary"
        assert application.services.reporting.period_report(application.services.reporting.read_ledger()).totals["net"] == 45.1
        assert smoke_application(profile, config, database, plan)["passed"]
        assert status(database, profile)["divergent"] is False
        with pytest.raises(BusinessProfileConfigurationError, match="retired"):
            create_application(profile, {**config, "bookkeeping_backends": {profile: {"backend": "sheets"}}})
    assert status(database, "alpha")["evidence"]["source_contract"]["pull_request"] == 263
    assert "Synthetic" not in json.dumps(activated)


def test_different_entity_id_preserves_source_profile_folder_and_document_ownership(database):
    from tabula_rasa.infrastructure.sheets_migration import snapshot_from_values
    from tabula_rasa.migration.documents import enrich_snapshot
    from tabula_rasa.migration.planning import build_plan
    _, values, documents = complete_plan()
    binding = Binding("alpha", "workbook-alpha", "entity-alpha")
    snapshot = snapshot_from_values(binding, values)
    plan = build_plan(enrich_snapshot(snapshot, entity_name="Synthetic alpha", document_scope="folder-alpha",
                                      documents=documents, inventory_complete=True))
    PostgresMigrationRunner(database, secondary=True).execute(plan, dry_run=False)
    controller = PostgresCutoverController(database)
    controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
    controller.activate(binding)
    config = settings_for("alpha")
    config["bookkeeping_backends"]["alpha"]["entity_id"] = "entity-alpha"
    services = create_application("alpha", config, database=database).services
    assert services.profile_key == "entity-alpha"
    assert services.receipts.references("ordinary")[0].profile_key == "entity-alpha"
    assert services.receipts.documents.profile_key == "alpha"
    assert services.receipts.documents.store.profile_key == "alpha"
    assert smoke_application("alpha", config, database, plan)["passed"]
    config["business_profiles"]["alpha"]["google_drive"]["receipt_folder"]["id"] = "folder-beta"
    with pytest.raises(CutoverError, match="document folder differs"):
        create_application("alpha", config, database=database)


def test_prepared_entity_refuses_bookkeeping_writes_through_services(database):
    plan = loaded(database)
    controller = PostgresCutoverController(database)
    services = create_postgres_services(database, "alpha")
    controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
    with pytest.raises(StorageConflictError) as caught:
        services.accounts.rename("cash", "Synthetic changed account")
    assert caught.value.code == "backend_inactive"
    assert status(database)["state"] == "prepared" and not status(database)["divergent"]
    assert controller.activate(plan.snapshot.binding)["state"] == "postgres"


def test_predivergence_rollback_reactivates_sheets_and_blocks_cached_postgres_services(database):
    plan = loaded(database)
    controller = PostgresCutoverController(database)
    controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
    controller.activate(plan.snapshot.binding)
    services = create_application("alpha", settings_for("alpha"), database=database).services
    rolled_back = controller.rollback(plan)
    assert rolled_back["state"] == "sheets" and not rolled_back["cutover_authorized"]
    with pytest.raises(StorageConflictError, match="inactive"):
        services.accounts.rename("cash", "Synthetic changed")
    with pytest.raises(CutoverError, match="not activated"):
        create_application("alpha", settings_for("alpha"), database=database)
    # Rollback preserves all target/source records; no reverse migration.
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT count(*) FROM entries WHERE entity_id='alpha'")) == 6


def test_any_committed_profile_write_ends_rollback_while_failed_and_other_profile_writes_do_not(database):
    plans = {profile: loaded(database, profile) for profile in ("alpha", "beta")}
    controller = PostgresCutoverController(database)
    for plan in plans.values():
        controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
        controller.activate(plan.snapshot.binding)
    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            connection.execute(text("UPDATE mileage_records SET notes='Synthetic failed write' WHERE entity_id='alpha'"))
            raise RuntimeError("Synthetic rollback")
    assert not status(database)["divergent"]
    services = create_application("beta", settings_for("alpha", "beta"), database=database).services
    services.accounts.rename("cash", "Synthetic beta renamed")
    assert status(database, "beta")["divergent"] and not status(database, "alpha")["divergent"]
    with pytest.raises(CutoverError, match="committed divergent"):
        controller.rollback(plans["beta"])
    # A secondary write has no effect on P&L, but still makes Sheets stale.
    with database.transaction() as connection:
        connection.execute(text("UPDATE mileage_records SET notes='Synthetic committed write' WHERE entity_id='alpha'"))
    with pytest.raises(CutoverError, match="committed divergent"):
        controller.rollback(plans["alpha"])
    assert status(database)["state"] == "postgres"


def test_changed_or_foreign_preserved_source_cannot_be_used_for_rollback(database):
    plan = loaded(database)
    controller = PostgresCutoverController(database)
    controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
    changed, _, _ = complete_plan("beta")
    with pytest.raises(CutoverError, match="No prepared"):
        controller.rollback(changed)
    with pytest.raises(CutoverError, match="different profile"):
        controller.activate(Binding("beta", "workbook-beta", "alpha"))
    from tabula_rasa.migration.documents import enrich_snapshot
    from tabula_rasa.migration.planning import build_plan
    changed = build_plan(enrich_snapshot(plan.snapshot, entity_name="Synthetic changed ledger", document_scope="folder-alpha",
                                        documents=complete_plan()[2], inventory_complete=True))
    with pytest.raises(CutoverError, match="source has changed"):
        controller.rollback(changed)
    assert status(database)["state"] == "prepared"


@pytest.mark.parametrize("missing", ["transaction_integrity", "backup_restore", "point_in_time_recovery", "production_setup", "schema_baseline", "source_freeze"])
def test_commercial_production_requires_every_independent_operator_gate_before_preparation(database, missing):
    plan = loaded(database)
    evidence = deepcopy(PRODUCTION_EVIDENCE)
    del evidence[missing]
    with pytest.raises(CutoverError, match="evidence is missing"):
        PostgresCutoverController(database).prepare(plan, mode="commercial_production", evidence=evidence)
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT count(*) FROM bookkeeping_cutovers")) == 0
        assert connection.scalar(text("SELECT status FROM migration_runs WHERE entity_id='alpha'")) == "loading"


def test_production_environment_is_explicit_distinct_and_bound_to_actual_database(database):
    plan = loaded(database)
    controller = PostgresCutoverController(database)
    controller.prepare(plan, mode="commercial_production", evidence=PRODUCTION_EVIDENCE)
    actual = database_identity(database)
    for production, staging in ((None, None), (actual, actual), ("c" * 64, "b" * 64)):
        with pytest.raises(CutoverError, match="intended provisioned"):
            controller.activate(plan.snapshot.binding, production_database_sha256=production, staging_database_sha256=staging)
    assert status(database)["state"] == "prepared"
    assert controller.activate(plan.snapshot.binding, production_database_sha256=actual,
                               staging_database_sha256="b" * 64)["state"] == "postgres"
    with pytest.raises(CutoverError, match="configured PostgreSQL environment"):
        create_application("alpha", settings_for("alpha"), database=database)
    assert status(database)["evidence"]["production_database_sha256"] == actual


def test_activation_binding_is_rechecked_when_composing_another_database(database):
    plan = loaded(database)
    controller = PostgresCutoverController(database)
    controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
    controller.activate(plan.snapshot.binding)
    with database.transaction() as connection:
        # Simulate restored control records carrying another endpoint identity.
        connection.execute(text("UPDATE bookkeeping_cutovers SET evidence=evidence || CAST(:data AS jsonb) WHERE entity_id='alpha'"),
                           {"data": json.dumps({"database_sha256": "c" * 64})})
    with pytest.raises(CutoverError, match="database differs"):
        create_application("alpha", settings_for("alpha"), database=database)


def test_every_exported_bookkeeping_collection_has_the_native_cutover_guard(database):
    from tabula_rasa.bookkeeping_export.schema import DOMAINS
    with database.transaction(read_only=True) as connection:
        guarded = set(connection.execute(text("SELECT event_object_table FROM information_schema.triggers "
                                              "WHERE trigger_schema=current_schema() AND trigger_name='bookkeeping_cutover_guard'")).scalars())
    assert guarded == set(DOMAINS)


@pytest.mark.parametrize('state', ['prepared', 'sheets', 'postgres'])
def test_vehicle_writes_obey_cutover_authority_and_divergence(database, state):
    plan = loaded(database)
    controller = PostgresCutoverController(database)
    controller.prepare(plan, mode='rehearsal', evidence=REHEARSAL_EVIDENCE)
    if state != 'prepared':
        controller.activate(plan.snapshot.binding)
    if state == 'sheets':
        controller.rollback(plan)
    vehicles = create_postgres_services(database, 'alpha').vehicles
    if state == 'postgres':
        vehicles.create('New vehicle')
        assert status(database)['divergent']
    else:
        with pytest.raises(StorageConflictError):
            vehicles.create('New vehicle')
        assert not status(database)['divergent']
        with database.transaction(read_only=True) as connection:
            assert not connection.scalar(text("SELECT count(*) FROM vehicles WHERE name='New vehicle'"))


def test_source_or_target_mismatch_blocks_preparation_without_validated_status(database):
    plan = loaded(database)
    with database.transaction() as connection:
        connection.execute(text("UPDATE accounts SET opening_balance=0 WHERE entity_id='alpha' AND id='cash'"))
    with pytest.raises(CutoverError, match="acceptance failed"):
        PostgresCutoverController(database).prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT count(*) FROM bookkeeping_cutovers")) == 0
        assert connection.scalar(text("SELECT status FROM migration_runs WHERE entity_id='alpha'")) == "loading"


def test_write_waiting_behind_rollback_cannot_commit_to_the_inactive_target(database, monkeypatch):
    plan = loaded(database)
    controller = PostgresCutoverController(database)
    controller.prepare(plan, mode="rehearsal", evidence=REHEARSAL_EVIDENCE)
    controller.activate(plan.snapshot.binding)
    other = Database(DatabaseSettings(database.engine.url))
    holding_lock, release = Event(), Event()
    original_export = controller._export

    def held_export(connection, binding):
        result = original_export(connection, binding)
        holding_lock.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(controller, "_export", held_export)
    def write():
        with other.transaction() as connection:
            connection.execute(text("UPDATE mileage_records SET notes='Synthetic concurrent edit' WHERE entity_id='alpha'"))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rollback = pool.submit(controller.rollback, plan)
            assert holding_lock.wait(10)
            mutation = pool.submit(write)
            release.set()
            assert rollback.result(timeout=10)["state"] == "sheets"
            with pytest.raises(DatabaseError, match="backend_inactive"):
                mutation.result(timeout=10)
        assert not status(database)["divergent"]
    finally:
        other.dispose()
