"""Canonical links, atomic location promotion and isolated byte recovery."""
from dataclasses import replace
from unittest.mock import Mock

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import text

from scripts.manage_document_storage import execute
from tabula_rasa.application.document_recovery import copy_entity_documents, verify_entity_documents
from tabula_rasa.application.documents import DocumentError, DocumentValidationError
from tabula_rasa.application.identity import IdentityError
from tabula_rasa.errors import StorageConflictError
from tabula_rasa.http.connections import TenantConnections
from tabula_rasa.infrastructure.connection_secrets import ConnectionSecrets
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services, create_postgres_export_service
from tabula_rasa.infrastructure.postgres.documents import PostgresReceiptRepository
from tabula_rasa.infrastructure.postgres.managed_documents import install_managed_connection
from tests.postgres.core_support import expense, seed_core
from tests.postgres.test_identity import setup as identity_setup
from tests.postgres.test_tenant_connections import context
from tests.s3_support import FakeS3, s3_store
from tests.test_application_documents import FakeDocumentStore
from tests.test_s3_documents import upload


class SourceDrive(FakeDocumentStore):
    scope_key = 'synthetic-drive-folder'

    def upload(self, entity_id, upload):
        result = super().upload(entity_id, upload)
        result = replace(result, storage_key='drivefile_' + result.metadata.document_id,
                         metadata=replace(result.metadata, stored_filename='receipt (1).pdf'))
        self.files[(entity_id, upload.document_id)] = result
        return result


def setup(database):
    seed_core(database)
    source, target = SourceDrive(), s3_store()
    services = create_postgres_services(database, 'alpha', document_store=source, document_store_kind='google_drive')
    services.transactions.create(transaction_id='txn', **expense())
    for identity in ['a' * 32, 'b' * 32]:
        doc = source.upload('alpha', upload(identity))
        services.receipts.repository.register_document(doc)
    services.receipts.link('txn', source.files[('alpha', 'a' * 32)])
    return services, source, target


def test_dry_run_optional_copy_promotion_links_originals_and_edit_copy_contract(database):
    services, source, target = setup(database)
    repository = services.receipts.repository
    before = services.transactions.list()[0]
    expected = repository.retained_documents()
    links = services.receipts.references('txn')
    result = copy_entity_documents(repository, source, target)
    assert result.verified == 2 and result.dry_run and not result.promoted
    assert target.inventory('alpha').documents == ()
    # Copy-only is a durable checkpoint, with the source still authoritative.
    copy_entity_documents(repository, source, target, dry_run=False)
    assert repository.retained_documents() == expected
    result = copy_entity_documents(repository, source, target, dry_run=False, promote=True)
    assert result.promoted and source.delete_calls == 0
    managed = create_postgres_services(database, 'alpha', document_store=target, document_store_kind='s3')
    managed.receipts.repository.validate_binding()
    assert verify_entity_documents(managed.receipts.repository, target) == 2
    document, = managed.receipts.references('txn')
    assert document.metadata == links[0].metadata
    after = managed.transactions.list()[0]
    # Bookkeeping records/postings were not rewritten, while projections resolve new locations.
    assert {k: v for k, v in before.items() if k != 'receipt_references'} == {k: v for k, v in after.items() if k != 'receipt_references'}
    assert after['receipt_references'][0]['id'] == document.metadata.document_id
    managed.transactions.update('txn', description='Edited with managed receipt', expected=after)
    managed.receipts.unlink_document('txn', document.metadata.document_id)
    assert managed.receipts.download(document.metadata.document_id)[1] == upload().content
    managed.transactions.create(transaction_id='copy', **expense(), receipt_references=after['receipt_references'])
    assert managed.receipts.references('copy') == (document,)
    managed.receipts.unlink_document('copy', document.metadata.document_id)
    managed.receipts.link_document('txn', document.metadata.document_id)
    assert managed.receipts.references('txn') == (document,)
    with database.transaction(read_only=True) as connection:
        originals = connection.execute(text('SELECT storage_key FROM document_locations WHERE entity_id=\'alpha\'')).scalars().all()
    assert set(originals) == {item.storage_key for item in expected}
    assert len(create_postgres_export_service(database, 'alpha').export()['domains']) == 27


