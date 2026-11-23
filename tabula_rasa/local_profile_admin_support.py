"""Shared, non-rendering helpers for local profile administration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tabula_rasa.storage import (
    GoogleSheetsStorage,
    StorageAuthenticationError,
    StorageError,
    StorageInitializationError,
    StorageValidationError,
    StorageWorkbookAccessError,
)


def masked_spreadsheet_id(spreadsheet_id: object) -> str:
    value = str(spreadsheet_id)
    return f"…{value[-8:]}" if len(value) > 8 else value


def initialize_candidate_storage(
    settings: Mapping[str, Any],
) -> GoogleSheetsStorage:
    candidate_storage = GoogleSheetsStorage.from_secrets(settings)
    try:
        candidate_storage.ensure_defaults()
    except (
        StorageAuthenticationError,
        StorageWorkbookAccessError,
        StorageInitializationError,
        StorageValidationError,
    ):
        raise
    except StorageError as error:
        raise StorageInitializationError(
            "The workbook opened, but default accounts and categories "
            "could not be initialized"
        ) from error
    return candidate_storage
