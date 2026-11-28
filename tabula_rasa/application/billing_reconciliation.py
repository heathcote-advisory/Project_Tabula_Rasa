"""Reconcile verified event triggers against provider-independent terms."""

from dataclasses import dataclass
from typing import Literal

from .billing_policy import SubscriptionTerms
from .identity import IdentityError


@dataclass(frozen=True)
class VerifiedSubscriptionEvent:
    provider: str
    provider_scope: str
    provider_event_id: str
    provider_subscription_id: str


@dataclass(frozen=True)
class ReconciledSubscription:
    plan_key: str
    plan_version: int
    terms: SubscriptionTerms


def reconcile_subscription_event(
    repository, event: VerifiedSubscriptionEvent, read_authoritative,
) -> Literal["unmapped", "unavailable", "stale", "applied", "duplicate"]:
    """Load the account version before asking the adapter for current terms.

    Only a verified event can enter this workflow. Its delivery order and
    payload do not determine access. The adapter returns None for an expected
    outage or unconfirmed state. A concurrent write requires a fresh read.
    """
    target = repository.reconciliation_target(event)
    if target is None:
        return "unmapped"
    account_id, version = target
    snapshot = read_authoritative(event)
    if snapshot is None:
        return "unavailable"
    try:
        applied = repository.apply_verified_event(
            account_id, event, expected_version=version, plan_key=snapshot.plan_key,
            plan_version=snapshot.plan_version, terms=snapshot.terms,
        )
    except IdentityError as error:
        if error.code == "stale":
            return "stale"
        raise
    return "applied" if applied else "duplicate"
