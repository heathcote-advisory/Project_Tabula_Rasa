"""The explicit v1 canonical record inventory; no worksheet or SQL types.

Adding a supported field/domain requires an export format decision. Unknown
fields are rejected rather than silently dropped. This is also the source for
the machine-readable JSON Schema exposed by the operator command.
"""

from dataclasses import dataclass

FORMAT_NAME = "tabula-rasa-bookkeeping"
FORMAT_VERSION = 1
# A new database revision requires a completeness review for this format.
# 0005 adds only operator authority/evidence, not bookkeeping fields. The v1
# inventory remains complete; cutover controls are deployment records.
# Identity control-plane tables contain no canonical bookkeeping fields and
# intentionally do not travel in entity exports or restore artifacts.
# Operations history is private and requires a separate authorized export contract.
# 0012 changes no v1 bookkeeping field; audit/job tables are deliberately excluded.
# 0013 adds separately protected operational policy/correction relationships.
# Reversing journals use existing entries/postings; those remain portable v1 data.
# 0014 retains recovery-only provider locations; documents keeps the current location.
# 0015 adds only privileged support grants/history, excluded from financial exports.
# 0016 adds only a control-plane uniqueness constraint; v1 stays unchanged.
# 0017 adds content-free customer delivery evidence; canonical v1 is unchanged.
# 0018 adds customer OAuth state, excluded from financial exports.
# 0019 adds program eligibility/history; canonical v1 stays unchanged.
# 0020 permits CAD in the existing currency_code field; v1 encoding is unchanged.
# 0021 adds lifecycle control-plane records only, excluded from financial exports.
SUPPORTED_DATABASE_REVISION = "0022_drive_recovery_binding"
SUPPORTED_DATABASE_REVISIONS = ("0004_mileage_order", "0005_bookkeeping_cutover",
                                "0006_identity_control_plane", "0007_identity_sessions", "0008_owner_lock_scope",
                                "0009_external_connections", "0010_workspace_lifecycle",
                                "0010_onboarding_routes", "0011_control_plane_join", "0012_operations", "0013_accounting_controls",
                                '0014_document_locations', '0015_support_controls', '0016_business_tenancy', '0017_customer_exports', '0018_drive_oauth', '0019_beta_admission', '0020_book_currency', '0021_customer_lifecycle', SUPPORTED_DATABASE_REVISION)
ROOT = "id:text created_at:instant updated_at:instant incarnation_id:text version:integer"


@dataclass(frozen=True)
class Domain:
    fields: dict[str, str]
    key: tuple[str, ...]
    references: dict[str, str]


def domain(fields, *, key="id", references="", root=False, entity=True):
    fields = ("entity_id:text " if entity else "") + (ROOT + " " if root else "") + fields
    return Domain(dict(field.split(":") for field in fields.split()),
                  tuple(key.split()), dict(pair.split(":") for pair in references.split()))


