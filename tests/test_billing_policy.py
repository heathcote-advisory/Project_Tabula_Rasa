from datetime import UTC, datetime, timedelta, timezone

import pytest

from tabula_rasa.application.billing_policy import BillingMember, FreeBusinessLimits, LinkedBusiness
from tabula_rasa.application.billing_policy import free_plan_limits, renewal_grace_active


START = datetime(2026, 10, 20, 12, tzinfo=UTC)
NEXT_DAY = START + timedelta(days=1)


def test_free_plan_selects_first_business_and_seats_per_organization():
    links = (LinkedBusiness("second", NEXT_DAY), LinkedBusiness("first", START))
    first = (
        BillingMember("shared", "editor", NEXT_DAY),
        BillingMember("owner-a", "owner", NEXT_DAY),
        BillingMember("admin-a", "admin", NEXT_DAY),
        BillingMember("older-viewer", "read_only", START),
    )
    second = (
        BillingMember("shared", "owner", NEXT_DAY),
        BillingMember("accountant-b", "accountant", START),
        BillingMember("editor-b", "editor", NEXT_DAY),
        BillingMember("viewer-b", "read_only", NEXT_DAY),
    )

    assert free_plan_limits(links, organization_id="first", active_members=first) == FreeBusinessLimits(
        True, frozenset({"owner-a", "admin-a", "older-viewer"}),
    )
    assert free_plan_limits(links, organization_id="second", active_members=second) == FreeBusinessLimits(
        False, frozenset({"shared", "accountant-b", "editor-b"}),
    )


def test_free_plan_breaks_time_ties_by_id_and_applies_the_cap_to_admins():
    links = (LinkedBusiness("beta", START), LinkedBusiness("alpha", START))
    members = (
        BillingMember("charlie", "admin", START),
        BillingMember("bob", "admin", START),
        BillingMember("alice", "admin", START),
        BillingMember("owner", "owner", NEXT_DAY),
        BillingMember("older-editor", "editor", START - timedelta(days=1)),
    )

    assert free_plan_limits(links, organization_id="alpha", active_members=members) == FreeBusinessLimits(
        True, frozenset({"owner", "alice", "bob"}),
    )
    assert free_plan_limits(links, organization_id="beta", active_members=()) == FreeBusinessLimits(False, frozenset())


def test_duplicate_business_links_or_memberships_fail_closed():
    owner = BillingMember("user", "owner", START)
    business = LinkedBusiness("alpha", START)
    with pytest.raises(ValueError, match="business link"):
        free_plan_limits((business, business), organization_id="alpha", active_members=(owner,))
    with pytest.raises(ValueError, match="business link"):
        free_plan_limits((business,), organization_id="beta", active_members=(owner,))
    with pytest.raises(ValueError, match="membership"):
        free_plan_limits((business,), organization_id="alpha", active_members=(owner, owner))


def test_grace_uses_exact_elapsed_time_across_offset_changes():
    before_clock_change = timezone(timedelta(hours=-4))
    after_clock_change = timezone(timedelta(hours=-5))
    failed = datetime(2026, 10, 25, 12, tzinfo=before_clock_change)
    expires = datetime(2026, 11, 9, 11, tzinfo=after_clock_change)

    assert not renewal_grace_active(failed, as_of=failed - timedelta(microseconds=1))
    assert renewal_grace_active(failed, as_of=failed)
    assert renewal_grace_active(failed, as_of=expires - timedelta(microseconds=1))
    assert not renewal_grace_active(failed, as_of=expires)


def test_billing_times_require_a_timezone():
    naive = START.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        free_plan_limits((LinkedBusiness("alpha", naive),), organization_id="alpha", active_members=())
    with pytest.raises(ValueError, match="timezone-aware"):
        renewal_grace_active(START, as_of=naive)
