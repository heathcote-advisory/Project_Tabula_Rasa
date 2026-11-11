"""Owner recovery from an empty disposable custody database with synthetic Drive."""
from hashlib import sha256
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from sqlalchemy import text

from tabula_rasa.google_drive.errors import GoogleDriveAccessError
from tabula_rasa.http.connections import TenantConnections
from tabula_rasa.http.drive_oauth import CALLBACK_PATH, CustomerDriveOAuth
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tabula_rasa.infrastructure.postgres.connection_secrets import PostgresConnectionSecrets
from tests.drive_fixtures import receipt_drive_file
from tests.postgres.core_support import account, category, expense
from tests.postgres.test_connection_custody import custody as custody
from tests.postgres.test_customer_drive_oauth import begin, finish, fixture
from tests.postgres.test_identity import verified
from tests.postgres.test_identity_http import client_for


def login(client, auth, subject):
    auth.provider.authorization_url.return_value = 'https://id.example.test/authorize'
    auth.provider.verify.return_value = verified(subject)
    assert client.get('/api/v1/auth/login', follow_redirects=False).status_code == 303
    state = auth.provider.authorization_url.call_args.args[0].state
    assert client.get('/api/v1/auth/callback', params={'code': 'synthetic-login', 'state': state},
                      follow_redirects=False).status_code == 303
    session = client.get('/api/v1/session')
    assert session.status_code == 200
    return {'origin': auth.settings.public_origin, 'x-csrf-token': session.json()['csrf_token']}


def attach_runtime(client, manager, database):
    runtime = client.app.state.runtime
    runtime.connections = manager

    def services(context):
        return create_postgres_services(database, context.entity_id,
            document_store=manager.document_store(context), document_store_kind='google_drive')

    runtime.service_factory = services


def reconnect(client, headers, entity, revision, account_code):
    url = f'/api/v1/entities/{entity}/connections/google-drive/authorize'
    response = client.post(url, json={'revision': revision}, headers=headers)
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.json()['authorization_url']).query)['state'][0]
    return client.get(CALLBACK_PATH, params={'state': state, 'code': account_code},
                      follow_redirects=False)


