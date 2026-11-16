"""Final acceptance tests use actual frozen-format extraction and native SQL."""

from copy import deepcopy
import json

import pytest
from sqlalchemy import text

from tabula_rasa.infrastructure.postgres.migration import PostgresMigrationRunner
from tabula_rasa.infrastructure.postgres.migration_acceptance import PostgresMigrationAcceptance, SOURCE_CONTRACT
from tabula_rasa.infrastructure.sheets_migration import extract_workbook
from tabula_rasa.migration.acceptance import acceptance_text
from tabula_rasa.migration.documents import enrich_snapshot
from tabula_rasa.migration.model import Binding, MigrationError
from tabula_rasa.migration.planning import build_plan
from tests.migration_support import complete_plan, put
from tests.test_sheets_migration_source import ReadOnlyWorkbook


def test_whole_profile_acceptance_covers_all_domains_and_keeps_source_and_authority_untouched(database):
    for profile in ("alpha", "beta"):
        _, values, docs = complete_plan(profile)
        original = deepcopy(values)
        source = ReadOnlyWorkbook(values, identity="workbook-" + profile)
        snapshot = extract_workbook(source, Binding(profile, source.id, profile))
        snapshot = enrich_snapshot(snapshot, entity_name="Synthetic " + profile, document_scope="folder-" + profile,
                                   documents=docs, inventory_complete=True)
        plan = build_plan(snapshot)
        PostgresMigrationRunner(database, secondary=True).execute(plan, dry_run=False)
        report = PostgresMigrationAcceptance(database).accept(plan)
        assert report["passed"] and report["whole_profile_accepted"], report["exceptions"]
        assert report["acceptance"] == "whole_profile" and report["export_verified"]
        assert report["source_contract"] == SOURCE_CONTRACT
        assert len(report["export_domain_counts"]) == 27
        assert report["unit_counts"] == plan.report()["unit_counts"]
        assert not report["cutover_authorized"]
        output = json.dumps(report) + acceptance_text(report)
        assert "PASS" in output and "#261 / PR #263" in output
        assert "Synthetic" not in output and "file-ordinary" not in output and "100.10" not in output
        assert source.values == original
        with database.transaction(read_only=True) as connection:
            assert connection.scalar(text("SELECT status FROM migration_runs WHERE entity_id=:scope"), {"scope": profile}) == "loading"
            assert connection.scalar(text("SELECT count(*) FROM bookkeeping_cutovers")) == 0


def test_matching_core_totals_without_all_loaded_units_never_passes_final_acceptance(database):
    plan, _, _ = complete_plan()
    runner = PostgresMigrationRunner(database, secondary=True)
    runner.execute(plan, dry_run=False)
    with database.transaction() as connection:
        connection.execute(text("UPDATE migration_units SET status='pending',loaded_at=NULL WHERE entity_id='alpha' AND unit_kind='documents'"))
    report = PostgresMigrationAcceptance(database).accept(plan)
    assert not report["whole_profile_accepted"]
    assert any(item["fields"] == ["complete_unit_checkpoints"] for item in report["exceptions"])


def test_valid_extra_tag_is_a_source_relationship_difference(database):
    original_plan, values, docs = complete_plan()
    put(values, "Transactions", 4, tag_ids="[]")
    snapshot = extract_workbook(ReadOnlyWorkbook(values), original_plan.snapshot.binding)
    plan = build_plan(enrich_snapshot(snapshot, entity_name="Synthetic alpha", document_scope="folder-alpha",
                                      documents=docs, inventory_complete=True))
    PostgresMigrationRunner(database, secondary=True).execute(plan, dry_run=False)
    with database.transaction() as connection:
        connection.execute(text("INSERT INTO entry_tags(entity_id,entry_id,tag_id) VALUES ('alpha','income','job')"))
    report = PostgresMigrationAcceptance(database).accept(plan)
    assert report["export_verified"]  # Portable/valid does not mean source-equivalent.
    assert not report["whole_profile_accepted"]
    assert any(item["fields"] == ["complete_tag_relationships"] for item in report["exceptions"])


@pytest.mark.parametrize("change", ["opening_balance", "mileage", "receipt_link"])
def test_material_domain_drift_blocks_final_acceptance_and_is_grouped_privately(database, change):
    plan, _, _ = complete_plan()
    PostgresMigrationRunner(database, secondary=True).execute(plan, dry_run=False)
    sql, domain = {
        "opening_balance": ("UPDATE accounts SET opening_balance=opening_balance+1 WHERE entity_id='alpha' AND id='cash'", "Accounts"),
        "mileage": ("DELETE FROM mileage_records WHERE entity_id='alpha'", "Mileage"),
        "receipt_link": ("DELETE FROM document_links WHERE entity_id='alpha' AND entry_id='ordinary'", "Documents"),
    }[change]
    with database.transaction() as connection:
        connection.execute(text(sql))
    report = PostgresMigrationAcceptance(database).accept(plan)
    assert not report["whole_profile_accepted"]
    assert any(item["domain"] == domain for item in report["exceptions"])
    output = acceptance_text(report)
    assert "FAIL" in output and domain + ":" in output
    assert "Synthetic" not in output and "file-ordinary" not in output


@pytest.mark.parametrize("anomaly", ["missing_reference", "duplicate_id", "partial_pair", "malformed", "retired_header", "future_header"])
def test_final_source_anomalies_and_retired_formats_stop_before_source_or_target_writes(database, anomaly):
    _, values, _ = complete_plan()
    if anomaly == "missing_reference":
        put(values, "Mileage", 1, tag_ids='["absent"]')
    elif anomaly == "duplicate_id":
        values["Accounts"].append(deepcopy(values["Accounts"][1]))
    elif anomaly == "partial_pair":
        values["MixedUseExpenses"] = values["MixedUseExpenses"][:1]
    elif anomaly == "malformed":
        put(values, "Transactions", 4, amount="not-a-number")
    elif anomaly == "retired_header":
        values["Transactions"][0][7] = "payee"
    else:
        values["Transactions"][0].append("future")
    original = deepcopy(values)
    source = ReadOnlyWorkbook(values)
    with pytest.raises(MigrationError):
        extract_workbook(source, Binding("alpha", source.id, "alpha"))
    assert source.values == original
    with database.transaction(read_only=True) as connection:
        for table in ("business_entities", "migration_runs", "entries", "bookkeeping_cutovers"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
