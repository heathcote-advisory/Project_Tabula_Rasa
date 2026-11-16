"""Shared account, payee, and tag rules independent of persistence and UI."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from tabula_rasa.errors import StorageValidationError
from tabula_rasa.hierarchy import HierarchyValidationError, validate_two_level_hierarchy


def vehicle_name(value):
    """Keep exact historical spelling/spacing; reject empty vehicle labels."""
    if not isinstance(value, str) or not value.strip():
        raise StorageValidationError('Vehicle name is required.')
    return value


ACCOUNT_TYPES = {
    "Bank",
    "Cash",
    "Credit card",
    "Short-term debt",
    "Equity",
    "Other",
}

ACCOUNT_CLASSIFICATIONS = {"Asset", "Liability", "Equity"}

FIXED_TYPE_CLASSIFICATIONS = {
    "Bank": "Asset",
    "Cash": "Asset",
    "Credit card": "Liability",
    "Short-term debt": "Liability",
    "Equity": "Equity",
}


def clean_text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        text = ""
    else:
        text = str(value).strip()
    if required and not text:
        raise StorageValidationError(f"{field} is required")
    return text


def normalize_payee_name(value: Any) -> str:
    """Normalize payee names for deduplication and reference resolution."""
    return " ".join(
        unicodedata.normalize("NFKC", clean_text(value, "Payee")).split()
    ).casefold()


def normalize_tag_name(value: Any) -> str:
    """Normalize tag names for profile-local deduplication."""
    return " ".join(
        unicodedata.normalize("NFKC", clean_text(value, "Tag name")).split()
    ).casefold()


def normalize_merge_ids(
    target_id: str, source_ids: Sequence[str], label: str
) -> tuple[str, list[str]]:
    """Validate explicit merge identities, preserving the source order."""
    target = clean_text(target_id, f"Target {label}", required=True)
    sources = list(dict.fromkeys(
        clean_text(source, f"Source {label}", required=True) for source in source_ids
    ))
    if not sources:
        raise StorageValidationError(f"Select at least one source {label}")
    if target in sources:
        raise StorageValidationError(f"The target {label} cannot also be a source")
    return target, sources


def validate_hierarchy_records(
    records: Iterable[Mapping[str, Any]],
    *,
    compatibility_field: str,
    item_label: str,
) -> None:
    try:
        validate_two_level_hierarchy(
            records,
            compatibility_field=compatibility_field,
            item_label=item_label,
        )
    except HierarchyValidationError as exc:
        raise StorageValidationError(str(exc)) from exc


def normalize_tag_ids(value: Any) -> list[str]:
    """Return sorted, unique tag IDs from an encoded list or sequence."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise StorageValidationError("Tag IDs are not valid JSON") from exc
    if not isinstance(value, (list, tuple, set)):
        raise StorageValidationError("Tag IDs must be a JSON array")
    normalized: list[str] = []
    for tag_id in value:
        if not isinstance(tag_id, str):
            raise StorageValidationError("Tag IDs must be strings")
        clean_id = tag_id.strip()
        if not clean_id:
            raise StorageValidationError("Tag IDs cannot be blank")
        if clean_id in normalized:
            raise StorageValidationError("Tag IDs must be unique")
        normalized.append(clean_id)
    return sorted(normalized)


def balance_amount(value: Any) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise StorageValidationError("Opening balance must be numeric") from None
    if not amount.is_finite():
        raise StorageValidationError("Opening balance must be finite")
    try:
        return format(amount.quantize(Decimal("0.01")), "f")
    except InvalidOperation:
        raise StorageValidationError("Opening balance exceeds supported precision") from None


def validate_account_classification(
    account_type: str, classification: str
) -> None:
    if classification not in ACCOUNT_CLASSIFICATIONS:
        raise StorageValidationError("Unknown account classification")
    expected = FIXED_TYPE_CLASSIFICATIONS.get(account_type)
    if expected is not None and classification != expected:
        raise StorageValidationError(
            f"{account_type} accounts must be classified as {expected}"
        )


def as_bool(value: Any, field: str = "active") -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text == "true":
        return True
    if text == "false":
        return False
    raise StorageValidationError(f"{field} must be true or false")


__all__ = [
    "ACCOUNT_TYPES",
    "ACCOUNT_CLASSIFICATIONS",
    "FIXED_TYPE_CLASSIFICATIONS",
    "clean_text",
    "normalize_payee_name",
    "normalize_tag_name",
    "normalize_merge_ids",
    "validate_hierarchy_records",
    "normalize_tag_ids",
    "balance_amount",
    "validate_account_classification",
    "as_bool",
]
