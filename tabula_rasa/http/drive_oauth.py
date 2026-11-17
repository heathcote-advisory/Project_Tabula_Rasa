"""Customer consent adapter over existing Drive folders and encrypted grant custody."""
import base64
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import unicodedata
from urllib.parse import urlencode

from sqlalchemy import text

from tabula_rasa.application.identity import IdentityError
from tabula_rasa.google_drive import DRIVE_SCOPE, GoogleDriveClient, load_oauth_config
from tabula_rasa.google_drive.errors import AUTHORIZATION_ENDPOINT, GoogleDriveError
from tabula_rasa.google_drive.oauth import exchange_authorization_code
from tabula_rasa.infrastructure.postgres.control_plane import identity_operation
from tabula_rasa.infrastructure.postgres.connections import (
    authorized_entity, lock_connection_entity, require_recent_authentication,
)

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


def load_picker(environment):
    key = environment.get('TABULA_RASA_DRIVE_PICKER_KEY', '')
    project = environment.get('TABULA_RASA_DRIVE_PICKER_PROJECT_NUMBER', '')
    if not key and not project:
        return None
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', key) or not re.fullmatch(r'[0-9]{1,20}', project):
        raise IdentityError('unavailable')
    return key, project


def clean_folder_name(value):
    name = (value or '').strip()
    if not 1 <= len(name) <= 255 or name in {'.', '..'} or any(
        char in '/\\' or unicodedata.category(char).startswith('C') for char in name
    ):
        raise IdentityError('validation')
    return name


def fingerprint(value):
    return sha256(value.encode()).hexdigest()