def test_partial_provider_failure_restart_and_stale_metadata_are_blocking(database):
    services, source, target = setup(database)
    repo = services.receipts.repository
    original_upload = target.upload
    count = 0
    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise DocumentError('synthetic outage')
        return original_upload(*args)
    target.upload = fail_second
    expected = repo.retained_documents()
    with pytest.raises(DocumentError):
        copy_entity_documents(repo, source, target, dry_run=False, promote=True)
    assert repo.retained_documents() == expected and len(target.client.objects) == 1
    target.upload = original_upload
    copy_entity_documents(repo, source, target, dry_run=False)
    assert len(target.client.objects) == 2  # Replay reused its stable destination.
    destinations = tuple(target.metadata('alpha', doc.storage_key) for doc in target.inventory('alpha').documents)
    destinations = tuple(sorted(destinations, key=lambda doc: doc.metadata.document_id))
    repo.register_document(source.upload('alpha', upload('c' * 32)))
    with pytest.raises(StorageConflictError, match='changed during copy'):
        repo.promote_locations(expected, destinations, target_kind='s3', target_scope=target.scope_key)
    assert source.delete_calls == 0


def test_failed_connection_promotion_rolls_back_all_locations_and_keeps_copies(database):
    services, source, target = setup(database)
    repo = services.receipts.repository
    expected = repo.retained_documents()
    progress = []
    with pytest.raises(DocumentValidationError, match='different organization'):
        copy_entity_documents(repo, source, target, dry_run=False, promote=True, progress=progress.append,
            connection_binding=dict(organization_id='wrong-org', profile_key='alpha',
                                    secret_ref='a' * 32, expected_revision=0))
    assert repo.retained_documents() == expected and len(target.client.objects) == 2
    assert progress == [{'stage': 'target_verified', 'verified': count, 'total': 2} for count in (1, 2)]
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM document_locations')) == 0
        assert connection.scalar(text("SELECT count(*) FROM documents WHERE store_kind='s3'")) == 0
    assert source.delete_calls == 0


def test_missing_corrupt_foreign_metadata_and_unregistered_receipts_stop_copy(database):
    services, source, target = setup(database)
    repo = services.receipts.repository
    original = source.contents[('alpha', 'a' * 32)]
    source.contents[('alpha', 'a' * 32)] = b'corrupt'
    with pytest.raises(DocumentValidationError, match='match retained metadata'):
        copy_entity_documents(repo, source, target, dry_run=False, promote=True)
    assert not target.client.objects
    source.contents[('alpha', 'a' * 32)] = original
    with pytest.raises(DocumentValidationError, match='different business entity'):
        copy_entity_documents(repo, source, s3_store('beta'), dry_run=False, promote=True)
    source.upload('alpha', upload('c' * 32))
    with pytest.raises(DocumentValidationError, match='unregistered'):
        copy_entity_documents(repo, source, target, dry_run=False, promote=True)
    assert repo.store_kind == 'google_drive' and source.delete_calls == 0


def test_isolated_recovery_uses_retained_metadata_and_pinned_version_after_delete_marker(database):
    services, source, target = setup(database)
    copy_entity_documents(services.receipts.repository, source, target, dry_run=False, promote=True)
    repo = PostgresReceiptRepository(database, 'alpha', store_kind='s3', store_scope_key=target.scope_key)
    document, other = repo.retained_documents()
    target.delete('alpha', document.storage_key)
    assert verify_entity_documents(repo, target) == 2
    # Restore/copy bytes into an isolated namespace; originals and versions remain.
    isolated = s3_store(namespace='recovery/' + '2' * 32)
    copy_entity_documents(repo, target, isolated, dry_run=False, recovery_snapshot=True)
    assert {doc.metadata for doc in isolated.inventory('alpha').documents} == {document.metadata, other.metadata}
    key, version = target._locator(document.storage_key)
    target.client.objects.pop((target.bucket, key, version))
    with pytest.raises(DocumentError, match='missing'):
        verify_entity_documents(repo, target)


