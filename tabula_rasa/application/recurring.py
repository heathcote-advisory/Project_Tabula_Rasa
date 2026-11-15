"""Recurring schedule commands and scheduler-neutral occurrence reconciliation."""

from datetime import date
from typing import Any
from uuid import uuid4

from tabula_rasa.errors import StorageNotFoundError, StorageValidationError
from tabula_rasa.ledger_validation import (
    iso_date as _iso_date,
    validate_recurring_provenance as _validate_recurring_provenance,
    validate_unique_recurring_occurrences as _validate_unique_recurring_occurrences,
)
from tabula_rasa.mixed_use import MIXED_USE_SOURCE_TYPE
from tabula_rasa.recurring import (
    _PENDING_INITIAL_LINK,
    _as_date,
    decode_schedule,
    schedule_template,
    validate_schedule_changes,
    latest_current_occurrence_date,
    existing_recurring_dates,
    next_due_for_schedule,
    occurrence_data,
    validate_recurring_occurrence_rows,
    valid_mixed_use_projection,
    validate_mixed_use_projection_parity,
)
from tabula_rasa.recurrence import next_occurrence, occurrences_between
from tabula_rasa.reference_data import clean_text
from tabula_rasa.transaction import utc_now

from .recurring_contracts import RecurringRepository
from .transactions import TransactionService
from .mixed_use import MixedUseService