def test_owners_recover_both_businesses_after_complete_ciphertext_loss(database, custody, tmp_path, monkeypatch):
    engine, key, url = custody
    _, manager, consent, _, alpha, beta, sessions = fixture(database, tmp_path)
    manager.secrets = PostgresConnectionSecrets(url, key)
    drives, originals = {}, {}
    monkeypatch.setattr('tabula_rasa.google_drive.GoogleDriveClient',
                        lambda config, grant: drives[grant['receipt_folder']['id']])
    for index, (context, session) in enumerate(zip((alpha, beta), sessions), 1):
        entity = context.entity_id
        assert finish(manager, context, session, begin(manager, context, session), entity)
        grant = manager.repository.grant(context)
        payload = manager.secrets.get(grant['secret_ref'], **manager.oauth._scope(context))
        folders = {name: payload['google_drive'][name] for name in
                   ('storage_root', 'receipt_folder', 'backup_folder')}
        content = b'%PDF synthetic ' + entity.encode()
        receipt_id, file_id = str(index) * 32, 'synthetic-file-' + entity
        drive_file = receipt_drive_file(file_id, 'receipt.pdf', profile_key=grant['profile_key'],
            receipt_id=receipt_id, parent_id=grant['store_scope_key'],
            sha256=sha256(content).hexdigest(), size=len(content))
        drive_file['webViewLink'] = 'https://drive.google.com/file/d/' + file_id + '/view'
        drive = drives[grant['store_scope_key']] = Mock()
        drive.receipt_file.return_value = drive_file
        drive.download_file.return_value = content
        with database.transaction() as connection:
            account(connection, entity, 'cash')
            category(connection, entity, 'office', kind='Expense')
        store = manager.document_store(context)
        services = create_postgres_services(database, entity, document_store=store,
                                            document_store_kind='google_drive')
        services.transactions.create(transaction_id='txn', **expense())
        services.receipts.link('txn', store.metadata(entity, file_id))
        originals[entity] = (grant, folders, receipt_id, content, services.receipts.repository.associations(entity))

    client, auth, _ = client_for(database)
    paths = {entity: f'/api/v1/entities/{entity}' for entity in originals}
    ledger = {}
    with client:
        attach_runtime(client, manager, database)
        for entity, subject in [('alpha', 'alice'), ('beta', 'bob')]:
            login(client, auth, subject)
            response = client.get(paths[entity] + '/transactions')
            assert response.status_code == 200
            ledger[entity] = response.json()
            assert ledger[entity]['items']
            receipt_id = originals[entity][2]
            assert client.get(f'{paths[entity]}/documents/{receipt_id}/content').content == originals[entity][3]

    with engine.begin() as connection:
        assert connection.scalar(text('SELECT count(*) FROM connection_custody.grants')) == 4
        connection.execute(text('TRUNCATE connection_custody.grants'))
        assert connection.scalar(text('SELECT count(*) FROM connection_custody.grants')) == 0
    key = Fernet.generate_key().decode()
    manager.secrets = PostgresConnectionSecrets(url, key)

    client, auth, _ = client_for(database)
    with client:
        attach_runtime(client, manager, database)
        for context, subject, other in [(alpha, 'alice', 'beta'), (beta, 'bob', 'alpha')]:
            entity = context.entity_id
            headers = login(client, auth, subject)
            path = paths[entity]
            grant, folders, receipt_id, content, links = originals[entity]
            assert client.get(path + '/transactions').json() == ledger[entity]
            assert client.get(f'{path}/documents/{receipt_id}/content').status_code == 503
            assert client.get(f'{paths[other]}/documents/{originals[other][2]}/content').status_code == 403
            assert client.post(paths[other] + '/connections/google-drive/authorize',
                               headers=headers, json={'revision': 1}).status_code == 403
            manager.validate_migrated_binding(organization_id=context.organization_id,
                entity_id=entity, profile_key=grant['profile_key'], scope_key=grant['store_scope_key'])
            status = client.get(path + '/connections/google-drive')
            assert status.status_code == 200
            assert status.json()['status'] == 'reconnect_required'
            revision = status.json()['revision']
            wrong_accounts = (other, 'alpha-replacement') if entity == 'alpha' else (other,)
            for wrong in wrong_accounts:
                validations = consent.validate_storage_configuration.call_count
                assert reconnect(client, headers, entity, revision, wrong).headers['location'] == '/data?drive=failed'
                assert consent.validate_storage_configuration.call_count == validations
                assert manager.repository.grant(context) == grant
            consent.validate_storage_configuration.side_effect = GoogleDriveAccessError('synthetic folder unavailable')
            assert reconnect(client, headers, entity, revision, entity).headers['location'] == '/data?drive=failed'
            assert manager.repository.grant(context) == grant
            consent.validate_storage_configuration.side_effect = None
            assert reconnect(client, headers, entity, revision, entity).headers['location'] == '/data'
            updated = manager.repository.grant(context)
            assert updated['secret_ref'] != grant['secret_ref']
            assert updated['status'] == 'connected'
            assert updated['store_scope_key'] == grant['store_scope_key']
            payload = manager.secrets.get(updated['secret_ref'], **manager.oauth._scope(context))
            assert {name: payload['google_drive'][name] for name in folders} == folders
            store = manager.document_store(context)
            services = create_postgres_services(database, entity, document_store=store,
                                                document_store_kind='google_drive')
            assert services.receipts.repository.associations(entity) == links

    assert consent.create_storage_root.call_count == consent.provision_storage_children.call_count == 2
    for drive in drives.values():
        drive.upload_receipt.assert_not_called()
        drive.delete_file.assert_not_called()

    # Recreate the application, manager and custody adapter over the same retained databases.
    restarted = TenantConnections(database, 'test', PostgresConnectionSecrets(url, key))
    restarted.oauth = CustomerDriveOAuth(restarted, manager.oauth.config, manager.oauth.redirect_uri,
                                        exchange=manager.oauth.exchange, client_factory=manager.oauth.client_factory)
    client, auth, _ = client_for(database)
    with client:
        attach_runtime(client, restarted, database)
        for entity, subject in [('alpha', 'alice'), ('beta', 'bob')]:
            login(client, auth, subject)
            path = paths[entity]
            _, _, receipt_id, content, _ = originals[entity]
            response = client.get(f'{path}/documents/{receipt_id}/content')
            assert response.status_code == 200 and response.content == content
            assert client.get(path + '/transactions').json() == ledger[entity]