def test_managed_binding_two_org_fresh_authorization_disconnect_and_no_drive_fallback(database, tmp_path):
    identity, alice, bob, a, b = identity_setup(database)
    secrets = ConnectionSecrets(tmp_path, Fernet.generate_key().decode())
    stores = {entity: s3_store(entity, FakeS3()) for entity in ('alpha', 'beta')}
    for entity, org in [('alpha', a), ('beta', b)]:
        store = stores[entity]
        reference = secrets.put(dict(environment='staging', organization_id=org, entity_id=entity,
            provider='s3', profile_key=entity, scope_key=store.scope_key))
        with database.transaction() as connection:
            install_managed_connection(connection, entity, store.scope_key, organization_id=org,
                profile_key=entity, secret_ref=reference, expected_revision=0)
    manager = TenantConnections(database, 'staging', secrets, store_factory=lambda row, payload, **kw: stores[row['entity_id']])
    alpha, beta = context(alice, a, 'alpha'), context(bob, b, 'beta')
    active = manager.document_store(alpha)
    assert active.store_kind == 's3'
    document = active.upload('alpha', upload())
    assert active.fetch('alpha', document.storage_key) == upload().content
    with pytest.raises(IdentityError):
        manager.document_store(replace(alpha, entity_id='beta'))
    with database.transaction() as connection:
        connection.execute(text("UPDATE external_connections SET status='disconnected' WHERE entity_id='alpha' AND provider='s3'"))
    with pytest.raises(IdentityError):
        active.fetch('alpha', document.storage_key)
    assert manager.document_store(alpha).store_kind == 's3'  # No fallback.
    assert manager.document_store(beta).inventory('beta').documents == ()
    identity.set_membership(a, bob.id, 'owner')
    identity.set_membership(a, alice.id, 'owner', active=False)
    secrets.get = Mock(side_effect=AssertionError('No secret lookup after denial'))
    with pytest.raises(IdentityError, match='forbidden'):
        active.fetch('alpha', document.storage_key)
    secrets.get.assert_not_called()


def test_operator_plan_new_entity_binding_and_restart_verifier(database, tmp_path, monkeypatch):
    _, _, _, org, other_org = identity_setup(database)
    secrets = ConnectionSecrets(tmp_path, Fernet.generate_key().decode())
    target = s3_store()
    monkeypatch.setattr('scripts.manage_document_storage.provider_store', lambda *args, **kw: target)
    payload = dict(environment='staging', organization_id=org, entity_id='alpha', provider='s3', profile_key='alpha',
                   scope_key=target.scope_key, s3={'synthetic': True})
    plan = execute(database, secrets, payload, operation='bind', environment='staging')
    assert not plan['applied']
    from tests.postgres.document_restore_support import cloned_database
    with cloned_database(database) as other_database:
        with pytest.raises(DocumentValidationError, match='exact reviewed plan'):
            execute(other_database, secrets, payload, operation='bind', environment='staging', apply=True,
                    expected_plan=plan['plan_sha256'], approval_sha256='a' * 64)
    with pytest.raises(DocumentValidationError, match='exact reviewed plan'):
        execute(database, secrets, payload, operation='bind', environment='staging', apply=True,
                expected_plan='incorrect', approval_sha256='a' * 64)
    result = execute(database, secrets, payload, operation='bind', environment='staging', apply=True,
                     expected_plan=plan['plan_sha256'], approval_sha256='a' * 64)
    assert result['applied']
    assert execute(database, secrets, payload, operation='copy', environment='staging')['already_managed']
    repo = PostgresReceiptRepository(database, 'alpha', store_kind='s3', store_scope_key=target.scope_key)
    document = target.upload('alpha', upload())
    repo.register_document(document)
    before_calls = list(target.client.calls)
    for changed in ({'profile_key': 'beta'}, {'organization_id': other_org}):
        for operation in ('copy', 'verify', 'rotate'):
            with pytest.raises(DocumentValidationError, match='organization/profile ownership'):
                execute(database, secrets, {**payload, **changed}, operation=operation, environment='staging')
    assert target.client.calls == before_calls
    assert repo.retained_documents() == (document,)
    rotation = execute(database, secrets, payload, operation='rotate', environment='staging', revision=1)
    assert rotation['verified'] == 1 and not rotation['applied']
    result = execute(database, secrets, payload, operation='rotate', environment='staging', revision=1, apply=True,
                     expected_plan=rotation['plan_sha256'], approval_sha256='b' * 64)
    assert result['applied'] and repo.retained_documents() == (document,)
    from tabula_rasa.infrastructure.postgres.cutover import database_identity
    identity = database_identity(database)
    with pytest.raises(DocumentValidationError, match='isolated staging database'):
        execute(database, secrets, payload, operation='restore', environment='staging', source_payload=payload,
                source_database_sha256=identity, target_database_sha256=identity)


