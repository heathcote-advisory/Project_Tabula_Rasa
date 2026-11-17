"""Synthetic real-PostgreSQL admission, concurrency and HTTP threat boundaries."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.application.workspaces import WorkspaceService
from tabula_rasa.infrastructure.postgres.beta import PostgresBeta
from tabula_rasa.infrastructure.postgres.workspaces import PostgresWorkspaceRepository
from tests.postgres.test_identity import verified
from tests.postgres.test_identity_http import client_for


@pytest.fixture
def cohort(database):
    client, auth, factory = client_for(database)

    def person(name):
        return auth.sessions.issue(
            replace(verified(name), email=name + "@example.test")
        )

    raw, operator = person("operator")
    with database.transaction() as c:
        c.execute(
            text("""INSERT INTO beta_operator_grants VALUES
            (:user,'test',clock_timestamp()+interval '1 hour','synthetic-only')"""),
            {"user": operator.user_id},
        )
    beta = PostgresBeta(database)

    def command(operation, **fields):
        revision = beta.operate(auth.sessions, raw, {"operation": "inspect"})[
            "program"
        ]["revision"]
        body = dict(
            operation=operation,
            revision=revision,
            reason="synthetic-validation",
            approval_reference="synthetic-release",
            **fields,
        )
        return beta.operate(auth.sessions, raw, body)

    def configure(**overrides):
        body = dict(
            stage="initial",
            business_cap=3,
            user_cap=None,
            creation_paused=False,
            invitations_paused=False,
            suspended=False,
        )
        body.update(overrides)
        return command("configure", **body)

    service = WorkspaceService(PostgresWorkspaceRepository(database))

    def request(session, name="Synthetic", **eligibility):
        key = uuid4().hex
        result = service.create_business(session, key, name, **eligibility)
        assert result["state"] == "pending"
        snapshot = beta.operate(auth.sessions, raw, {"operation": "inspect"})
        admission = next(
            r["id"] for r in snapshot["admissions"] if r["request_id"] == key
        )
        return admission, key

    def admit(session, name="Synthetic"):
        admission, key = request(session, name)
        command("approve", admission_id=admission)
        return admission, key, service.create_business(session, key, name)

    return (
        client,
        auth,
        factory,
        person,
        raw,
        operator,
        beta,
        command,
        configure,
        service,
        request,
        admit,
    )


def test_self_service_preserves_request_bindings_caps_and_business_isolation(database, cohort):
    _, auth, _, person, _, _, beta, command, configure, service, request, _ = cohort
    _, alice = person("alice")
    bob_token, bob = person("bob")
    pending, original = request(alice, "Existing request")
    with pytest.raises(IdentityError, match="validation"):
        command("set_enrollment_mode", enrollment_mode="self_service")
    with pytest.raises(IdentityError, match="forbidden"):
        beta.operate(auth.sessions, bob_token, {"operation": "set_enrollment_mode"})

    configure(stage="expanded", business_cap=3, user_cap=2, invitations_paused=True)
    changed = command("set_enrollment_mode", enrollment_mode="self_service")
    audit = changed["events"][0]
    assert audit["action"] == "set_enrollment_mode"
    assert audit["approval_reference"] == "synthetic-release"
    assert audit["details"]["configuration"] == {"enrollment_mode": "self_service"}
    assert beta.status(alice.user_id, verified=True)["self_service"]
    assert service.create_business(alice, original, "Existing request")["state"] == "pending"
    with pytest.raises(IdentityError, match="forbidden"):
        service.create_business(replace(alice, verified_email=None), uuid4().hex, "Unverified")
    with pytest.raises(IdentityError, match="beta_eligibility"):
        service.create_business(alice, uuid4().hex, "Unsupported", country="FR", currency="USD")

    key = uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as executor:
        one, replay = executor.map(lambda _: service.create_business(alice, key, "First"), range(2))
    assert one == replay
    two = service.create_business(alice, uuid4().hex, "Second", country="CA", currency="CAD")
    three = service.create_business(bob, uuid4().hex, "Bob's business")
    assert len({row["organization_id"] for row in (one, two, three)}) == 3
    assert {row.entity_id for row in auth.sessions.identity.allowed_entities(alice.user_id)} == {
        one["entity_id"], two["entity_id"]
    }
    assert {row.entity_id for row in auth.sessions.identity.allowed_entities(bob.user_id)} == {three["entity_id"]}
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT currency_code FROM business_entities WHERE id=:id"),
                                 {"id": two["entity_id"]}) == "CAD"
    with pytest.raises(IdentityError, match="beta_capacity"):
        service.create_business(bob, uuid4().hex, "Over cap")
    with pytest.raises(IdentityError, match="beta_capacity"):
        command("approve", admission_id=pending)
    configure(stage="expanded", business_cap=3, user_cap=2, creation_paused=True)
    with pytest.raises(IdentityError, match="beta_paused"):
        service.create_business(bob, uuid4().hex, "Paused")

    command("set_enrollment_mode", enrollment_mode="approval_required")
    assert service.create_business(bob, uuid4().hex, "Requires approval")["state"] == "pending"
    assert service.create_business(alice, key, "First") == one
    assert service.bootstrap_status(alice, original)["state"] == "pending"


def test_mode_changes_keep_existing_approvals_and_expiration(database, cohort):
    _, _, _, person, _, _, _, command, configure, service, request, _ = cohort
    _, applicant = person("applicant")
    approved, can_create = request(applicant, "Approved before switch")
    expired, needs_approval = request(applicant, "Expired before switch")
    command("approve", admission_id=approved)
    command("approve", admission_id=expired)
    with database.transaction() as connection:
        connection.execute(text("UPDATE beta_admissions SET expires_at=clock_timestamp() WHERE id=:id"),
                           {"id": expired})
    configure(stage="expanded", business_cap=2, user_cap=1)
    command("set_enrollment_mode", enrollment_mode="self_service")
    ready = service.create_business(applicant, can_create, "Approved before switch")
    assert ready["state"] == "ready"
    assert service.create_business(applicant, needs_approval, "Expired before switch")["state"] == "expired"
    command("set_enrollment_mode", enrollment_mode="approval_required")
    command("approve", admission_id=expired)
    assert service.create_business(applicant, needs_approval, "Expired before switch")["state"] == "ready"
    assert service.create_business(applicant, can_create, "Approved before switch") == ready


def test_closed_default_pending_identity_binding_and_eligibility(database, cohort):
    _, auth, _, person, raw, _, beta, command, configure, service, request, admit = (
        cohort
    )
    _, alice = person("alice")
    _, bob = person("bob")
    admission, key = request(alice)
    assert beta.status(alice.user_id)["paused"] is True
    command("approve", admission_id=admission)
    with pytest.raises(IdentityError, match="beta_paused"):
        service.create_business(alice, key, "Synthetic")
    assert service.bootstrap_status(bob, key)["state"] == "not_started"
    assert service.create_business(bob, key, "Synthetic")["state"] == "pending"
    assert not auth.sessions.identity.allowed_entities(alice.user_id)
    configure()
    ready = service.create_business(alice, key, "Synthetic")
    assert ready["state"] == "ready"
    assert service.create_business(alice, key, "Synthetic") == ready
    with pytest.raises(IdentityError, match="conflict"):
        service.create_business(alice, key, "Changed")
    ca, _ = request(alice, "Canadian", country="CA", currency="CAD")
    command("approve", admission_id=ca)
    for country, currency in [("US", "CAD"), ("CA", "USD"), ("FR", "USD")]:
        with pytest.raises(IdentityError, match="beta_eligibility"):
            service.create_business(
                alice, uuid4().hex, "Unsupported", country=country, currency=currency
            )
    with database.transaction(read_only=True) as c:
        assert c.scalar(text("SELECT count(*) FROM organizations")) == 1
        assert c.scalar(text("SELECT currency_code FROM business_entities")) == "USD"
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM beta_events WHERE actor_id=:actor AND action='activated'"
                ),
                {"actor": alice.user_id},
            )
            == 1
        )


def test_three_aggregate_business_slots_concurrent_replay_and_reclamation(
    database, cohort
):
    _, auth, _, person, raw, _, beta, command, configure, service, request, admit = (
        cohort
    )
    configure()
    _, alice = person("alice")
    _, bob = person("bob")
    pending = [
        request(alice, "A"),
        request(alice, "B"),
        request(bob, "C"),
        request(bob, "D"),
    ]
    for admission, _ in pending[:3]:
        command("approve", admission_id=admission)
    with pytest.raises(IdentityError, match="beta_capacity"):
        command("approve", admission_id=pending[3][0])

    def create(item):
        index, (admission, key) = item
        return service.create_business(alice if index < 2 else bob, key, "ABCD"[index])

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                create,
                [(0, pending[0]), (0, pending[0]), (1, pending[1]), (2, pending[2])],
            )
        )
    assert results[0] == results[1]
    assert beta.operate(auth.sessions, raw, {"operation": "inspect"})["counts"] == {
        "businesses": 3,
        "users": 2,
    }
    assert len(auth.sessions.identity.allowed_entities(alice.user_id)) == 2
    assert len(auth.sessions.identity.allowed_entities(bob.user_id)) == 1
    command("suspend", admission_id=pending[0][0])
    with pytest.raises(IdentityError, match="beta_capacity"):
        command("approve", admission_id=pending[3][0])
    # Real admitted records retain their total program allocation after closure.
    command("close", admission_id=pending[0][0])
    with pytest.raises(IdentityError, match="beta_capacity"):
        command("approve", admission_id=pending[3][0])


def test_expiring_unused_approval_deny_and_withdraw_free_slots(database, cohort):
    _, _, _, person, raw, _, beta, command, configure, service, request, _ = cohort
    configure()
    _, alice = person("alice")
    requests = [request(alice, str(i)) for i in range(4)]
    for admission, _ in requests[:3]:
        command("approve", admission_id=admission)
    with database.transaction() as c:
        c.execute(
            text(
                "UPDATE beta_admissions SET expires_at=clock_timestamp() WHERE id=:id"
            ),
            {"id": requests[0][0]},
        )
    command("approve", admission_id=requests[3][0])
    assert service.create_business(alice, requests[0][1], "0")["state"] == "expired"
    command("deny", admission_id=requests[1][0])
    command("withdraw", admission_id=requests[2][0])
    command("approve", admission_id=requests[0][0])
    assert service.create_business(alice, requests[0][1], "0")["state"] == "ready"


def test_named_users_independent_roles_expiry_revocation_pause_and_reapproval(
    database, cohort
):
    _, auth, _, person, raw, _, beta, command, configure, service, request, admit = (
        cohort
    )
    configure()
    alice_raw, alice = person("alice")
    _, bob = person("bob")
    _, guest = person("guest")
    a, _, one = admit(alice, "A")
    b, _, two = admit(bob, "B")
    for admission in (a, b):
        command("approve_user", admission_id=admission, user_id=guest.user_id)
    invite = service.invite(
        alice, one["organization_id"], guest.verified_email, "read_only"
    )
    with pytest.raises(IdentityError, match="beta_pending"):
        service.invite(alice, one["organization_id"], guest.verified_email, "editor")
    configure(invitations_paused=True, creation_paused=True)
    with pytest.raises(IdentityError, match="beta_paused"):
        service.respond(guest, invite.credential, True)
    assert auth.sessions.validate(alice_raw).selected.entity_id == one["entity_id"]
    assert (
        service.bootstrap_status(
            alice, next(r["request_id"] for r in beta.status(alice.user_id)["requests"])
        )["state"]
        == "ready"
    )
    configure()
    service.respond(guest, invite.credential, True)
    service.respond(
        guest,
        service.invite(
            bob, two["organization_id"], guest.verified_email, "accountant"
        ).credential,
        True,
    )
    assert {
        i.entity_id: i.role
        for i in auth.sessions.identity.allowed_entities(guest.user_id)
    } == {one["entity_id"]: "read_only", two["entity_id"]: "accountant"}
    with pytest.raises(IdentityError, match="invitation_used"):
        service.respond(guest, invite.credential, True)
    command("approve_user", admission_id=a, user_id=guest.user_id)
    stale = service.invite(
        alice, one["organization_id"], guest.verified_email, "editor"
    )
    # Even a trusted separate support path revocation invalidates the old offer.
    auth.sessions.identity.set_membership(
        one["organization_id"], guest.user_id, "read_only", active=False
    )
    with pytest.raises(IdentityError, match="beta_pending"):
        service.respond(guest, stale.credential, True)
    command("approve_user", admission_id=a, user_id=guest.user_id)
    fresh = service.invite(
        alice, one["organization_id"], guest.verified_email, "editor"
    )
    service.respond(guest, fresh.credential, False)
    with pytest.raises(IdentityError, match="beta_pending"):
        service.invite(alice, one["organization_id"], guest.verified_email, "editor")
    command("approve_user", admission_id=a, user_id=guest.user_id)
    fresh = service.invite(
        alice, one["organization_id"], guest.verified_email, "editor"
    )
    service.respond(guest, fresh.credential, True)
    assert len(auth.sessions.identity.allowed_entities(guest.user_id)) == 2
    command("suspend", admission_id=a)
    assert len(auth.sessions.identity.allowed_entities(guest.user_id)) == 2
    with pytest.raises(IdentityError, match="beta_paused"):
        service.invite(alice, one["organization_id"], bob.verified_email, "read_only")
    command("resume", admission_id=a)
    assert beta.operate(auth.sessions, raw, {"operation": "inspect"})["counts"] == {
        "businesses": 2,
        "users": 3,
    }


def test_expanded_requires_authorization_and_ten_twenty_unique_caps(database, cohort):
    _, auth, _, person, raw, _, beta, command, configure, service, request, admit = (
        cohort
    )
    _, alice = person("alice")
    with pytest.raises(IdentityError, match="validation"):
        configure(business_cap=10, user_cap=20)
    configure(stage="expanded", business_cap=10, user_cap=20)
    admissions = [admit(alice, str(i))[0] for i in range(10)]
    eleventh, _ = request(alice, "eleventh")
    with pytest.raises(IdentityError, match="beta_capacity"):
        command("approve", admission_id=eleventh)
    guests = [person("guest" + str(i))[1] for i in range(20)]
    for guest in guests[:19]:
        command("approve_user", admission_id=admissions[0], user_id=guest.user_id)
    command("approve_user", admission_id=admissions[1], user_id=guests[0].user_id)
    assert beta.operate(auth.sessions, raw, {"operation": "inspect"})["counts"] == {
        "businesses": 10,
        "users": 20,
    }
    with pytest.raises(IdentityError, match="beta_capacity"):
        command("approve_user", admission_id=admissions[0], user_id=guests[19].user_id)
    command("withdraw_user", admission_id=admissions[0], user_id=guests[18].user_id)
    command("approve_user", admission_id=admissions[0], user_id=guests[19].user_id)
    with pytest.raises(IdentityError, match="beta_capacity"):
        configure()  # Cannot downgrade to caps below existing allocations.


def test_operator_http_csrf_expiry_selectors_and_private_status(
    database, cohort, caplog
):
    (
        client,
        auth,
        factory,
        person,
        raw,
        operator,
        beta,
        command,
        configure,
        service,
        request,
        admit,
    ) = cohort
    alice_raw, alice = person("alice")
    admission, _ = request(alice, "Private business")
    with client:
        client.cookies.set(auth.session_cookie, alice_raw, domain="127.0.0.1", path="/")
        headers = {
            "origin": auth.settings.public_origin,
            "x-csrf-token": alice.csrf_token,
        }
        assert (
            client.post(
                "/api/v1/beta/operator", json={"operation": "inspect"}, headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/beta/operator", json={"operation": "inspect"}
            ).status_code
            == 403
        )
        status = client.get("/api/v1/beta/status")
        assert (
            status.status_code == 200 and status.headers["cache-control"] == "no-store"
        )
        assert "Private business" in status.text and operator.user_id not in status.text
        forged = client.post(
            "/api/v1/businesses",
            headers=headers,
            json={
                "request_id": uuid4().hex,
                "business_name": "Forged",
                "admission_id": admission,
            },
        )
        assert forged.status_code == 422
        client.cookies.set(auth.session_cookie, raw, domain="127.0.0.1", path="/")
        op_headers = {**headers, "x-csrf-token": operator.csrf_token}
        assert (
            client.post(
                "/api/v1/beta/operator",
                json={"operation": "inspect"},
                headers=op_headers,
            ).status_code
            == 200
        )
        assert client.get("/api/v1/beta/status").json()["requests"] == []
        with database.transaction() as c:
            c.execute(
                text("UPDATE beta_operator_grants SET expires_at=clock_timestamp()")
            )
        assert (
            client.post(
                "/api/v1/beta/operator",
                json={"operation": "inspect"},
                headers=op_headers,
            ).status_code
            == 403
        )
        factory.assert_not_called()
    assert "Private business" not in caplog.text and raw not in caplog.text
    from tabula_rasa.infrastructure.postgres.runtime import DatabaseError

    with pytest.raises(DatabaseError):
        with database.transaction() as c:
            c.execute(text("DELETE FROM beta_events"))


def test_concurrent_invitation_consumption_expiry_and_atomic_creation_failure(
    database, cohort, monkeypatch
):
    from tabula_rasa.errors import StorageError
    from tabula_rasa.infrastructure.postgres.reference_data import (
        PostgresLedgerSetupRepository,
    )

    (
        client,
        auth,
        _,
        person,
        raw,
        _,
        beta,
        command,
        configure,
        service,
        request,
        admit,
    ) = cohort
    configure(stage="expanded", business_cap=10, user_cap=2)
    _, alice = person("alice")
    _, guest = person("guest")
    _, other = person("other")
    admission, key = request(alice)
    command("approve", admission_id=admission)
    original = PostgresLedgerSetupRepository.ensure_defaults

    def fail(repository):
        original(repository)
        raise StorageError(code="unavailable")

    monkeypatch.setattr(PostgresLedgerSetupRepository, "ensure_defaults", fail)
    with pytest.raises(StorageError):
        service.create_business(alice, key, "Synthetic")
    snapshot = beta.operate(auth.sessions, raw, {"operation": "inspect"})
    assert snapshot["counts"] == {"businesses": 1, "users": 1}
    assert snapshot["admissions"][0]["state"] == "approved"
    assert auth.sessions.identity.allowed_entities(alice.user_id) == ()
    monkeypatch.setattr(PostgresLedgerSetupRepository, "ensure_defaults", original)
    result = service.create_business(alice, key, "Synthetic")
    command("approve_user", admission_id=admission, user_id=guest.user_id)

    def offer(_):
        try:
            return service.invite(
                alice, result["organization_id"], guest.verified_email, "read_only"
            )
        except IdentityError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(offer, range(2)))
    assert (
        sum(isinstance(item, str) and item == "beta_pending" for item in outcomes) == 1
    )
    invitation = next(item for item in outcomes if not isinstance(item, str))
    with pytest.raises(IdentityError, match="beta_capacity"):
        command("approve_user", admission_id=admission, user_id=other.user_id)
    with database.transaction() as c:
        c.execute(
            text(
                "UPDATE beta_named_users SET expires_at=clock_timestamp() WHERE user_id=:user"
            ),
            {"user": guest.user_id},
        )
    command("approve_user", admission_id=admission, user_id=other.user_id)
    with pytest.raises(IdentityError, match="beta_pending"):
        service.respond(guest, invitation.credential, True)
    # Expired offers require fresh approval; cancellation never revokes a session.
    service.revoke_invitation(alice, result["organization_id"], invitation.id)
    assert (
        beta.operate(auth.sessions, raw, {"operation": "inspect"})["counts"]["users"]
        == 2
    )


def test_stale_operator_revision_old_session_and_regular_business_roles_cannot_expand(
    database, cohort
):
    from datetime import timedelta

    _, auth, _, person, raw, _, beta, command, configure, service, _, admit = cohort
    configure()
    alice_raw, alice = person("alice")
    _, _, business = admit(alice)
    with pytest.raises(IdentityError, match="forbidden"):
        beta.operate(auth.sessions, alice_raw, {"operation": "inspect"})
    stale = beta.operate(auth.sessions, raw, {"operation": "inspect"})["program"][
        "revision"
    ]
    configure(invitations_paused=True)
    with pytest.raises(IdentityError, match="stale"):
        beta.operate(
            auth.sessions,
            raw,
            dict(
                operation="configure",
                revision=stale,
                stage="expanded",
                business_cap=10,
                user_cap=20,
                creation_paused=False,
                invitations_paused=False,
                suspended=False,
                reason="synthetic",
                approval_reference="synthetic",
            ),
        )
    previous = auth.sessions.clock
    auth.sessions.clock = lambda: previous() + timedelta(minutes=6)
    with pytest.raises(IdentityError, match="reauthentication_required"):
        beta.operate(auth.sessions, raw, {"operation": "inspect"})


def test_invitation_deadline_matches_approval_in_inventory_preview_and_acceptance(
    database, cohort
):
    from datetime import timedelta

    _, auth, _, person, _, _, _, command, configure, service, _, admit = cohort
    configure()
    _, owner = person("owner")
    _, guest = person("guest")
    admission, _, business = admit(owner)
    command("approve_user", admission_id=admission, user_id=guest.user_id)
    with database.transaction() as connection:
        deadline = connection.scalar(
            text("""UPDATE beta_named_users
            SET expires_at=clock_timestamp()+interval '30 seconds'
            WHERE admission_id=:id AND user_id=:user RETURNING expires_at"""),
            {"id": admission, "user": guest.user_id},
        )
    invitation = service.invite(
        owner, business["organization_id"], guest.verified_email, "read_only"
    )
    assert (
        service.invitations(owner, business["organization_id"])[0]["expires_at"]
        == deadline
    )
    assert (
        service.preview_invitation(guest, invitation.credential)["expires_at"]
        == deadline
    )
    with database.transaction(read_only=True) as connection:
        assert (
            connection.scalar(
                text("SELECT expires_at FROM workspace_invitations WHERE id=:id"),
                {"id": invitation.id},
            )
            == deadline
        )
        assert (
            connection.scalar(
                text(
                    "SELECT expires_at FROM beta_named_users WHERE admission_id=:id AND user_id=:user"
                ),
                {"id": admission, "user": guest.user_id},
            )
            == deadline
        )
    service.repository.clock = lambda: deadline + timedelta(seconds=1)
    assert (
        service.invitations(owner, business["organization_id"])[0]["state"] == "expired"
    )
    for operation in (
        lambda: service.preview_invitation(guest, invitation.credential),
        lambda: service.respond(guest, invitation.credential, True),
    ):
        with pytest.raises(IdentityError, match="invitation_expired"):
            operation()
    assert auth.sessions.identity.allowed_entities(guest.user_id) == ()


def test_named_operator_commands_require_target_and_do_not_audit_silent_withdrawals(
    database, cohort
):
    client, auth, _, person, raw, operator, beta, command, configure, _, _, admit = (
        cohort
    )
    configure()
    _, owner = person("owner")
    _, stranger = person("stranger")
    admission, _, _ = admit(owner)
    before = beta.operate(auth.sessions, raw, {"operation": "inspect"})
    with client:
        client.cookies.set(auth.session_cookie, raw, domain="127.0.0.1", path="/")
        headers = {
            "origin": auth.settings.public_origin,
            "x-csrf-token": operator.csrf_token,
        }
        for operation in ("approve_user", "withdraw_user"):
            body = {
                "operation": operation,
                "revision": before["program"]["revision"],
                "admission_id": admission,
                "reason": "synthetic",
                "approval_reference": "synthetic",
            }
            for target in ({}, {"user_id": None}, {"user_id": ""}):
                result = client.post(
                    "/api/v1/beta/operator", headers=headers, json={**body, **target}
                )
                assert (
                    result.status_code == 422
                    and result.json()["error"]["code"] == "validation"
                )
        with pytest.raises(IdentityError, match="not_found"):
            command("withdraw_user", admission_id=admission, user_id=stranger.user_id)
    after = beta.operate(auth.sessions, raw, {"operation": "inspect"})
    assert after == before
