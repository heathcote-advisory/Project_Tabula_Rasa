"""Synthetic native verifier coverage, not managed production restore evidence."""

from copy import deepcopy

import pytest
from sqlalchemy import event, text

from tabula_rasa.application.entity_export import ExportError
from tabula_rasa.bookkeeping_export.format import content_digest
from tabula_rasa.infrastructure.postgres.composition import create_postgres_export_service
from tabula_rasa.infrastructure.postgres.cutover import database_identity
from tabula_rasa.infrastructure.postgres.recovery import verify_recovery
from tests.postgres.core_support import seed_core, expense
from tests.postgres.test_entity_export import representative


def exported(database, scope="alpha"):
    return create_postgres_export_service(database, scope).export()


def verify(database, expected, *, scope="alpha", later=None, **changes):
    return verify_recovery(database, scope, expected, later=later, **{
        "source_database_sha256": "a" * 64, "target_database_sha256": database_identity(database), **changes,
    })


def test_representative_recovery_verification_is_read_only_and_isolates_two_entities(database):
    _, beta = representative(database)
    expected_alpha, expected_beta = exported(database), exported(database, "beta")
    assert all(expected_alpha["manifest"]["counts"].values())  # All 26 supported collections.
    beta.transactions.create(transaction_id="after-point", **expense(account_id="bank"))
    statements = []
    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(database.engine, "before_cursor_execute", observe)
    try:
        result = verify(database, expected_alpha)
        assert result == {
            "status": "matched", "scope": "entity_bookkeeping",
            "database_schema_version": "0022_drive_recovery_binding",
            "content_sha256": expected_alpha["manifest"]["content_sha256"], "later_state_distinct": False,
            "diagnostic_count": 0,
        }
        with pytest.raises(ExportError, match="recovery_point_mismatch"):
            verify(database, expected_beta, scope="beta")
    finally:
        event.remove(database.engine, "before_cursor_execute", observe)
    assert statements and "SET TRANSACTION READ ONLY" in statements
    assert not any(sql.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP"}
                   for sql in statements)
    assert exported(database)["domains"] == expected_alpha["domains"]


def test_pitr_comparison_requires_known_before_state_and_excludes_later_committed_state(database):
    alpha, _ = seed_core(database)
    alpha.transactions.create(transaction_id="before-point", **expense())
    expected = exported(database)
    late = alpha.transactions.create(transaction_id="after-point", **expense())
    later = exported(database)
    assert expected["manifest"]["content_sha256"] != later["manifest"]["content_sha256"]
    with pytest.raises(ExportError, match="recovery_point_mismatch"):
        verify(database, expected, later=later)
    # Simulate recovered content only; this is explicitly not a provider drill.
    alpha.transactions.delete("after-point", expected=late)
    assert verify(database, expected, later=later)["later_state_distinct"] is True
    assert alpha.transactions.list()[0]["id"] == "before-point"
    with pytest.raises(ExportError, match="pitr_control_unchanged"):
        verify(database, expected, later=expected)


@pytest.mark.parametrize("target", ["source", "wrong", "malformed"])
def test_endpoint_bindings_fail_before_any_database_reads(database, target):
    identity = database_identity(database)
    changes = {"source_database_sha256": identity} if target == "source" else {
        "target_database_sha256": "b" * 64 if target == "wrong" else "private-secret",
    }
    def refuse(*args):
        pytest.fail("An invalid endpoint must fail before a database read")
    event.listen(database.engine, "before_cursor_execute", refuse)
    try:
        with pytest.raises(ExportError, match="recovery_target") as caught:
            verify(database, None, **changes)
        assert "private-secret" not in str(caught.value) and caught.value.__context__ is None
    finally:
        event.remove(database.engine, "before_cursor_execute", refuse)


def test_expected_point_requires_matching_schema_valid_checksum_and_same_entity(database):
    seed_core(database)
    expected = exported(database)
    expected["manifest"]["database_schema_version"] = "0004_mileage_order"
    with pytest.raises(ExportError, match="recovery_schema_mismatch"):
        verify(database, expected)
    expected = exported(database)
    with pytest.raises(ExportError, match="entity_scope"):
        verify(database, expected, scope="beta")
    with pytest.raises(ExportError, match="entity_scope"):
        verify(database, expected, later=exported(database, "beta"))
    expected["manifest"]["content_sha256"] = "f" * 64
    with pytest.raises(ExportError, match="checksum"):
        verify(database, expected)


def test_matching_totals_cannot_hide_a_recovered_reference_or_relationship_change(database):
    representative(database)
    expected = exported(database)
    changed = deepcopy(expected)
    # Valid relationship drift with unchanged accounting totals must fail.
    changed["domains"]["mileage_tags"] = []
    changed["manifest"]["counts"]["mileage_tags"] = 0
    changed["manifest"]["content_sha256"] = content_digest(changed["domains"])
    with pytest.raises(ExportError, match="recovery_point_mismatch"):
        verify(database, changed)


def test_expected_supported_diagnostics_are_preserved_but_unknown_revision_stops(database):
    representative(database)
    expected = exported(database)
    changed = deepcopy(expected)
    changed["domains"]["migration_id_mappings"][0]["target_id"] = "historically-deleted"
    changed["manifest"]["content_sha256"] = content_digest(changed["domains"])
    changed["manifest"]["status"] = "complete_with_diagnostics"
    changed["manifest"]["diagnostics"] = [{
        "domain": "migration_id_mappings", "record": 0, "code": "historical_target_absent",
    }]
    with pytest.raises(ExportError, match="recovery_point_mismatch"):
        verify(database, changed)  # The diagnostic state is valid but not restored.
    with database.transaction() as connection:
        connection.execute(text("UPDATE migration_id_mappings SET target_id='historically-deleted' WHERE entity_id='alpha'"))
    result = verify(database, changed, later=expected)
    assert result["status"] == "matched" and result["diagnostic_count"] == 1
    expected["manifest"]["database_schema_version"] = "unknown-private-sentinel"
    with pytest.raises(ExportError, match="unsupported_database_schema") as caught:
        verify(database, expected)
    assert "private-sentinel" not in str(caught.value)