class CustomerDriveOAuth:
    def __init__(self, manager, config, redirect_uri, *, exchange=exchange_authorization_code,
                 client_factory=GoogleDriveClient, picker=None):
        self.manager, self.config, self.redirect_uri = manager, config, redirect_uri
        self.exchange, self.client_factory = exchange, client_factory
        self.picker = picker

    def _scope(self, context):
        return dict(environment=self.manager.environment, organization_id=context.organization_id,
                    entity_id=context.entity_id, provider='google_drive')

    def status(self, context, session=None):
        result = asdict(self.manager.repository.status(context))
        with identity_operation(self.manager.repository.database, read_only=True) as connection:
            authorized_entity(connection, context, 'connections_manage')
            attempt = connection.execute(text('''SELECT outcome, expires_at>CURRENT_TIMESTAMP AS valid,
                actor_id, session_hash, state_hash, secret_ref, setup_selection IS NOT NULL AS reserved
                FROM drive_oauth_requests WHERE entity_id=:entity AND organization_id=:org'''),
                {'entity': context.entity_id, 'org': context.organization_id}).mappings().first()
            setup = (attempt and attempt['outcome'] == 'setup' and attempt['valid']
                     and session and attempt['actor_id'] == context.actor_id
                     and attempt['session_hash'] == fingerprint(session.cache_id))
            if setup:
                name = connection.scalar(text('SELECT name FROM business_entities WHERE id=:entity'),
                                         {'entity': context.entity_id})
                suffix = ''.join(char if char not in '/\\' and not unicodedata.category(char).startswith('C')
                                 else ' ' for char in name)
                result['folder_setup'] = dict(default_name=('Tabula Rasa - ' + suffix.strip())[:255].rstrip(),
                                              picker_available=bool(self.picker), reserved=attempt['reserved'],
                                              setup_id=attempt['state_hash'])
        if setup:
            try:
                payload = self.manager.secrets.get(attempt['secret_ref'], **self._scope(context))
            except IdentityError as error:
                if error.code != 'unavailable':
                    raise
                result.pop('folder_setup')
                setup = False
            else:
                result['authorized_account'] = payload['setup_grant']['authorized_email']
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
        result['authorization_status'] = (None if setup or not attempt or attempt['outcome'] in {'complete', 'cancelled'} else
            'connecting' if attempt['outcome'] == 'connecting' and attempt['valid'] else 'failed')
        return result

    def begin(self, context, session, revision):
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
            self._require_selected(context, session, reauthenticate)
            authorized_entity(connection, context, 'connections_manage')
            if not current:
                selection = attempt['setup_selection']
                if selection and selection['account'] != fingerprint(account_id):
                    return False
                reference = self.manager.secrets.put(dict(self._scope(context), verifier=payload['verifier'],
                    setup_grant=grant, account_fingerprint=fingerprint(account_id)))
                connection.execute(text("""UPDATE drive_oauth_requests
                    SET outcome='setup', secret_ref=:secret, expires_at=CURRENT_TIMESTAMP+interval '10 minutes'
                    WHERE entity_id=:entity"""), {'entity': context.entity_id, 'secret': reference})
                return True
            profile = current['profile_key']
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
            client.validate_storage_configuration(profile)
            self._require_selected(context, session, reauthenticate)
            self._commit(connection, context, attempt, profile, grant, fingerprint(account_id))
        return True

    @staticmethod
    def _require_selected(context, session, reauthenticate):
        fresh = reauthenticate()
        if (fresh.cache_id != session.cache_id or fresh.selected is None
                or fresh.selected.entity_id != context.entity_id
                or fresh.selected.organization_id != context.organization_id):
            raise IdentityError('forbidden')

    def _commit(self, connection, context, attempt, profile, grant, account):
        authorized_entity(connection, context, 'connections_manage')
        scope = grant['receipt_folder']['id']
        binding = {'account_fingerprint': account,
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
        connection.execute(text("UPDATE drive_oauth_requests SET outcome='complete', setup_selection=NULL WHERE entity_id=:entity"),
                           {'entity': context.entity_id})

    def _setup_attempt(self, connection, context, session, command):
        lock_connection_entity(connection, context.entity_id)
        authorized_entity(connection, context, 'connections_manage')
        current = self.manager.repository._row(connection, context, lock=True)
        if current or command.revision != 0:
            raise IdentityError('concurrent_write')
        attempt = connection.execute(text("""SELECT * FROM drive_oauth_requests
            WHERE entity_id=:entity AND organization_id=:org AND actor_id=:actor
              AND session_hash=:session AND state_hash=:setup AND expected_revision=:revision
              AND outcome='setup' AND expires_at>CURRENT_TIMESTAMP FOR UPDATE"""),
            dict(entity=context.entity_id, org=context.organization_id, actor=context.actor_id,
                 session=fingerprint(session.cache_id), setup=command.setup_id,
                 revision=command.revision)).mappings().first()
        if attempt is None:
            raise IdentityError('forbidden')
        return attempt

    def setup(self, context, session, command, *, reauthenticate):
        require_recent_authentication(session.authenticated_at)
        try:
            with identity_operation(self.manager.repository.database) as connection:
                attempt = self._setup_attempt(connection, context, session, command)
                if command.action == 'cancel':
                    connection.execute(text("UPDATE drive_oauth_requests SET outcome='cancelled' WHERE entity_id=:entity"),
                                       {'entity': context.entity_id})
                    return {}
                payload = self.manager.secrets.get(attempt['secret_ref'], **self._scope(context))
                grant = payload['setup_grant']
                client = self.client_factory(self.config, grant)
                self._require_selected(context, session, reauthenticate)
                selection = attempt['setup_selection']
                if command.action == 'picker':
                    if not self.picker or selection:
                        raise IdentityError('forbidden')
                    key, project = self.picker
                    token = client.access_token()
                    self._require_selected(context, session, reauthenticate)
                    authorized_entity(connection, context, 'connections_manage')
                    return dict(access_token=token, developer_key=key, project_number=project)
                if selection is None:
                    name = clean_folder_name(command.name)
                    if command.parent_id and not self.picker:
                        raise IdentityError('forbidden')
                    parent = client.storage_parent(command.parent_id or 'root')
                    selection = dict(name=name, parent=parent, ids=client.new_storage_ids(),
                                     account=payload['account_fingerprint'])
                    connection.execute(text("""UPDATE drive_oauth_requests SET setup_selection=CAST(:value AS jsonb)
                        WHERE entity_id=:entity"""), {'entity': context.entity_id, 'value': json.dumps(selection)})
                elif command.name is not None or command.parent_id is not None:
                    raise IdentityError('concurrent_write')
            with identity_operation(self.manager.repository.database) as connection:
                latest = self._setup_attempt(connection, context, session, command)
                if latest['setup_selection'] != selection:
                    raise IdentityError('concurrent_write')
                self._require_selected(context, session, reauthenticate)
                profile = 'business_' + fingerprint(context.entity_id)[:32]
                root_id, *child_ids = selection['ids']
                root = client.create_storage_root(profile, selection['name'], parent_id=selection['parent'],
                                                  file_id=root_id)
                children = client.provision_storage_children(profile, root['id'], file_ids=tuple(child_ids))
                grant.update(storage_root=root, receipt_folder=children['receipts'], backup_folder=children['ledger-backups'])
                self.client_factory(self.config, grant).validate_storage_configuration(profile)
                self._require_selected(context, session, reauthenticate)
                self._commit(connection, context, latest, profile, grant, selection['account'])
        except GoogleDriveError:
            raise IdentityError('unavailable') from None
        return {}
