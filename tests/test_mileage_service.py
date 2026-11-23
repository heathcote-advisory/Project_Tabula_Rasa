"""Mileage business contracts run against small fakes without Sheets or UI."""

from dataclasses import replace
from typing import Callable

import pytest

from tabula_rasa.application.mileage import (
    MileageService,
    MileageTag,
    MileageTrip,
    MileageValidationError,
)
from tests.application_import_probe import assert_application_runs_without_infrastructure


class FakeMileageRepository:
    """A profile's records; preparation runs at the fake mutation boundary."""

    def __init__(self) -> None:
        self.trips: list[MileageTrip] = []
        self.tags: list[MileageTag] = []
        self.calls: list[str] = []
        self.before_write: Callable[[], None] = lambda: None
        self.error: Exception | None = None

    def list_trips(self, *, fresh=False):
        return tuple(self.trips)

    def list_tags(self, *, fresh=False):
        return tuple(self.tags)

    def _begin(self, operation):
        self.calls.append(operation)
        if self.error:
            raise self.error
        self.before_write()

    def _create_tag(self, name):
        self.calls.append("create_tag")
        tag = MileageTag(f"tag-{len(self.tags)}", name, True)
        self.tags.append(tag)
        return tag

    def add_trips(self, prepare):
        self._begin("add")
        trips = tuple(prepare(self.list_tags(), self._create_tag))
        self.trips.extend(reversed(trips))
        return trips

    def update_trip(self, trip_id, prepare):
        self._begin("update")
        index = next(
            index for index, item in enumerate(self.trips) if item.id == trip_id
        )
        trip = prepare(self.trips[index], self.list_tags(), self._create_tag)
        self.trips[index] = trip
        return trip

    def delete_trip(self, trip_id):
        self._begin("delete")
        self.trips = [item for item in self.trips if item.id != trip_id]

    def import_trips(self, prepare):
        self._begin("import")
        trips = tuple(prepare(self.list_trips(), self.list_tags()))
        self.trips.extend(trips)
        return trips


TRIP = {
    "trip_date": "2026-07-20",
    "miles": "12.34",
    "vehicle": " Blue car ",
    "start_location": " Office ",
    "destination": "Client site",
    "business_purpose": "Project meeting",
    "notes": "Parking receipt saved",
}
QUICKBOOKS = {
    "Date": "07/20/2026",
    "Type": "Business",
    "Trip Purpose": "Project meeting",
    "Vehicle": "Blue car",
    "Start Address": "Office",
    "End Address": "Client site",
    "Distance": "12.3",
    "Deduction": "8.68",
    "Logging Method": "Auto-tracked",
}


def test_mileage_crud_filtering_and_partial_edit_without_sheets():
    repository = FakeMileageRepository()
    service = MileageService(repository)
    first = service.create_trip(**TRIP)
    second = service.create_trip(**{**TRIP, "trip_date": "2026-07-21", "miles": 2})
    assert first.miles == "12.3" and first.vehicle == "Blue car"
    assert [item.id for item in service.list_trips()] == [second.id, first.id]
    assert service.list_trips(end="2026-07-20") == (first,)
    updated = service.edit_trip(first.id, miles=14.2, destination="Updated client")
    assert updated.miles == "14.2" and updated.created_at == first.created_at
    assert updated.business_purpose == first.business_purpose
    service.delete_trip(second.id)
    assert service.list_trips() == (updated,)
    assert repository.calls == ["add", "add", "update", "delete"]


def test_round_trip_validates_both_legs_before_creating_tag():
    repository = FakeMileageRepository()
    service = MileageService(repository)
    with pytest.raises(MileageValidationError, match="greater than zero"):
        service.create_round_trip(**TRIP, return_miles=0, new_tag_name="Client")
    assert repository.trips == [] and repository.tags == []
    outbound, returned = service.create_round_trip(
        **TRIP, return_miles=13.1, new_tag_name="Client"
    )
    assert outbound.id != returned.id
    assert outbound.created_at == returned.created_at
    assert (outbound.miles, returned.miles) == ("12.3", "13.1")
    assert (returned.start_location, returned.destination) == ("Client site", "Office")
    assert outbound.tag_ids == returned.tag_ids == ("tag-0",)
    assert service.list_trips() == (returned, outbound)
    assert repository.calls == ["add", "add", "create_tag"]


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"miles": "nan"}, "Miles must be greater than zero"),
        ({"business_purpose": " "}, "Business purpose is required"),
        ({"destination": ""}, "Destination is required"),
        ({"trip_date": "07/20/2026"}, "Date must use YYYY-MM-DD"),
        ({"tag_ids": ["unknown"]}, "Tag does not exist"),
    ],
)
def test_invalid_mileage_never_persists_trip_or_inline_tag(changes, message):
    repository = FakeMileageRepository()
    with pytest.raises(MileageValidationError, match=message):
        MileageService(repository).create_trip(
            **{**TRIP, **changes}, new_tag_name="Inline"
        )
    assert repository.trips == [] and repository.tags == []


