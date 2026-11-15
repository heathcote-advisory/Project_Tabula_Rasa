"""Scheduler-neutral schedule and occurrence repository contracts."""

from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from tabula_rasa.transaction import TransactionReferences
from .contracts import ExpectedRecord


@dataclass(frozen=True)
class RecurringSnapshot:
    schedules: Sequence[Mapping[str, Any]]
    transactions: Sequence[Mapping[str, Any]]
    mixed_sources: Sequence[Mapping[str, Any]]
    references: TransactionReferences | None = None


BuildSchedule = Callable[
    [TransactionReferences, Mapping[str, Any] | None], Mapping[str, Any]
]


class RecurringRepository(Protocol):
    """One entity's schedules and generated occurrences.

    Reconciliation supplies fresh records/references in the adapter's mutation
    boundary. Stable schedule and occurrence identities survive repeated calls.
    Expected schedule comparisons exclude derived display/attention metadata.
    Initial creation/conversion operations preserve paired source relationships
    and detectable partial results; adapter recovery is bounded.
    """

    def snapshot(self, *, fresh: bool = False) -> RecurringSnapshot: ...
    def create(self, schedule_id: str, *, build: BuildSchedule) -> dict[str, Any]: ...
    def update(
        self, schedule_id: str, *, expected: ExpectedRecord | None, build: BuildSchedule
    ) -> dict[str, Any]: ...
    def can_delete(self, schedule_id: str) -> bool: ...
    def delete(self, schedule_id: str, *, expected: ExpectedRecord | None) -> None: ...
    def reconcile(
        self, process: Callable[[RecurringSnapshot], dict[str, Any]]
    ) -> dict[str, Any]: ...
    def attention(self, schedule_id: str, code: str) -> None: ...
    def create_expense(
        self, data: Mapping[str, Any], *, mixed: bool = False
    ) -> dict[str, Any]: ...
    def convert_expense(
        self,
        identity: str,
        changes: Mapping[str, Any],
        *,
        expected: ExpectedRecord | None,
        mixed: bool = False,
    ) -> dict[str, Any]: ...