@pytest.mark.parametrize('apply', [False, True])
def test_optional_s3_restore_rejects_original_scope_before_replay(database, tmp_path, monkeypatch, apply):
    from tests.postgres.document_restore_support import cloned_database
    from tabula_rasa.infrastructure.postgres.identity import database_identity
    _, _, _, org, _ = identity_setup(database)
    secrets = ConnectionSecrets(tmp_path, Fernet.generate_key().decode())
    store = s3_store()
    monkeypatch.setattr('scripts.manage_document_storage.provider_store', lambda *args, **kw: store)
    payload = dict(environment='staging', organization_id=org, entity_id='alpha', provider='s3',
                   profile_key='alpha', scope_key=store.scope_key, s3={'synthetic': True})
    plan = execute(database, secrets, payload, operation='bind', environment='staging')
    execute(database, secrets, payload, operation='bind', environment='staging', apply=True,
            expected_plan=plan['plan_sha256'], approval_sha256='a' * 64)
    repo = PostgresReceiptRepository(database, 'alpha', store_kind='s3', store_scope_key=store.scope_key)
    repo.register_document(store.upload('alpha', upload()))
    before = list(store.client.calls)
    with cloned_database(database) as restored:
        with pytest.raises(DocumentValidationError, match='distinct source and destination'):
            execute(restored, secrets, payload, operation='restore', environment='staging', source_payload=payload,
                    source_database_sha256=database_identity(database),
                    target_database_sha256=database_identity(restored), apply=apply)
        assert PostgresReceiptRepository(restored, 'alpha', store_kind='s3',
            store_scope_key=store.scope_key).retained_documents() == repo.retained_documents()
    assert store.client.calls == before


def test_operator_drive_copy_installs_managed_connection_preserving_original_grant(database, tmp_path, monkeypatch):
    from tabula_rasa.infrastructure.postgres.control_plane import PostgresIdentityRepository
    from tabula_rasa.infrastructure.postgres.connections import PostgresConnections
    from tests.postgres.test_identity import verified
    services, source, target = setup(database)
    identity = PostgresIdentityRepository(database)
    user = identity.resolve_user(verified())
    org = identity.create_organization(user.id, 'Synthetic')
    identity.bind_entity(org, 'alpha')
    secrets = ConnectionSecrets(tmp_path, Fernet.generate_key().decode())
    source_payload = dict(environment='staging', organization_id=org, entity_id='alpha', provider='google_drive',
                          profile_key='alpha', scope_key=source.scope_key)
    source_reference = secrets.put(source_payload)
    connections = PostgresConnections(database)
    connections.stage(organization_id=org, entity_id='alpha', profile_key='alpha', scope_key=source.scope_key,
                      secret_ref=source_reference, expected_revision=0)
    with database.transaction() as connection:
        connection.execute(text("UPDATE external_connections SET status='connected' WHERE entity_id='alpha' AND provider='google_drive'"))
    monkeypatch.setattr('scripts.manage_document_storage.google_store', lambda *args, **kw: source)
    monkeypatch.setattr('scripts.manage_document_storage.provider_store', lambda *args, **kw: target)
    payload = dict(environment='staging', organization_id=org, entity_id='alpha', provider='s3', profile_key='alpha',
                   scope_key=target.scope_key, s3={'synthetic': True})
    plan = execute(database, secrets, payload, operation='copy', environment='staging')
    assert not target.client.objects
    result = execute(database, secrets, payload, operation='copy', environment='staging', apply=True,
                     expected_plan=plan['plan_sha256'], approval_sha256='a' * 64)
    assert result['promoted'] and result['verified'] == 2
    assert execute(database, secrets, payload, operation='copy', environment='staging')['already_managed']
    with database.transaction(read_only=True) as connection:
        original = connection.execute(text("SELECT * FROM external_connections WHERE entity_id='alpha' AND provider='google_drive'"))
        original = original.mappings().one()
        assert original['secret_ref'] == source_reference and original['status'] == 'connected'
        assert connection.scalar(text('SELECT count(*) FROM document_locations')) == 2
    assert source.delete_calls == 0


