"""Customer consent adapter over existing Drive folders and encrypted grant custody."""
import base64
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import secrets
from urllib.parse import urlencode

from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.google_drive import DRIVE_SCOPE, GoogleDriveClient, load_oauth_config
from tabula_rasa.google_drive.errors import AUTHORIZATION_ENDPOINT
from tabula_rasa.google_drive.oauth import exchange_authorization_code
from tabula_rasa.infrastructure.postgres.control_plane import identity_operation
from tabula_rasa.infrastructure.postgres.connections import authorized_entity, lock_connection_entity

CALLBACK_PATH = '/api/v1/connections/google-drive/callback'
MANAGED_FOLDERS = ('storage_root', 'receipt_folder', 'backup_folder')


def load_customer_oauth(environment, origin):
    """Opt-in shared web client; never reuse a customer's or login-provider config."""
    filename = environment.get('TABULA_RASA_CUSTOMER_DRIVE_OAUTH_FILE')
    if not filename:
        return None
    try:
        path = Path(filename)
        if not path.is_absolute():
            raise ValueError
        config = load_oauth_config(json.loads(path.read_text(encoding='utf-8')))
        if origin + CALLBACK_PATH not in config.redirect_uris:
            raise ValueError
        return config
    except Exception:
        pass
    raise IdentityError('unavailable')


def fingerprint(value):
    return sha256(value.encode()).hexdigest()


