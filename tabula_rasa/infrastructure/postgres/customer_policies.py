"""Minimal immutable acknowledgement evidence, scoped to the authenticated person."""

from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from .control_plane import identity_operation
from .workspaces import PostgresWorkspaceRepository


class PostgresPolicyAcknowledgements:
    def __init__(self, database):
        self.database = database

    def acknowledged(self, session, policies):
        with identity_operation(self.database, read_only=True) as c:
            return bool(c.scalar(text('''SELECT 1 FROM customer_policy_acknowledgements
                WHERE user_id=:actor AND bundle_sha256=:digest'''),
                {'actor': session.user_id, 'digest': policies.digest}))

    def accept(self, session, policies, digest):
        if not policies.beta or digest != policies.digest:
            raise IdentityError('conflict')
        with identity_operation(self.database) as c:
            PostgresWorkspaceRepository(self.database)._users(c, session.user_id)
            c.execute(text('''INSERT INTO customer_policy_acknowledgements
                (user_id,bundle_sha256,policy_version) VALUES (:actor,:digest,:version)
                ON CONFLICT (user_id,bundle_sha256) DO NOTHING'''),
                {'actor': session.user_id, 'digest': digest, 'version': policies.version})
        return True
