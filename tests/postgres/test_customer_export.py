"""Named users, fresh membership and canonical document-inclusive delivery."""

from datetime import timedelta
from io import BytesIO
import json
from zipfile import ZipFile

import pytest
from sqlalchemy import text

from tabula_rasa.bookkeeping_export.format import validate_artifact
from tabula_rasa.infrastructure.postgres.audit import AuditReader
from tests.postgres.test_authorization_http import named_client
from tests.postgres.test_identity import verified

PATH = '/api/v1/entities/alpha/exports/handoff'


def export(client, headers, scope='documents_inclusive'):
    return client.post(PATH, headers=headers, json={'scope': scope})


def test_named_handoff_is_complete_scoped_versioned_and_audited(database):
    with named_client(database) as (client, _, _, _, _, stores, headers):
        identity = 'a' * 32
        assert client.put('/api/v1/entities/alpha/documents/' + identity + '/content',
            headers=headers, params={'filename': 'synthetic.pdf'}, content=b'synthetic receipt').status_code == 200
        # Retained SQL metadata and provider-only recovery uploads both travel.
        from tabula_rasa.infrastructure.postgres.documents import PostgresReceiptRepository
        PostgresReceiptRepository(database, 'alpha', store_kind='fake', store_scope_key=stores['alpha'].scope_key).register_document(
            stores['alpha'].inventory('alpha', fresh=True).documents[0])
        assert client.put('/api/v1/entities/alpha/documents/' + 'c' * 32 + '/content',
            headers=headers, params={'filename': 'unlinked.pdf'}, content=b'unlinked receipt').status_code == 200
        result = export(client, headers)
        assert result.status_code == 200, result.text
        assert result.headers['cache-control'] == 'no-store'
        with ZipFile(BytesIO(result.content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            canonical = json.loads(archive.read('bookkeeping.json'))
            assert manifest['format_version'] == 1
            assert manifest['scope'] == 'documents_inclusive' and manifest['audit_history'] == 'excluded'
            assert manifest['history_boundary']['activated_at']
            validate_artifact(canonical, entity_id='alpha')
            assert len(canonical['domains']) == 27
            assert archive.read(manifest['attachments'][0]['path']) == b'synthetic receipt'
            assert [item['origin'] for item in manifest['attachments']] == ['canonical', 'unlinked_inventory']
            assert len(canonical['domains']['documents']) == 1
        assert stores['beta'].upload_calls == 0
        reader = AuditReader(database, 'alpha', capabilities=frozenset({'platform:operate', 'audit:inspect'}))
        events = reader.query(target_type='customer_export_actions')
        assert [event['after_summary']['status'] for event in events] == ['requested', 'available']
        assert all(event['source'] == 'customer_export' and event['actor_type'] == 'user' for event in events)
        assert reader.verify()['status'] == 'matched'


@pytest.mark.parametrize('role,expected', [('owner', 200), ('admin', 200), ('accountant', 200),
                                          ('editor', 403), ('read_only', 403)])
def test_export_capability_distinctions_and_cross_tenant_forgery(database, role, expected):
    with named_client(database) as (client, _, repo, identities, factory, _, headers):
        alice, bob, org_a, _ = identities
        repo.set_membership(org_a, bob.id, 'owner')
        repo.set_membership(org_a, alice.id, role)
        assert export(client, headers, 'metadata_only').status_code == expected
        factory.reset_mock()
        denied = client.post(PATH.replace('alpha', 'beta'), headers={**headers, 'X-Role': 'owner'},
                             json={'scope': 'documents_inclusive'})
        assert denied.status_code == 403
        assert client.post(PATH, headers=headers, json={'scope': 'metadata_only', 'audit_entity': 'beta'}).status_code == (422 if expected == 200 else 403)
        factory.assert_not_called()


@pytest.mark.parametrize('loss', ['expired', 'revoked', 'revoked_during_prepare'])
def test_expired_or_revoked_session_cannot_obtain_archive(database, loss):
    with named_client(database) as (client, auth, repo, identities, _, _, headers):
        alice, bob, org_a, _ = identities
        repo.set_membership(org_a, bob.id, 'owner')
        if loss == 'expired':
            auth.sessions.clock = lambda: verified().authenticated_at + timedelta(hours=13)
        elif loss == 'revoked':
            repo.set_membership(org_a, alice.id, 'read_only', active=False)
        else:
            runtime = client.app.state.runtime
            original = runtime.prepare_customer_export
            def prepare(*args):
                stream = original(*args)
                repo.set_membership(org_a, alice.id, 'read_only', active=False)
                return stream
            runtime.prepare_customer_export = prepare
        result = export(client, headers)
        assert result.status_code in {401, 403}, result.text
        assert result.headers['content-type'].startswith('application/json')


def test_missing_bytes_fail_cleanly_metadata_scope_and_explicit_retry_remain_available(database):
    with named_client(database) as (client, _, _, _, _, stores, headers):
        identity = 'b' * 32
        client.put('/api/v1/entities/alpha/documents/' + identity + '/content',
                   headers=headers, params={'filename': 'synthetic.pdf'}, content=b'synthetic')
        original = stores['alpha'].fetch
        def missing(*args):
            raise RuntimeError('private document provider sentinel')
        stores['alpha'].fetch = missing
        failed = export(client, headers)
        assert failed.status_code == 503 and 'sentinel' not in failed.text
        assert export(client, headers, 'metadata_only').status_code == 200
        stores['alpha'].fetch = original
        assert export(client, headers).status_code == 200
        with database.transaction(read_only=True) as connection:
            statuses = list(connection.scalars(text('SELECT status FROM customer_export_actions')))
            assert statuses.count('requested') == 3 and statuses.count('available') == 2
            assert statuses.count('failed') == 1
