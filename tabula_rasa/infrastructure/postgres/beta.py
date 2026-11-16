"""Small admission control plane, serialized with the mutations it protects.

Program locks precede identity user/organization locks. These records never
confer a membership, revoke a session, or change bookkeeping authority.
"""

from datetime import timedelta
import json
from uuid import uuid4

from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from .control_plane import identity_operation


def lock_program(connection):
    row = (
        connection.execute(text("SELECT * FROM beta_program WHERE id=true FOR UPDATE"))
        .mappings()
        .first()
    )
    if row is None:
        raise IdentityError("beta_pending")
    return row


def internal(connection, user):
    return bool(
        connection.scalar(
            text("SELECT user_id FROM beta_internal_users WHERE user_id=:user"),
            {"user": user},
        )
    )


def event(
    connection,
    actor,
    action,
    target,
    reason="admission_workflow",
    approval="server-policy",
    **details,
):
    connection.execute(
        text("""INSERT INTO beta_events(actor_id,action,target_id,reason,approval_reference,details)
        VALUES (:actor,:action,:target,:reason,:approval,CAST(:details AS jsonb))"""),
        dict(
            actor=actor,
            action=action,
            target=target,
            reason=reason,
            approval=approval,
            details=json.dumps(details),
        ),
    )


def counts(connection):
    businesses = connection.scalar(
        text("""SELECT count(*) FROM beta_admissions
        WHERE organization_id IS NOT NULL OR state='approved' AND expires_at>clock_timestamp()""")
    )
    users = connection.scalar(
        text("""SELECT count(DISTINCT n.user_id) FROM beta_named_users n
        JOIN beta_admissions a ON a.id=n.admission_id WHERE n.ever_active
        OR n.state IN ('approved','invited') AND n.expires_at>clock_timestamp()
        AND (a.organization_id IS NOT NULL OR a.state='approved' AND a.expires_at>clock_timestamp())""")
    )
    return {"businesses": businesses, "users": users}


def check_capacity(connection, program):
    used = counts(connection)
    if (
        used["businesses"] > program["business_cap"]
        or program["user_cap"] is not None
        and used["users"] > program["user_cap"]
    ):
        raise IdentityError("beta_capacity")


def require_open(program, *, creation=False):
    if (
        program["suspended"]
        or program["creation_paused" if creation else "invitations_paused"]
    ):
        raise IdentityError("beta_paused")