DOMAINS = {
    "business_entities": domain("name:text currency_code:text", root=True, entity=False),
    "accounts": domain("name:text name_key:text type:text classification:text opening_balance:money active:boolean parent_id:text?",
                       root=True, references="parent_id:accounts"),
    "categories": domain("name:text name_key:text kind:text parent_id:text?", root=True, references="parent_id:categories"),
    "payees": domain("name:text name_key:text active:boolean", root=True),
    "tags": domain("name:text name_key:text active:boolean", root=True),
    "entries": domain("txn_date:date projection_kind:text simple_kind:text? description:text reference:text payee_id:text? "
                      "mixed_use_source_id:text? depreciation_asset_id:text?", root=True,
                      references="payee_id:payees mixed_use_source_id:mixed_use_sources depreciation_asset_id:fixed_assets"),
    "postings": domain("id:text entry_id:text projection_kind:text position:integer account_id:text? category_id:text? "
                       "side:text amount:money payee_id:text? description:text", key="projection_kind id",
                       references="entry_id:entries account_id:accounts category_id:categories payee_id:payees"),
    "entry_tags": domain("entry_id:text tag_id:text", key="entry_id tag_id", references="entry_id:entries tag_id:tags"),
    "mixed_use_sources": domain("txn_date:date total_expense:money business_use_percentage:percentage allocated_amount:money "
                                "account_id:text category_id:text payee_id:text? description:text reference:text "
                                "allocation_rationale:text expected_projection_entry_id:text relationship_state:text",
                                root=True, references="account_id:accounts category_id:categories payee_id:payees"),
    "mixed_use_tags": domain("source_id:text tag_id:text", key="source_id tag_id", references="source_id:mixed_use_sources tag_id:tags"),
    "recurring_schedules": domain("name:text source_kind:text recurrence_unit:text recurrence_interval:integer anchor_date:date "
                                  "start_date:date end_date:date? status:text amount:money total_expense:money? "
                                  "business_use_percentage:percentage? account_id:text category_id:text payee_id:text? "
                                  "description:text reference:text allocation_rationale:text attention_code:text attention_at:instant?",
                                  root=True, references="account_id:accounts category_id:categories payee_id:payees"),
    "schedule_tags": domain("schedule_id:text tag_id:text", key="schedule_id tag_id", references="schedule_id:recurring_schedules tag_id:tags"),
    "recurring_occurrences": domain("schedule_id:text scheduled_date:date source_kind:text occurrence_type:text entry_id:text? "
                                    "mixed_use_source_id:text? created_at:instant", key="schedule_id scheduled_date",
                                    references="schedule_id:recurring_schedules entry_id:entries mixed_use_source_id:mixed_use_sources"),
    "fixed_assets": domain("name:text asset_type:text notes:text acquisition_date:date depreciation_start_date:date "
                           "cost_basis:money salvage_value:money useful_life_quarters:integer depreciation_method:text "
                           "expense_category_id:text? accumulated_depreciation_account_id:text? payee_id:text? reference:text "
                           "source_entry_id:text? retired_date:date? status:text", root=True,
                           references="expense_category_id:categories accumulated_depreciation_account_id:accounts payee_id:payees source_entry_id:entries"),
    "asset_tags": domain("asset_id:text tag_id:text", key="asset_id tag_id", references="asset_id:fixed_assets tag_id:tags"),
    "asset_period_overrides": domain("asset_id:text sequence:integer amount:money reason:text created_at:instant updated_at:instant",
                                     key="asset_id sequence", references="asset_id:fixed_assets"),
    "depreciation_events": domain("asset_id:text sequence:integer entry_id:text scheduled_date:date amount:money created_at:instant",
                                  key="asset_id sequence", references="asset_id:fixed_assets entry_id:entries"),
    "mileage_records": domain("trip_date:date miles:miles vehicle:text start_location:text destination:text business_purpose:text "
                              "notes:text insertion_order:integer", root=True),
    "mileage_tags": domain("mileage_id:text tag_id:text", key="mileage_id tag_id", references="mileage_id:mileage_records tag_id:tags"),
    "documents": domain("stored_filename:text original_filename:text mime_type:text byte_size:integer? sha256:text? "
                        "store_kind:text store_scope_key:text storage_key:text open_url:text", root=True),
    "document_links": domain("document_id:text entry_id:text? mixed_use_source_id:text? position:integer created_at:instant",
                             key="document_id", references="document_id:documents entry_id:entries mixed_use_source_id:mixed_use_sources"),
    "import_batches": domain("source_kind:text format_version:text status:text", root=True),
    "import_records": domain("batch_id:text source_position:integer record_kind:text entry_id:text? mileage_id:text? "
                              "fingerprint:text created_at:instant", key="batch_id source_position",
                              references="batch_id:import_batches entry_id:entries mileage_id:mileage_records"),
    "migration_runs": domain("source_profile_key:text source_workbook_key:text source_kind:text source_backup_version:integer? "
                              "manifest_version:text mapping_version:text source_snapshot_sha256:text extracted_at:instant status:text", root=True),
    "migration_units": domain("run_id:text unit_key:text unit_kind:text unit_sha256:text status:text loaded_at:instant?",
                              key="run_id unit_key", references="run_id:migration_runs"),
    "migration_id_mappings": domain("run_id:text source_record_kind:text source_id:text target_record_kind:text target_id:text "
                                    "mapping_reason:text source_updated_at:instant?", key="run_id source_record_kind source_id",
                                    references="run_id:migration_runs"),
}

SCALES = {"money": 2, "percentage": 5, "miles": 1}


def json_schema():
    """Return a standard JSON Schema for canonical records and manifest."""
    def field_schema(kind):
        nullable = kind.endswith("?")
        kind = kind.rstrip("?")
        result = {"type": {"integer": "integer", "boolean": "boolean"}.get(kind, "string")}
        if kind in SCALES:
            result["pattern"] = rf"^-?(0|[1-9][0-9]*)\.[0-9]{{{SCALES[kind]}}}$"
        if kind in {"date", "instant"}:
            result["format"] = "date" if kind == "date" else "date-time"
        if nullable:
            result["type"] = [result["type"], "null"]
        return result

    records = {
        name: {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": list(spec.fields), "properties": {field: field_schema(kind) for field, kind in spec.fields.items()},
        }} for name, spec in DOMAINS.items()
    }
    records['business_entities']['items']['properties']['currency_code']['enum'] = ['USD', 'CAD']
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Tabula Rasa canonical bookkeeping export v1",
        "type": "object", "additionalProperties": False, "required": ["manifest", "domains"],
        "properties": {
            "domains": {"type": "object", "additionalProperties": False, "required": list(DOMAINS), "properties": records},
            "manifest": {
                "type": "object", "additionalProperties": False,
                "required": ["format", "format_version", "database_schema_version", "entity_id", "snapshot_at",
                             "counts", "content_sha256", "status", "diagnostics", "document_bytes"],
                "properties": {
                    "format": {"const": FORMAT_NAME}, "format_version": {"const": FORMAT_VERSION},
                    "database_schema_version": {"enum": list(SUPPORTED_DATABASE_REVISIONS)}, "entity_id": {"type": "string", "minLength": 1},
                    "snapshot_at": {"type": "string", "format": "date-time"},
                    "counts": {"type": "object", "additionalProperties": False, "required": list(DOMAINS),
                               "properties": {name: {"type": "integer", "minimum": 0} for name in DOMAINS}},
                    "content_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "status": {"enum": ["complete", "complete_with_diagnostics"]},
                    "diagnostics": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False, "required": ["domain", "record", "code"],
                        "properties": {"domain": {"enum": list(DOMAINS)}, "record": {"type": "integer", "minimum": 0},
                                       "code": {"type": "string"}},
                    }},
                    "document_bytes": {"const": "external"},
                },
            },
        },
    }