def test_edit_uses_fresh_trip_and_tag_references_at_boundary():
    repository = FakeMileageRepository()
    repository.tags = [MileageTag("client", "Client", True)]
    service = MileageService(repository)
    trip = service.create_trip(**TRIP, tag_ids=["client"])

    def changed_since_page_read():
        repository.trips[0] = replace(repository.trips[0], vehicle="Current car")
        repository.tags[0] = replace(repository.tags[0], active=False)

    repository.before_write = changed_since_page_read
    updated = service.edit_trip(trip.id, notes="Edited")
    assert updated.vehicle == "Current car"
    assert updated.tag_ids == ("client",)  # Existing inactive Tags are retained.
    with pytest.raises(MileageValidationError, match="Tag is inactive"):
        service.create_trip(**TRIP, tag_ids=["client"])
    repository.tags.clear()
    repository.before_write = lambda: None
    with pytest.raises(MileageValidationError, match="Tag does not exist"):
        service.edit_trip(trip.id, notes="Should fail")
    assert repository.trips[0].notes == "Edited"


def test_two_profile_trip_tag_and_import_state_stays_separate():
    north, south = FakeMileageRepository(), FakeMileageRepository()
    north.tags = [MileageTag("north-client", "Client", True)]
    south.tags = [MileageTag("south-client", "Client", True)]
    north_service, south_service = MileageService(north), MileageService(south)
    north_trip = north_service.create_trip(**TRIP, tag_ids=["north-client"])
    assert south_service.list_trips() == ()
    with pytest.raises(MileageValidationError, match="Tag does not exist"):
        south_service.create_trip(**TRIP, tag_ids=["north-client"])
    assert (
        north_service.preview_quickbooks_mileage([QUICKBOOKS]).summary["duplicates"]
        == 1
    )
    assert south_service.preview_quickbooks_mileage([QUICKBOOKS]).summary["ready"] == 1
    imported = south_service.import_quickbooks_mileage([QUICKBOOKS])
    assert len(imported) == 1 and imported[0].tag_ids == ()
    north_service.delete_trip(north_trip.id)
    assert len(south_service.list_trips()) == 1


def test_quickbooks_classification_errors_and_fresh_duplicate_boundary():
    repository = FakeMileageRepository()
    service = MileageService(repository)
    personal = {**QUICKBOOKS, "Type": "Personal", "Trip Purpose": ""}
    unreviewed = {**QUICKBOOKS, "Type": "", "Trip Purpose": ""}
    invalid = {**QUICKBOOKS, "Trip Purpose": ""}
    blocked = service.preview_quickbooks_mileage(
        [QUICKBOOKS, personal, unreviewed, invalid]
    )
    assert blocked.summary == {
        "ready": 1,
        "duplicates": 0,
        "personal_skipped": 1,
        "unreviewed_skipped": 1,
    }
    with pytest.raises(MileageValidationError, match="Trip Purpose is required"):
        service.import_quickbooks_mileage([QUICKBOOKS, invalid])
    assert repository.trips == []
    preview = service.preview_quickbooks_mileage(
        [QUICKBOOKS, QUICKBOOKS, personal, unreviewed]
    )
    assert preview.duplicate_rows == (3,)
    assert preview.personal_rows == (4,) and preview.unreviewed_rows == (5,)
    # A supported concurrent app write between preview and import is observed.
    existing = service.create_trip(**TRIP)
    assert service.import_quickbooks_mileage([QUICKBOOKS]) == ()
    assert service.list_trips() == (existing,)
    assert service.preview_quickbooks_mileage([{"Date": "2026-07-20"}]).errors


@pytest.mark.parametrize("method", ["create", "edit", "delete", "import"])
def test_backend_failures_propagate_without_service_retry(method):
    repository = FakeMileageRepository()
    service = MileageService(repository)
    trip = service.create_trip(**TRIP)
    failure = RuntimeError("Synthetic repository unavailable")
    repository.error = failure
    operations = {
        "create": lambda: service.create_trip(**TRIP),
        "edit": lambda: service.edit_trip(trip.id, notes="Updated"),
        "delete": lambda: service.delete_trip(trip.id),
        "import": lambda: service.import_quickbooks_mileage([QUICKBOOKS]),
    }
    with pytest.raises(RuntimeError) as raised:
        operations[method]()
    assert raised.value is failure
    assert len(repository.calls) == 2
    assert repository.trips == [trip]


def test_mileage_application_imports_without_storage_ui_maps_or_google():
    assert_application_runs_without_infrastructure("""
from tabula_rasa.application.mileage import MileageService
class Repository:
    def list_tags(self, **kwargs): return ()
    def list_trips(self, **kwargs): return ()
    def add_trips(self, prepare): return prepare((), lambda name: None)
service = MileageService(Repository())
trip = service.create_trip(trip_date='2026-07-20', miles='2', start_location='A',
                           destination='B', business_purpose='Meeting')
assert trip.miles == '2.0'
assert service.list_trips() == ()
""")