class CustomerDriveOAuth:
    def __init__(self, manager, config, redirect_uri, *, exchange=exchange_authorization_code,
                 client_factory=GoogleDriveClient):
        self.manager, self.config, self.redirect_uri = manager, config, redirect_uri
        self.exchange, self.client_factory = exchange, client_factory

    def _scope(self, context):
        return dict(environment=self.manager.environment, organization_id=context.organization_id,
                    entity_id=context.entity_id, provider='google_drive')

    def status(self, context):
        result = asdict(self.manager.repository.status(context))
        with identity_operation(self.manager.repository.database, read_only=True) as connection:
            authorized_entity(connection, context, 'connections_manage')
            attempt = connection.execute(text('''SELECT outcome, expires_at>CURRENT_TIMESTAMP AS valid
                FROM drive_oauth_requests WHERE entity_id=:entity AND organization_id=:org'''),
                {'entity': context.entity_id, 'org': context.organization_id}).mappings().first()
        result['self_service_available'] = True
        row = self.manager.repository.grant(context, require_connected=False, action='connections_manage')
        if row:
            try:
                payload = self.manager.secrets.get(row['secret_ref'], **self._scope(context))
            except IdentityError as error:
                if error.code != 'unavailable':
                    raise
                if result['status'] == 'connected':
                    result['status'] = 'reconnect_required'
            else:
                result['authorized_account'] = payload.get('google_drive', {}).get('authorized_email')
        result['authorization_status'] = (None if not attempt or attempt['outcome'] == 'complete' else
            'connecting' if attempt['outcome'] == 'connecting' and attempt['valid'] else 'failed')
        return result

    def begin(self, context, session, revision):
        from tabula_rasa.infrastructure.postgres.connections import require_recent_authentication
        require_recent_authentication(session.authenticated_at)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        with identity_operation(self.manager.repository.database) as connection:
            authorized_entity(connection, context, 'connections_manage')
            lock_connection_entity(connection, context.entity_id)
            row = self.manager.repository._row(connection, context, lock=True)
            if (row['revision'] if row else 0) != revision:
                raise IdentityError('concurrent_write')
            reference = self.manager.secrets.put(dict(self._scope(context), verifier=verifier))
            connection.execute(text('''INSERT INTO drive_oauth_requests
                (entity_id,organization_id,actor_id,session_hash,state_hash,secret_ref,expected_revision,expires_at,outcome)
                VALUES (:entity,:org,:actor,:session,:state,:secret,:revision,CURRENT_TIMESTAMP+interval '5 minutes','connecting')
                ON CONFLICT (entity_id) DO UPDATE SET actor_id=EXCLUDED.actor_id,
                session_hash=EXCLUDED.session_hash,state_hash=EXCLUDED.state_hash,secret_ref=EXCLUDED.secret_ref,
                expected_revision=EXCLUDED.expected_revision,expires_at=EXCLUDED.expires_at,consumed=false,outcome='connecting' '''),
                dict(entity=context.entity_id, org=context.organization_id, actor=context.actor_id,
                     session=fingerprint(session.cache_id), state=fingerprint(state), secret=reference, revision=revision))
        challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip('=')
        return AUTHORIZATION_ENDPOINT + '?' + urlencode(dict(client_id=self.config.client_id,
            redirect_uri=self.redirect_uri, response_type='code', scope=DRIVE_SCOPE, access_type='offline',
            prompt='consent select_account', state=state, code_challenge=challenge, code_challenge_method='S256'))

    def complete(self, context, session, state, code, *, denied=False, reauthenticate):
        # Commit consumption before exchanging codes. Provider failure cannot resurrect state.
        with identity_operation(self.manager.repository.database) as connection:
            authorized_entity(connection, context, 'connections_manage')
            attempt = connection.execute(text('''UPDATE drive_oauth_requests SET consumed=true,outcome='failed'
                WHERE entity_id=:entity AND organization_id=:org AND actor_id=:actor
                AND session_hash=:session AND state_hash=:state AND NOT consumed
                AND expires_at>CURRENT_TIMESTAMP RETURNING *'''),
                dict(entity=context.entity_id, org=context.organization_id, actor=context.actor_id,
                     session=fingerprint(session.cache_id), state=fingerprint(state))).mappings().first()
            if attempt is None:
                raise IdentityError('forbidden')
        if denied or not code:
            return False
        # Serialize folder setup and connection writes with the existing entity lock.
        with identity_operation(self.manager.repository.database) as connection:
            lock_connection_entity(connection, context.entity_id)
            authorized_entity(connection, context, 'connections_manage')
            current = self.manager.repository._row(connection, context, lock=True)
            if (current['revision'] if current else 0) != attempt['expected_revision']:
                raise IdentityError('concurrent_write')
            latest = connection.scalar(text('SELECT state_hash FROM drive_oauth_requests WHERE entity_id=:entity FOR UPDATE'),
                                       {'entity': context.entity_id})
            if latest != fingerprint(state):
                raise IdentityError('concurrent_write')
            payload = self.manager.secrets.get(attempt['secret_ref'], **self._scope(context))
            grant = self.exchange(self.config, code, self.redirect_uri,
                                  code_verifier=payload['verifier'], include_account_id=True)
            account_id = grant.pop('authorized_account_id', None)
            if grant['scopes'] != [DRIVE_SCOPE] or not grant.get('authorized_email') or not account_id:
                return False
            profile = current['profile_key'] if current else 'business_' + fingerprint(context.entity_id)[:32]
            if current:
                binding = current['drive_recovery_binding']
                if binding is None:
                    # Existing rows enroll only from their original readable grant.
                    try:
                        old = self.manager.secrets.get(current['secret_ref'], **self._scope(context))
                    except IdentityError as error:
                        if error.code != 'unavailable':
                            raise
                        return False
                    previous = old['google_drive']
                    if (previous.get('authorized_email') or '').casefold() != grant['authorized_email'].casefold():
                        return False
                    folders = {key: previous[key] for key in MANAGED_FOLDERS}
                else:
                    if binding['account_fingerprint'] != fingerprint(account_id):
                        return False
                    folders = binding['folders']
                if folders['receipt_folder']['id'] != current['store_scope_key']:
                    return False
                grant.update(folders)
            client = self.client_factory(self.config, grant)
            if not current:
                root = client.create_storage_root(profile, 'Tabula Rasa receipts ' + profile[-8:])
                children = client.provision_storage_children(profile, root['id'])
                grant.update(storage_root=root, receipt_folder=children['receipts'], backup_folder=children['ledger-backups'])
                client = self.client_factory(self.config, grant)
            client.validate_storage_configuration(profile)
            # Recheck session selection, expiry and membership after provider I/O too.
            fresh = reauthenticate()
            if (fresh.cache_id != session.cache_id or fresh.selected is None
                    or fresh.selected.entity_id != context.entity_id
                    or fresh.selected.organization_id != context.organization_id):
                raise IdentityError('forbidden')
            authorized_entity(connection, context, 'connections_manage')
            scope = grant['receipt_folder']['id']
            binding = {'account_fingerprint': fingerprint(account_id),
                       'folders': {key: {field: grant[key][field] for field in ('id', 'name')}
                                   for key in MANAGED_FOLDERS}}
            config = dict(client_id=self.config.client_id, client_secret=self.config.client_secret,
                          redirect_uris=list(self.config.redirect_uris))
            reference = self.manager.secrets.put(dict(self._scope(context), profile_key=profile,
                scope_key=scope, google_drive_oauth=config, google_drive=grant))
            self.manager.repository._stage(connection, organization_id=context.organization_id,
                entity_id=context.entity_id, profile_key=profile, scope_key=scope, secret_ref=reference,
                expected_revision=attempt['expected_revision'])
            connection.execute(text('''UPDATE external_connections
                SET status='connected', drive_recovery_binding=CAST(:binding AS jsonb)
                WHERE entity_id=:entity AND provider='google_drive'
            '''),
                {'entity': context.entity_id, 'binding': json.dumps(binding)})
            connection.execute(text("UPDATE drive_oauth_requests SET outcome='complete' WHERE entity_id=:entity"),
                               {'entity': context.entity_id})
        return True