class RecurringService:
    def __init__(
        self,
        repository: RecurringRepository,
        *,
        transactions: TransactionService,
        mixed_use: MixedUseService,
    ):
        self._repository = repository
        self._transactions = transactions
        self._mixed_use = mixed_use

    def list(self, *, fresh=False):
        snapshot = self._repository.snapshot(fresh=fresh)
        return self._schedules(snapshot)

    @staticmethod
    def _schedules(snapshot):
        projections = {
            str(record.get("id", "")): record for record in snapshot.transactions
        }
        records = dict(
            transactions=snapshot.transactions,
            mixed_sources=snapshot.mixed_sources,
            projections=projections,
        )
        schedules = []
        for stored in snapshot.schedules:
            schedule = decode_schedule(stored)
            try:
                schedule["next_due_date"] = next_due_for_schedule(schedule, **records)
            except (StorageValidationError, ValueError, TypeError):
                schedule["next_due_date"] = ""
            try:
                latest = latest_current_occurrence_date(
                    schedule["id"], schedule.get("source_kind"), **records
                )
            except (StorageValidationError, ValueError, TypeError):
                latest = None
            schedule["latest_current_occurrence_date"] = (
                latest.isoformat() if latest else ""
            )
            schedules.append(schedule)
        return sorted(
            schedules, key=lambda item: (item["start_date"], item["id"]), reverse=True
        )

    def detail(self, schedule_id, *, fresh=False):
        identity = clean_text(schedule_id, 'Schedule ID', required=True)
        snapshot = self._repository.snapshot(fresh=fresh)
        schedule = next((row for row in self._schedules(snapshot) if row['id'] == identity), None)
        if schedule is None:
            raise StorageNotFoundError('Recurring schedule no longer exists')
        return dict(can_delete=self._repository.can_delete(identity), schedule=schedule,
                    transactions=[row for row in snapshot.transactions if row.get('recurring_schedule_id') == identity],
                    mixed_sources=[row for row in snapshot.mixed_sources if row.get('recurring_schedule_id') == identity])

    def get(self, schedule_id, *, fresh=False):
        identity = clean_text(schedule_id, "Schedule ID", required=True)
        for record in self.list(fresh=fresh):
            if record["id"] == identity:
                return record
        raise StorageNotFoundError("Recurring schedule no longer exists")

    @staticmethod
    def _template(data, references, **kwargs):
        return schedule_template(
            data,
            account_lookup=references.accounts,
            category_lookup=references.categories,
            payee_lookup=references.payees,
            tag_lookup=references.tags,
            **kwargs,
        )

    def create(self, **data):
        identity = clean_text(
            data.get("schedule_id", data.get("id")), "Schedule ID"
        ) or str(uuid4())
        return self._repository.create(
            identity,
            build=lambda references, current: self._template(
                data, references, schedule_id=identity
            ),
        )

    def update(
        self,
        schedule_id,
        *,
        expected_schedule=None,
        expected=None,
        _transition=None,
        **changes,
    ):
        identity = clean_text(schedule_id, "Schedule ID", required=True)

        def build(references, current):
            validate_schedule_changes(current, changes, transition=_transition)
            return self._template(
                {**current, **changes},
                references,
                schedule_id=identity,
                created_at=current["created_at"],
                updated_at=utc_now(),
            )

        return self._repository.update(
            identity, expected=expected_schedule or expected, build=build
        )

    def pause(self, schedule_id, *, expected_schedule=None):
        return self.update(
            schedule_id,
            expected_schedule=expected_schedule,
            _transition="pause",
            status="paused",
        )

    def resume(self, schedule_id, *, expected_schedule=None):
        return self.update(
            schedule_id,
            expected_schedule=expected_schedule,
            _transition="resume",
            status="active",
        )

    def end(self, schedule_id, *, expected_schedule=None):
        return self.update(
            schedule_id,
            expected_schedule=expected_schedule,
            _transition="end",
            status="ended",
            end_date=date.today(),
            attention_code="",
            attention_at="",
        )

    def delete(self, schedule_id, *, expected_schedule=None):
        """Remove an unused template and detach its sole original occurrence."""
        identity = clean_text(schedule_id, "Schedule ID", required=True)
        return self._repository.delete(identity, expected=expected_schedule)

    def create_expense(self, **data):
        return self._repository.create_expense(data)

    def create_mixed_expense(self, **data):
        return self._repository.create_expense(data, mixed=True)

    def from_transaction(self, transaction_id, *, expected_transaction=None, **changes):
        return self._repository.convert_expense(
            transaction_id, changes, expected=expected_transaction
        )

    def from_mixed_use(self, source_id, *, expected_source=None, **changes):
        return self._repository.convert_expense(
            source_id, changes, expected=expected_source, mixed=True
        )

    def reconcile(self, *, as_of: date | str | None = None, schedule_id=None,
                  max_occurrences=None, raise_on_error=False):
        """Run independently of page loads; the caller supplies its cutoff date."""
        cutoff = _iso_date(as_of if as_of is not None else date.today())
        return self._repository.reconcile(
            lambda snapshot: self._reconcile(snapshot, as_of=cutoff, schedule_id=schedule_id,
                                            max_occurrences=max_occurrences, raise_on_error=raise_on_error)
        )

    def _clear_attention(self, schedule_id):
        try:
            self._repository.attention(schedule_id, "")
        except Exception:
            pass

    def _reconcile(self, snapshot, *, as_of, schedule_id=None,
                   max_occurrences=None, raise_on_error=False) -> dict[str, Any]:
        if max_occurrences is not None and not 1 <= max_occurrences <= 100:
            raise StorageValidationError('Invalid occurrence budget.')
        cutoff = _iso_date(as_of or date.today())
        generated: list[dict[str, str]] = []
        attention: dict[str, str] = {}
        transactions = snapshot.transactions
        mixed_sources = snapshot.mixed_sources
        transactions_by_schedule: dict[str, list[Any]] = {}
        mixed_sources_by_schedule: dict[str, list[Any]] = {}
        for row in transactions:
            transactions_by_schedule.setdefault(
                str(row.get("recurring_schedule_id", "") or "").strip(), []
            ).append(row)
        for row in mixed_sources:
            mixed_sources_by_schedule.setdefault(
                str(row.get("recurring_schedule_id", "") or "").strip(), []
            ).append(row)
        projections = {str(row.get("id", "")): row for row in transactions}
        accounts = snapshot.references.accounts
        categories = snapshot.references.categories
        payees = snapshot.references.payees
        tags = snapshot.references.tags
        schedules = sorted(
            [decode_schedule(record) for record in snapshot.schedules],
            key=lambda item: (item["start_date"], item["id"]),
            reverse=True,
        )
        schedule_values = {
            schedule["id"]: schedule for schedule in schedules if schedule["id"]
        }
        schedule_ids = [schedule["id"] for schedule in schedules if schedule["id"]]
        if len(schedule_ids) != len(set(schedule_ids)):
            raise StorageValidationError("Recurring schedule IDs are duplicated")
        for row in (*transactions, *mixed_sources):
            if (
                str(row.get("recurring_schedule_id", "") or "").strip()
                not in schedule_ids
            ):
                _validate_recurring_provenance(row, "Recurring occurrence")
        mixed_sources_by_id = {str(row.get("id", "")): row for row in mixed_sources}
        for schedule in schedules:
            if schedule_id is not None and schedule['id'] != schedule_id:
                continue
            expected_schedule = {key: schedule[key] for key in ("id", "baseline") if key in schedule}
            try:
                if not schedule["id"]:
                    raise StorageValidationError("Schedule ID is missing")
                _validate_unique_recurring_occurrences(
                    [row for row in transactions_by_schedule.get(schedule["id"], [])],
                    "Transactions",
                )
                _validate_unique_recurring_occurrences(
                    [row for row in mixed_sources_by_schedule.get(schedule["id"], [])],
                    "Mixed-use expenses",
                )
                validate_recurring_occurrence_rows(
                    transactions_by_schedule.get(schedule["id"], []),
                    schedule_values,
                    "Transaction",
                    mixed_source=False,
                    filter_schedule_id=schedule["id"],
                )
                validate_recurring_occurrence_rows(
                    mixed_sources_by_schedule.get(schedule["id"], []),
                    schedule_values,
                    "Mixed-use source",
                    mixed_source=True,
                    filter_schedule_id=schedule["id"],
                )
                for source_record in mixed_sources_by_schedule.get(schedule["id"], []):
                    source = source_record
                    projection = projections.get(
                        str(source.get("linked_transaction_id", "") or "")
                    )
                    valid_mixed_use_projection(source, projection)
                    validate_mixed_use_projection_parity(source, projection)
                for projection_record in transactions_by_schedule.get(
                    schedule["id"], []
                ):
                    projection = projection_record
                    if projection.get("source_type") != MIXED_USE_SOURCE_TYPE:
                        continue
                    source = mixed_sources_by_id.get(
                        str(projection.get("source_id", "") or "")
                    )
                    valid_mixed_use_projection(source or {}, projection)
                    validate_mixed_use_projection_parity(source or {}, projection)
                schedule = schedule_template(
                    schedule,
                    schedule_id=schedule["id"],
                    created_at=schedule.get("created_at"),
                    updated_at=schedule.get("updated_at"),
                    account_lookup=accounts,
                    category_lookup=categories,
                    payee_lookup=payees,
                    tag_lookup=tags,
                )
                if (
                    schedule.get("attention_code") == _PENDING_INITIAL_LINK
                    and schedule["status"] != "ended"
                ):
                    linked_transactions = transactions_by_schedule.get(
                        schedule["id"], []
                    )
                    linked_mixed_sources = mixed_sources_by_schedule.get(
                        schedule["id"], []
                    )
                    linked = (
                        bool(linked_mixed_sources) and bool(linked_transactions)
                        if schedule.get("source_kind") == "mixed_use"
                        else bool(linked_transactions)
                    )
                    if linked:
                        # The link may have committed even when the original
                        # response was uncertain. The validated occurrence
                        # rows make it safe to clear the temporary marker.
                        self._clear_attention(schedule["id"])
                    else:
                        # A conversion creates the schedule before linking its
                        # existing occurrence. Keep this temporary schedule out
                        # of reconciliation, but leave an actionable marker
                        # visible after a session or server restart.
                        attention[schedule["id"]] = _PENDING_INITIAL_LINK
                        continue
                if schedule["status"] != "active":
                    self._clear_attention(schedule["id"])
                    continue
                end = (
                    min(cutoff, schedule["end_date"])
                    if schedule.get("end_date")
                    else cutoff
                )
                existing = existing_recurring_dates(
                    schedule["id"],
                    schedule.get("source_kind", "transaction"),
                    transactions=transactions,
                    mixed_sources=mixed_sources,
                    projections=projections,
                )
                latest = latest_current_occurrence_date(
                    schedule["id"],
                    schedule.get("source_kind"),
                    transactions=transactions,
                    mixed_sources=mixed_sources,
                    projections=projections,
                )
                candidate_start = _as_date(schedule["start_date"])
                if latest is not None:
                    candidate_start = next_occurrence(
                        _as_date(schedule["anchor_date"]),
                        schedule["recurrence_unit"],
                        int(schedule["recurrence_interval"]),
                        latest,
                    )
                for scheduled in occurrences_between(
                    _as_date(schedule["anchor_date"]),
                    schedule["recurrence_unit"],
                    int(schedule["recurrence_interval"]),
                    candidate_start,
                    _as_date(end),
                ):
                    key = scheduled.isoformat()
                    if key in existing:
                        continue
                    if max_occurrences is not None and len(generated) >= max_occurrences:
                        break
                    if schedule.get("source_kind") == "mixed_use":
                        persisted_source = self._mixed_use.create_occurrence(
                            expected_schedule=expected_schedule,
                            **occurrence_data(schedule, scheduled, role="generated")
                        )
                        generated.append(persisted_source)
                    else:
                        record = self._transactions.create_occurrence(
                            expected_schedule=expected_schedule,
                            **occurrence_data(schedule, scheduled, role="generated")
                        )
                        generated.append(record)
                    existing.add(key)
            except Exception as exc:  # one bad schedule must not block others
                code = (
                    "validation"
                    if isinstance(exc, StorageValidationError)
                    else "storage"
                )
                failed_schedule_id = schedule.get("id", "")
                if failed_schedule_id:
                    attention[failed_schedule_id] = code
                try:
                    if not failed_schedule_id:
                        continue
                    self._repository.attention(failed_schedule_id, code)
                except Exception:
                    pass
                if raise_on_error:
                    raise
            else:
                self._clear_attention(schedule["id"])
        return {"generated": generated, "attention": attention}