def test_s3_restore_rechecks_approved_link_point_at_promotion(database, tmp_path, monkeypatch):
    from tests.postgres.document_restore_support import cloned_database
    from tabula_rasa.infrastructure.postgres.identity import database_identity
    from tabula_rasa.infrastructure.postgres.control_plane import PostgresIdentityRepository
    from tests.postgres.test_identity import verified
    services, drive, source = setup(database)
    copy_entity_documents(services.receipts.repository, drive, source, dry_run=False, promote=True)
    identity = PostgresIdentityRepository(database)
    org = identity.create_organization(identity.resolve_user(verified()).id, 'Synthetic')
    identity.bind_entity(org, 'alpha')
    secrets = ConnectionSecrets(tmp_path, Fernet.generate_key().decode())
    source_payload = dict(environment='staging', organization_id=org, entity_id='alpha', provider='s3',
                          profile_key='alpha', scope_key=source.scope_key, s3={'synthetic': True})
    with database.transaction() as connection:
        install_managed_connection(connection, 'alpha', source.scope_key, organization_id=org,
            profile_key='alpha', secret_ref=secrets.put(source_payload), expected_revision=0)
    target = s3_store(namespace='staging/' + '2' * 32)
    payload = {**source_payload, 'scope_key': target.scope_key}
    monkeypatch.setattr('scripts.manage_document_storage.provider_store',
        lambda row, payload, **kw: source if payload['scope_key'] == source.scope_key else target)
    with cloned_database(database) as restored:
        kwargs = dict(operation='restore', environment='staging', source_payload=source_payload, revision=1,
            source_database_sha256=database_identity(database), target_database_sha256=database_identity(restored))
        plan = execute(restored, secrets, payload, **kwargs)
        repo = PostgresReceiptRepository(restored, 'alpha', store_kind='s3', store_scope_key=source.scope_key)
        expected = repo.retained_documents()
        original_upload = target.upload
        def change_link(*args):
            destination = original_upload(*args)
            document, = repo.references('alpha', 'txn')
            repo.unlink('alpha', 'txn', document.storage_key)
            monkeypatch.setattr(target, 'upload', original_upload)
            return destination
        monkeypatch.setattr(target, 'upload', change_link)
        with pytest.raises(StorageConflictError, match='recovery point changed'):
            execute(restored, secrets, payload, **kwargs, apply=True,
                    expected_plan=plan['plan_sha256'], approval_sha256='a' * 64)
        assert repo.retained_documents() == expected
        with restored.transaction(read_only=True) as connection:
            assert connection.scalar(text('SELECT count(*) FROM document_locations')) == 2
            assert connection.scalar(text("SELECT store_scope_key FROM external_connections WHERE provider='s3'")) == source.scope_key
        assert len(target.inventory('alpha').documents) == 2


