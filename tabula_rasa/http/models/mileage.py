from typing import Literal

from pydantic import Field, field_validator

from .accounts import DecimalString
from .common import DTO
from .mutations import ProtectedMutation


class MileageChanges(DTO):
    trip_date: str
    miles: DecimalString
    vehicle: str = ''
    vehicle_id: str | None = None
    start_location: str
    destination: str
    business_purpose: str
    notes: str = ''
    tag_ids: list[str] = Field(default_factory=list)
    new_tag_name: str = ''


class MileageCreate(MileageChanges):
    round_trip: bool = False
    return_miles: DecimalString | None = None


class Mileage(DTO):
    id: str
    trip_date: str
    miles: DecimalString
    vehicle: str
    vehicle_id: str | None = None
    start_location: str
    destination: str
    business_purpose: str
    notes: str
    tag_ids: list[str]
    created_at: str
    baseline: str


class MileageList(DTO):
    items: list[Mileage]


class MileageUpdate(ProtectedMutation):
    changes: MileageChanges


class MileageMaps(DTO):
    browser_api_key: str | None = None
    routes_available: bool = False
    region_code: str | None = None


class MileageRouteRequest(DTO):
    origin_place_id: str = Field(min_length=1, max_length=512)
    destination_place_id: str = Field(min_length=1, max_length=512)
    round_trip: bool = False

    @field_validator('origin_place_id', 'destination_place_id')
    @classmethod
    def valid_place_id(cls, value):
        value = value.strip()
        if not value or not value.isascii() or any(ord(char) < 32 for char in value):
            raise ValueError('Invalid selected place.')
        return value


class MileageRoute(DTO):
    outbound_miles: DecimalString | None = None
    return_miles: DecimalString | None = None
    error_code: Literal['quota', 'unavailable', 'no_route'] | None = None


def mileage_response(trip):
    row = trip.as_record()
    return Mileage(**{name: row[name] for name in Mileage.model_fields})