def prepare_business(connection, actor, request_id, name, country, currency, *, verified=False):
    program = lock_program(connection)
    if (country, currency) not in {("US", "USD"), ("CA", "CAD")}:
        raise IdentityError("beta_eligibility")
    row = (
        connection.execute(
            text(
                "SELECT * FROM beta_admissions WHERE user_id=:user AND request_id=:request"
            ),
            {"user": actor, "request": request_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        # Exempt only a new USD request, never a request already bound to admission.
        if internal(connection, actor) and (country, currency) == ("US", "USD"):
            return None
        self_service = program["enrollment_mode"] == "self_service"
        if self_service:
            require_open(program, creation=True)
            if not verified:
                raise IdentityError("forbidden")
        key = uuid4().hex
        connection.execute(
            text("""INSERT INTO beta_admissions(id,user_id,request_id,business_name,country,state)
            VALUES (:id,:user,:request,:name,:country,:state)"""),
            dict(
                id=key,
                user=actor,
                request=request_id,
                name=name,
                country=country,
                state='approved' if self_service else 'pending',
            ),
        )
        if self_service:
            PostgresBeta._approve_user(connection, key, actor)
            check_capacity(connection, program)
            event(connection, actor, "self_service_admitted", key, revision=program["revision"])
            return None
        event(connection, actor, "requested", key)
        return {"state": "pending", "entity_id": None, "organization_id": None}
    if (row["business_name"], row["country"]) != (
        name,
        country,
    ):
        raise IdentityError("conflict")
    if row["state"] != "approved" or connection.scalar(
        text("SELECT :expiry<=clock_timestamp()"), {"expiry": row["expires_at"]}
    ):
        return {
            "state": "pending"
            if row["state"] == "pending"
            else "expired"
            if row["state"] == "approved"
            else row["state"],
            "entity_id": None,
            "organization_id": None,
        }
    require_open(program, creation=True)
    owner = connection.scalar(
        text("""SELECT user_id FROM beta_named_users WHERE admission_id=:id
        AND user_id=:user AND state='approved' AND expires_at>clock_timestamp()"""),
        {"id": row["id"], "user": actor},
    )
    if owner is None:
        raise IdentityError("beta_pending")
    check_capacity(connection, program)
    return None


def activate_business(connection, actor, request_id, org):
    row = (
        connection.execute(
            text("""UPDATE beta_admissions SET state='active',organization_id=:org
        WHERE user_id=:user AND request_id=:request AND state='approved' RETURNING id"""),
            dict(org=org, user=actor, request=request_id),
        )
        .mappings()
        .first()
    )
    if row:
        connection.execute(
            text(
                "UPDATE beta_named_users SET state='active',ever_active=true WHERE admission_id=:id AND user_id=:user"
            ),
            {"id": row["id"], "user": actor},
        )
        event(connection, actor, "activated", row["id"])


def business_admission(connection, actor, org):
    row = (
        connection.execute(
            text("SELECT * FROM beta_admissions WHERE organization_id=:org"),
            {"org": org},
        )
        .mappings()
        .first()
    )
    if row is None:
        # Existing internal operations remain separately authorized; an external
        # beta identity never gains this bypass by acquiring a membership.
        if not internal(connection, actor):
            raise IdentityError("beta_pending")
        return None
    if row["state"] != "active":
        raise IdentityError("beta_paused")
    return row


def invitation_grant(connection, actor, org, *, email=None, user=None, invitation=None):
    program = lock_program(connection)
    admission = business_admission(connection, actor, org)
    if admission is None:
        return None
    require_open(program)
    rows = (
        connection.execute(
            text("""SELECT n.*,u.email FROM beta_named_users n
        JOIN identity_users u ON u.id=n.user_id AND u.state='active'
        WHERE n.admission_id=:id AND n.state=:state AND n.expires_at>clock_timestamp()
        AND (CAST(:user AS text) IS NULL OR n.user_id=:user)"""),
            {
                "id": admission["id"],
                "state": "invited" if invitation else "approved",
                "user": user,
            },
        )
        .mappings()
        .all()
    )
    rows = [
        row
        for row in rows
        if (email is None or row["email"].strip().casefold() == email)
        and (invitation is None or row["invitation_id"] == invitation)
    ]
    if len(rows) != 1:
        raise IdentityError("beta_pending")
    check_capacity(connection, program)
    return rows[0]


def release_invitation(connection, actor, invitation):
    row = (
        connection.execute(
            text("""UPDATE beta_named_users SET state='withdrawn'
        WHERE invitation_id=:invitation AND state='invited' RETURNING admission_id"""),
            {"invitation": invitation},
        )
        .mappings()
        .first()
    )
    if row:
        event(connection, actor, "invitation_released", row["admission_id"])


class PostgresBeta:
    def __init__(self, database):
        self.database = database

    def status(self, actor, *, verified=False):
        with identity_operation(self.database, read_only=True) as connection:
            program = (
                connection.execute(text("SELECT * FROM beta_program WHERE id=true"))
                .mappings()
                .one()
            )
            requests = [
                dict(row)
                for row in connection.execute(
                    text("""SELECT request_id,business_name,country,
                CASE WHEN state='approved' AND expires_at<=clock_timestamp() THEN 'expired' ELSE state END AS state
                FROM beta_admissions WHERE user_id=:user ORDER BY id"""),
                    {"user": actor},
                ).mappings()
            ]
            return {
                "paused": program["suspended"] or program["creation_paused"],
                "invitations_paused": program["suspended"]
                or program["invitations_paused"],
                "internal": internal(connection, actor),
                "self_service": verified and program["enrollment_mode"] == "self_service",
                "requests": requests,
            }

    def operate(self, sessions, credential, command):
        # Revalidate the real session in this transaction. HTTP snapshots and tenant
        # roles cannot confer an environment-scoped, expiring operator grant.
        with identity_operation(self.database) as connection:
            session = sessions.validate(credential)
            if (
                not -timedelta(seconds=60)
                <= sessions.clock() - session.authenticated_at
                <= timedelta(minutes=5)
            ):
                raise IdentityError("reauthentication_required")
            program = lock_program(connection)
            if not connection.scalar(
                text("""SELECT g.user_id FROM beta_operator_grants g
                JOIN identity_users u ON u.id=g.user_id AND u.state='active'
                WHERE g.user_id=:user AND g.environment=:env AND g.expires_at>clock_timestamp() FOR SHARE OF g,u"""),
                {"user": session.user_id, "env": sessions.environment},
            ):
                raise IdentityError("forbidden")
            operation = command["operation"]
            if operation == "inspect":
                return self._snapshot(connection, program)
            if command.get("revision") != program["revision"]:
                raise IdentityError("stale")
            reason, approval = (
                command.get("reason", ""),
                command.get("approval_reference", ""),
            )
            if not all(
                isinstance(v, str) and 1 <= len(v) <= 200 and v.strip()
                for v in (reason, approval)
            ):
                raise IdentityError("validation")
            if operation in {"approve_user", "withdraw_user"} and not command.get(
                "user_id"
            ):
                raise IdentityError("validation")
            target = command.get("admission_id") or "program"
            if operation == "set_enrollment_mode":
                target = "program"
                mode = command.get("enrollment_mode")
                if mode not in {"approval_required", "self_service"} or (
                    mode == "self_service" and program["stage"] != "expanded"
                ):
                    raise IdentityError("validation")
                connection.execute(text("UPDATE beta_program SET enrollment_mode=:mode WHERE id=true"),
                                   {"mode": mode})
            elif operation == "configure":
                stage = command["stage"]
                cap, users = command["business_cap"], command.get("user_cap")
                if (
                    stage == "initial"
                    and (not 1 <= cap <= 3 or users is not None)
                    or stage == "expanded"
                    and (
                        not 1 <= cap <= 10
                        or type(users) is not int
                        or not 1 <= users <= 20
                    )
                    or stage not in {"initial", "expanded"}
                    or stage != "expanded" and program["enrollment_mode"] == "self_service"
                ):
                    raise IdentityError("validation")
                connection.execute(
                    text("""UPDATE beta_program SET stage=:stage,business_cap=:cap,user_cap=:users,
                    creation_paused=:creation,invitations_paused=:invitations,suspended=:suspended WHERE id=true"""),
                    dict(
                        stage=stage,
                        cap=cap,
                        users=users,
                        creation=command["creation_paused"],
                        invitations=command["invitations_paused"],
                        suspended=command["suspended"],
                    ),
                )
            else:
                admission = (
                    connection.execute(
                        text("SELECT * FROM beta_admissions WHERE id=:id"),
                        {"id": target},
                    )
                    .mappings()
                    .first()
                )
                if admission is None:
                    raise IdentityError("not_found")
                if operation == "approve":
                    if admission["organization_id"] is not None:
                        raise IdentityError("beta_eligibility")
                    if admission["state"] not in {
                        "pending",
                        "approved",
                        "denied",
                        "withdrawn",
                    }:
                        raise IdentityError("conflict")
                    connection.execute(
                        text("""UPDATE beta_admissions SET state='approved',
                        expires_at=clock_timestamp()+interval '48 hours' WHERE id=:id"""),
                        {"id": target},
                    )
                    self._approve_user(connection, target, admission["user_id"])
                elif operation == "approve_user":
                    if admission["state"] != "active":
                        raise IdentityError("beta_paused")
                    self._approve_user(connection, target, command["user_id"])
                elif operation in {"deny", "withdraw"}:
                    if admission["organization_id"] is not None:
                        raise IdentityError("conflict")
                    connection.execute(
                        text("UPDATE beta_admissions SET state=:state WHERE id=:id"),
                        {
                            "id": target,
                            "state": "denied" if operation == "deny" else "withdrawn",
                        },
                    )
                    connection.execute(
                        text(
                            "UPDATE beta_named_users SET state='withdrawn' WHERE admission_id=:id"
                        ),
                        {"id": target},
                    )
                elif operation in {"suspend", "resume", "close"}:
                    if admission["organization_id"] is None or admission[
                        "state"
                    ] not in (
                        {"active"}
                        if operation == "suspend"
                        else {"suspended"}
                        if operation == "resume"
                        else {"active", "suspended"}
                    ):
                        raise IdentityError("conflict")
                    connection.execute(
                        text("UPDATE beta_admissions SET state=:state WHERE id=:id"),
                        {
                            "id": target,
                            "state": {
                                "suspend": "suspended",
                                "resume": "active",
                                "close": "closed",
                            }[operation],
                        },
                    )
                elif operation == "withdraw_user":
                    withdrawn = connection.scalar(
                        text(
                            "UPDATE beta_named_users SET state='withdrawn' WHERE admission_id=:id AND user_id=:user RETURNING user_id"
                        ),
                        {"id": target, "user": command["user_id"]},
                    )
                    if withdrawn is None:
                        raise IdentityError("not_found")
                else:
                    raise IdentityError("validation")
            current = (
                connection.execute(text("SELECT * FROM beta_program WHERE id=true"))
                .mappings()
                .one()
            )
            check_capacity(connection, current)
            connection.execute(
                text("UPDATE beta_program SET revision=revision+1 WHERE id=true")
            )
            event(
                connection,
                session.user_id,
                operation,
                target,
                reason,
                approval,
                user_id=command.get("user_id"),
                configuration={
                    k: v
                    for k, v in command.items()
                    if k
                    in ({"enrollment_mode"} if operation == "set_enrollment_mode" else {
                        "stage",
                        "business_cap",
                        "user_cap",
                        "creation_paused",
                        "invitations_paused",
                        "suspended",
                    })
                },
            )
            return self._snapshot(
                connection,
                connection.execute(text("SELECT * FROM beta_program WHERE id=true"))
                .mappings()
                .one(),
            )

    @staticmethod
    def _approve_user(connection, admission, user):
        if not connection.scalar(
            text("SELECT id FROM identity_users WHERE id=:user AND state='active'"),
            {"user": user},
        ):
            raise IdentityError("forbidden")
        # Never approve by email or transfer an approval between identity subjects.
        connection.execute(
            text("""INSERT INTO beta_named_users(admission_id,user_id,state,expires_at)
            VALUES (:id,:user,'approved',clock_timestamp()+interval '48 hours')
            ON CONFLICT(admission_id,user_id) DO UPDATE SET state='approved',invitation_id=NULL,
            expires_at=EXCLUDED.expires_at"""),
            {"id": admission, "user": user},
        )

    @staticmethod
    def _snapshot(connection, program):
        return {
            "program": dict(program),
            "counts": counts(connection),
            "admissions": [
                dict(r)
                for r in connection.execute(
                    text("SELECT * FROM beta_admissions ORDER BY id")
                ).mappings()
            ],
            "events": [
                dict(r)
                for r in connection.execute(
                    text("SELECT * FROM beta_events ORDER BY sequence DESC LIMIT 100")
                ).mappings()
            ],
            "membership_counts": [
                dict(r)
                for r in connection.execute(
                    text(
                        "SELECT a.id,count(m.user_id) AS active_members FROM beta_admissions a LEFT JOIN memberships m ON m.organization_id=a.organization_id AND m.state='active' GROUP BY a.id ORDER BY a.id"
                    )
                ).mappings()
            ],
            "named_users": [
                dict(r)
                for r in connection.execute(
                    text("SELECT * FROM beta_named_users ORDER BY admission_id,user_id")
                ).mappings()
            ],
        }
