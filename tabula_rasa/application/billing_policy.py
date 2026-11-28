"""Provider-independent free limits and renewal grace calculations."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


FREE_SEATS_PER_BUSINESS = 3
RENEWAL_GRACE = timedelta(days=15)
_SEAT_PRIORITY = {"owner": 0, "admin": 1}


@dataclass(frozen=True)
class BillingMember:
    user_id: str
    role: str
    joined_at: datetime


@dataclass(frozen=True)
class LinkedBusiness:
    organization_id: str
    linked_at: datetime
    active_members: tuple[BillingMember, ...]


@dataclass(frozen=True)
class FreeBusinessLimits:
    editable: bool
    seated_user_ids: frozenset[str]


def _utc(instant: datetime) -> datetime:
    if instant.utcoffset() is None:
        raise ValueError("Billing policy requires timezone-aware timestamps.")
    return instant.astimezone(UTC)


def free_plan_limits(businesses: tuple[LinkedBusiness, ...]) -> dict[str, FreeBusinessLimits]:
    """Calculate limits for one account's current links and active memberships.

    The caller supplies existing identity roles for active users and excludes
    invitations. Billing eligibility only narrows existing permissions.
    """
    limits = {}
    ordered_businesses = sorted(businesses, key=lambda business: (_utc(business.linked_at), business.organization_id))
    for index, business in enumerate(ordered_businesses):
        if business.organization_id in limits:
            raise ValueError("A billing account must contain each active business link once.")
        members = sorted(business.active_members, key=lambda member: (
            _SEAT_PRIORITY.get(member.role, 2), _utc(member.joined_at), member.user_id,
        ))
        if len({member.user_id for member in members}) != len(members):
            raise ValueError("A business must contain each active membership once.")
        limits[business.organization_id] = FreeBusinessLimits(
            editable=index == 0,
            seated_user_ids=frozenset(member.user_id for member in members[:FREE_SEATS_PER_BUSINESS]),
        )
    return limits


def renewal_grace_active(first_failed_renewal_at: datetime, *, as_of: datetime) -> bool:
    """Grace includes the first failure and ends after 15 elapsed days."""
    start = _utc(first_failed_renewal_at)
    return start <= _utc(as_of) < start + RENEWAL_GRACE
