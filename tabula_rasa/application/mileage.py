"""Business-mileage rules and workflows, independent of UI and persistence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Any
from uuid import uuid4

from tabula_rasa.errors import StorageNotFoundError

from tabula_rasa.quickbooks_mileage_import import (
    mileage_fingerprint,
    missing_required_columns,
    normalize_rows,
)

from .mileage_contracts import CreateTag, MileageRepository
from .contracts import ExpectedRecord


class MileageValidationError(ValueError):
    """A safe, user-displayable invalid mileage request or record."""


@dataclass(frozen=True)
class MileageTag:
    id: str
    name: str
    active: bool
    created_at: str = ""


@dataclass(frozen=True)
class MileageTrip:
    id: str
    trip_date: str
    miles: str
    vehicle: str
    start_location: str
    destination: str
    business_purpose: str
    notes: str
    tag_ids: tuple[str, ...]
    created_at: str
    tags: tuple[str, ...] = ()
    baseline: str | None = None
    vehicle_id: str | None = None
    original_vehicle: str | None = None

    def as_record(self) -> dict[str, Any]:
        """Return portable field values for existing table/form consumers."""
        return {
            "id": self.id,
            "trip_date": self.trip_date,
            "miles": self.miles,
            "vehicle": self.vehicle,
            "vehicle_id": self.vehicle_id,
            "start_location": self.start_location,
            "destination": self.destination,
            "business_purpose": self.business_purpose,
            "notes": self.notes,
            "tag_ids": list(self.tag_ids),
            "created_at": self.created_at,
            "tags": list(self.tags),
            **({"baseline": self.baseline} if self.baseline is not None else {}),
        }


@dataclass(frozen=True)
class MileageImportPreview:
    rows: tuple[Mapping[str, str], ...]
    errors: tuple[str, ...]
    duplicate_rows: tuple[int, ...]
    personal_rows: tuple[int, ...]
    unreviewed_rows: tuple[int, ...]

    @property
    def summary(self) -> dict[str, int]:
        return {
            "ready": len(self.rows),
            "duplicates": len(self.duplicate_rows),
            "personal_skipped": len(self.personal_rows),
            "unreviewed_skipped": len(self.unreviewed_rows),
        }

    def as_record(self) -> dict[str, Any]:
        return {
            "rows": [dict(row) for row in self.rows],
            "errors": list(self.errors),
            "duplicate_rows": list(self.duplicate_rows),
            "personal_rows": list(self.personal_rows),
            "unreviewed_rows": list(self.unreviewed_rows),
            "summary": self.summary,
        }


def _text(value: Any, field: str, *, required: bool = False) -> str:
    text = (
        ""
        if value is None or isinstance(value, float) and math.isnan(value)
        else str(value).strip()
    )
    if required and not text:
        raise MileageValidationError(f"{field} is required")
    return text


def mileage_date(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value, "Date", required=True)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise MileageValidationError("Date must use YYYY-MM-DD format") from None


def _miles(value: Any) -> str:
    try:
        miles = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MileageValidationError("Miles must be numeric") from None
    if not miles.is_finite() or miles <= 0:
        raise MileageValidationError("Miles must be greater than zero")
    return format(miles.quantize(Decimal("0.1")), "f")


def _tag_ids(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple, set)):
        raise MileageValidationError("Tag IDs must be a JSON array")
    normalized: list[str] = []
    for tag_id in value:
        if not isinstance(tag_id, str):
            raise MileageValidationError("Tag IDs must be strings")
        clean_id = tag_id.strip()
        if not clean_id:
            raise MileageValidationError("Tag IDs cannot be blank")
        if clean_id in normalized:
            raise MileageValidationError("Tag IDs must be unique")
        normalized.append(clean_id)
    return tuple(sorted(normalized))


def _now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def prepare_mileage_trip(
    data: Mapping[str, Any],
    tags: Sequence[MileageTag],
    *,
    trip_id: str | None = None,
    created_at: str | None = None,
    original_tag_ids: Sequence[str] = (),
    validate_tags: bool = True,
) -> MileageTrip:
    """The single owner of mileage field and existing Tag-reference rules."""
    trip_date = mileage_date(data.get("trip_date"))
    miles = _miles(data.get("miles"))
    vehicle = _text(data.get("vehicle"), "Vehicle")
    start = _text(data.get("start_location"), "Starting location", required=True)
    destination = _text(data.get("destination"), "Destination", required=True)
    purpose = _text(data.get("business_purpose"), "Business purpose", required=True)
    notes = _text(data.get("notes"), "Notes")
    ids = _tag_ids(data.get("tag_ids", ())) if validate_tags else ()
    tag_by_id = {tag.id: tag for tag in tags}
    for tag_id in ids:
        tag = tag_by_id.get(tag_id)
        if tag is None:
            raise MileageValidationError("Tag does not exist")
        if not tag.active and tag_id not in original_tag_ids:
            raise MileageValidationError("Tag is inactive")
    return MileageTrip(
        id=trip_id or str(uuid4()),
        trip_date=trip_date,
        miles=miles,
        vehicle=vehicle,
        vehicle_id=_text(data.get('vehicle_id'), 'Vehicle ID') or None,
        start_location=start,
        destination=destination,
        business_purpose=purpose,
        notes=notes,
        tag_ids=ids,
        created_at=created_at or _now(),
        tags=tuple(tag_by_id[tag_id].name for tag_id in ids),
    )


def _apply_new_tag(
    trips: Sequence[MileageTrip], name: Any, create_tag: CreateTag
) -> tuple[MileageTrip, ...]:
    name = _text(name, "New tag")
    if not name:
        return tuple(trips)
    tag = create_tag(name)
    updated = []
    for trip in trips:
        names = dict(zip(trip.tag_ids, trip.tags))
        names[tag.id] = tag.name
        ids = tuple(sorted(names))
        updated.append(
            replace(trip, tag_ids=ids, tags=tuple(names[item] for item in ids))
        )
    return tuple(updated)


def _validated_trips(
    trips: Sequence[MileageTrip],
    tags: Sequence[MileageTag],
    *,
    start: str | None = None,
    end: str | None = None,
) -> tuple[MileageTrip, ...]:
    result = []
    seen: set[str] = set()
    for trip in trips:
        trip_id = _text(trip.id, "Mileage ID", required=True)
        if trip_id in seen:
            raise MileageValidationError("Mileage IDs must be unique")
        seen.add(trip_id)
        # Stored inactive Tags remain valid; only adding one is prohibited.
        normalized = prepare_mileage_trip(
            trip.as_record(),
            tags,
            trip_id=trip_id,
            created_at=trip.created_at,
            original_tag_ids=trip.tag_ids,
            validate_tags=False,
        )
        if start and normalized.trip_date < start or end and normalized.trip_date > end:
            continue
        result.append(
            replace(prepare_mileage_trip(
                trip.as_record(),
                tags,
                trip_id=trip_id,
                created_at=trip.created_at,
                original_tag_ids=trip.tag_ids,
            ), baseline=trip.baseline, vehicle=trip.vehicle, original_vehicle=trip.original_vehicle)
        )
    # Preserve backend insertion order for equal timestamps (new return first).
    return tuple(
        sorted(result, key=lambda trip: (trip.trip_date, trip.created_at), reverse=True)
    )


def _quickbooks_preview(
    rows: Sequence[Mapping[str, Any]], existing: Sequence[MileageTrip]
) -> MileageImportPreview:
    missing = missing_required_columns({str(column) for row in rows for column in row})
    if missing:
        return MileageImportPreview(
            (),
            ("QuickBooks mileage export is missing columns: " + ", ".join(missing),),
            (),
            (),
            (),
        )
    normalized = normalize_rows(rows)
    known = {mileage_fingerprint(item.as_record()) for item in existing}
    known.update(mileage_fingerprint({**item.as_record(), 'vehicle': item.original_vehicle})
                 for item in existing if item.original_vehicle is not None)
    ready = []
    duplicates = []
    for source in normalized["rows"]:
        item = dict(source)
        number = int(item.pop("_source_row"))
        identity = mileage_fingerprint(item)
        if identity in known:
            duplicates.append(number)
        else:
            known.add(identity)
            ready.append(item)
    return MileageImportPreview(
        tuple(ready),
        tuple(normalized["errors"]),
        tuple(duplicates),
        tuple(normalized["personal_rows"]),
        tuple(normalized["unreviewed_rows"]),
    )


class MileageService:
    """Business mileage and import decisions for the explicitly supplied profile."""

    def __init__(self, repository: MileageRepository) -> None:
        self._repository = repository

    def list_tags(self) -> tuple[MileageTag, ...]:
        return tuple(self._repository.list_tags())

    def list_trips(
        self,
        *,
        start: date | str | None = None,
        end: date | str | None = None,
        fresh: bool = False,
    ) -> tuple[MileageTrip, ...]:
        start_text = mileage_date(start) if start is not None else None
        end_text = mileage_date(end) if end is not None else None
        tags = self._repository.list_tags(fresh=fresh)
        return _validated_trips(
            self._repository.list_trips(fresh=fresh),
            tags,
            start=start_text,
            end=end_text,
        )

    def create_trip(self, **data: Any) -> MileageTrip:
        def prepare(
            tags: Sequence[MileageTag], create_tag: CreateTag
        ) -> tuple[MileageTrip, ...]:
            trip = prepare_mileage_trip(data, tags)
            return _apply_new_tag((trip,), data.get("new_tag_name"), create_tag)

        return self._repository.add_trips(prepare)[0]

    def get_trip(self, trip_id: str) -> MileageTrip:
        for trip in self.list_trips(fresh=True):
            if trip.id == trip_id:
                return trip
        raise StorageNotFoundError("The mileage trip no longer exists.")

    def create_round_trip(
        self,
        *,
        return_miles: Any | None = None,
        **data: Any,
    ) -> tuple[MileageTrip, ...]:
        def prepare(
            tags: Sequence[MileageTag], create_tag: CreateTag
        ) -> tuple[MileageTrip, ...]:
            timestamp = _now()
            outbound = prepare_mileage_trip(data, tags, created_at=timestamp)
            return_data = {
                **data,
                "start_location": data.get("destination"),
                "destination": data.get("start_location"),
            }
            if return_miles is not None:
                return_data["miles"] = return_miles
            returned = prepare_mileage_trip(return_data, tags, created_at=timestamp)
            return _apply_new_tag(
                (outbound, returned), data.get("new_tag_name"), create_tag
            )

        return tuple(self._repository.add_trips(prepare))

    def edit_trip(
        self, trip_id: str, *, expected: ExpectedRecord | None = None, **changes: Any,
    ) -> MileageTrip:
        if 'vehicle' in changes and 'vehicle_id' not in changes:
            changes['vehicle_id'] = None
        def prepare(
            current: MileageTrip,
            tags: Sequence[MileageTag],
            create_tag: CreateTag,
        ) -> MileageTrip:
            updated = prepare_mileage_trip(
                {**current.as_record(), **changes},
                tags,
                trip_id=current.id,
                created_at=current.created_at,
                original_tag_ids=current.tag_ids,
            )
            return _apply_new_tag((updated,), changes.get("new_tag_name"), create_tag)[
                0
            ]

        return self._repository.update_trip(
            trip_id, prepare, **({"expected": expected} if expected is not None else {}),
        )

    def delete_trip(self, trip_id: str, *, expected: ExpectedRecord | None = None) -> None:
        self._repository.delete_trip(
            trip_id, **({"expected": expected} if expected is not None else {}),
        )

    def preview_quickbooks_mileage(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        fresh: bool = False,
    ) -> MileageImportPreview:
        return _quickbooks_preview(list(rows), self.list_trips(fresh=fresh))

    def import_quickbooks_mileage(
        self,
        rows: Iterable[Mapping[str, Any]],
    ) -> tuple[MileageTrip, ...]:
        source_rows = list(rows)

        def prepare(
            existing: Sequence[MileageTrip],
            tags: Sequence[MileageTag],
        ) -> tuple[MileageTrip, ...]:
            preview = _quickbooks_preview(source_rows, _validated_trips(existing, tags))
            if preview.errors:
                raise MileageValidationError("\n".join(preview.errors))
            return tuple(prepare_mileage_trip(item, tags) for item in preview.rows)

        return tuple(self._repository.import_trips(prepare))