def test_full_isolated_snapshot_byte_restore_drill_with_fake_provider_boundary(database, tmp_path, monkeypatch):
    from tests.postgres.test_s3_staging import run_drill
    from tabula_rasa.infrastructure.s3_documents import create_s3_document_store
    clients = FakeS3(), FakeS3()
    configurations = {name: dict(bucket='synthetic-receipts', region='us-east-1',
        namespace='staging/' + digit * 32, access_key_id='synthetic', secret_access_key='synthetic')
        for name, digit in [('source', '1'), ('target', '2')]}
    def factory(row, payload, **kwargs):
        client = clients[payload['s3']['namespace'] == configurations['target']['namespace']]
        return create_s3_document_store(row['entity_id'], payload['s3'], client=client)
    monkeypatch.setattr('scripts.manage_document_storage.provider_store', factory)
    run_drill(database, tmp_path, configurations, clients=clients)


def test_http_managed_upload_link_unlink_relink_logout_and_cross_entity_denial(database, tmp_path):
    from tests.postgres.test_identity_http import client_for
    from tests.postgres.test_identity import verified
    from tests.postgres._support import account, category
    identity, alice, bob, org, _ = identity_setup(database)
    with database.transaction() as connection:
        account(connection, 'alpha', 'cash')
        category(connection, 'alpha', 'office', kind='Expense')
    secrets = ConnectionSecrets(tmp_path, Fernet.generate_key().decode())
    target = s3_store()
    secret = secrets.put(dict(environment='staging', organization_id=org, entity_id='alpha', provider='s3',
                             profile_key='alpha', scope_key=target.scope_key))
    with database.transaction() as connection:
        install_managed_connection(connection, 'alpha', target.scope_key, organization_id=org,
            profile_key='alpha', secret_ref=secret, expected_revision=0)
    manager = TenantConnections(database, 'staging', secrets, store_factory=lambda *args, **kw: target)
    services = create_postgres_services(database, 'alpha', document_store=target, document_store_kind='s3')
    services.transactions.create(transaction_id='txn', **expense())
    client, auth, _ = client_for(database)
    with client:
        runtime = client.app.state.runtime
        runtime.connections = manager
        def services_for(ctx):
            store = manager.document_store(ctx)
            return create_postgres_services(database, ctx.entity_id, document_store=store,
                                            document_store_kind=store.store_kind if store else None)
        runtime.service_factory = services_for
        token, _ = auth.sessions.issue(verified())
        client.cookies.set(auth.session_cookie, token)
        info = client.get('/api/v1/session').json()
        headers = {'origin': auth.settings.public_origin, 'x-csrf-token': info['csrf_token']}
        client.cookies.set(auth.csrf_cookie, info['csrf_token'])
        path = '/api/v1/entities/alpha/documents/' + 'a' * 32
        response = client.put(path + '/content?filename=receipt.pdf', headers=headers, content=upload().content)
        assert response.status_code == 200 and response.json()['id'] == 'a' * 32
        assert 'versionId' not in response.text and 'synthetic-receipts' not in response.text
        link = '/api/v1/entities/alpha/transactions/txn/receipts'
        assert client.post(link, headers=headers, json={'document_id': 'a' * 32}).status_code == 200
        assert client.delete(link + '/' + 'a' * 32, headers=headers).status_code == 200
        assert client.get(path + '/content').content == upload().content
        target.client.read_failure = 'AccessDenied'
        assert client.get(path + '/content').status_code == 503
        target.client.read_failure = None
        assert client.post(link, headers=headers, json={'document_id': 'a' * 32}).status_code == 200
        assert client.get(path.replace('/alpha/', '/beta/') + '/content').status_code == 403
        assert client.get('/api/v1/entities/alpha/documents/' + 'b' * 32 + '/content').status_code == 404
        assert client.post('/api/v1/auth/logout', headers=headers).status_code == 204
        assert client.get(path + '/content').status_code == 401
    assert not any(method == 'delete-marker' for method, _ in target.client.calls)
