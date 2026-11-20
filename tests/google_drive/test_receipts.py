from __future__ import annotations

import hashlib

import pytest
import requests

from tabula_rasa.google_drive import (
    GoogleDriveAccessError, GoogleDriveClient, safe_receipt_filename, suffixed_receipt_filename,
)

from ._support import FakeResponse, DriveSession, drive_error, drive_settings, oauth_config

class TestGoogleDriveReceipts:
    def test_download_outage_recovers_without_reusing_another_profile(self):
        north_bytes, south_bytes = b'north receipt', b'south receipt'
        north_session = DriveSession([
            *[requests.ConnectionError('private-provider-response') for _ in range(4)],
            FakeResponse(200, content=north_bytes),
        ], access_token='north-access')
        south_session = DriveSession([
            FakeResponse(200, content=south_bytes),
            FakeResponse(200, content=south_bytes),
        ], access_token='south-access')
        delays = []
        north = GoogleDriveClient(oauth_config(), drive_settings(refresh_token='north-refresh'),
            session=north_session, sleeper=delays.append)
        south = GoogleDriveClient(oauth_config(),
            drive_settings(email='south@example.test', refresh_token='south-refresh'),
            session=south_session, sleeper=lambda _: None)

        # The same opaque file selector must still use each selected profile's grant.
        with pytest.raises(GoogleDriveAccessError) as caught:
            north.download_file('receipt12345')
        assert caught.value.retryable
        assert 'private' not in str(caught.value)
        assert len(north_session.requests) == 4 and len(delays) == 3
        assert south.download_file('receipt12345') == south_bytes
        assert north.download_file('receipt12345') == north_bytes
        assert south.download_file('receipt12345') == south_bytes
        for session, profile in ((north_session, 'north'), (south_session, 'south')):
            assert session.refresh_tokens == [profile + '-refresh']
            assert all(request[2]['headers']['Authorization'] == 'Bearer ' + profile + '-access'
                       for request in session.requests)

    @pytest.mark.parametrize('damage', [None, 'profile', 'folder', 'trashed', 'identity', 'kind'])
    def test_single_receipt_metadata_uses_scoped_get_and_refuses_foreign_or_deleted_file(self, damage):
        from tests.drive_fixtures import receipt_drive_file
        file = receipt_drive_file('receipt12345', 'receipt.pdf', profile_key='north', parent_id='folder123456')
        if damage == 'profile':
            file['appProperties']['tabulaRasaProfile'] = 'south'
        elif damage == 'folder':
            file['parents'] = ['foreign12345']
        elif damage == 'trashed':
            file['trashed'] = True
        elif damage == 'identity':
            file['id'] = 'foreign12345'
        elif damage == 'kind':
            file['appProperties']['tabulaRasaKind'] = 'backup'
        session = DriveSession([FakeResponse(200, file)])
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=session, sleeper=lambda _: None)
        if damage:
            with pytest.raises(GoogleDriveAccessError, match='selected receipt folder'):
                client.receipt_file(profile_key='north', folder_id='folder123456', file_id='receipt12345')
        else:
            assert client.receipt_file(profile_key='north', folder_id='folder123456', file_id='receipt12345') == file
        assert len(session.requests) == 1
        assert session.requests[0][1].endswith('/files/receipt12345')
        assert 'q' not in session.requests[0][2]['params']

    @pytest.mark.parametrize('duplicate', [False, True])
    def test_recovery_reuses_one_matching_identity_and_rejects_duplicates(self, duplicate):
        from tests.drive_fixtures import receipt_drive_file
        existing = receipt_drive_file('existing123456', 'receipt.pdf', profile_key='north',
            receipt_id='a' * 32, sha256=hashlib.sha256(b'receipt').hexdigest())
        session = DriveSession([FakeResponse(200, {'files': [existing] * (2 if duplicate else 1)})])
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=session, sleeper=lambda _: None)
        def recover():
            return client.upload_receipt(profile_key='north', folder_id='folder123456', original_name='receipt.pdf',
                stored_filename='receipt.pdf', content=b'receipt', mime_type='application/pdf', receipt_id='a' * 32)
        if duplicate:
            with pytest.raises(GoogleDriveAccessError, match='conflicting receipt identities'):
                recover()
        else:
            assert recover()['id'] == existing['id']
        assert [method for method, *_ in session.requests] == ['GET']

    @pytest.mark.parametrize('uncertain', [False, True])
    def test_recovery_checks_identity_without_checksum_before_create_or_retry(self, uncertain):
        from tests.drive_fixtures import receipt_drive_file
        conflict = receipt_drive_file('existing123456', 'different.pdf', profile_key='north',
                                      receipt_id='a' * 32, sha256=hashlib.sha256(b'other').hexdigest())
        responses = ([FakeResponse(200, {'files': []}), FakeResponse(200, {'files': []}),
                      requests.ConnectionError('synthetic lost response')] if uncertain else [])
        session = DriveSession(responses + [FakeResponse(200, {'files': [conflict]})])
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=session, sleeper=lambda _: None)
        with pytest.raises(GoogleDriveAccessError, match='conflicting receipt identities'):
            client.upload_receipt(profile_key='north', folder_id='folder123456', original_name='receipt.pdf',
                stored_filename='receipt.pdf', content=b'receipt', mime_type='application/pdf', receipt_id='a' * 32)
        assert sum(method == 'POST' for method, *_ in session.requests) == int(uncertain)
        for request in [session.requests[0], session.requests[-1]]:
            query = request[2]['params']['q']
            assert "value='north'" in query and "value='" + 'a' * 32 + "'" in query
            assert "'folder123456' in parents" in query and 'tabulaRasaSha256' not in query

    def test_explicit_recovery_upload_preserves_canonical_names_with_normal_retry_owner(self):
        session = DriveSession([
            FakeResponse(200, {'files': []}), FakeResponse(200, {'files': []}),
            requests.ConnectionError('synthetic response lost'), FakeResponse(200, {'files': []}),
            FakeResponse(200, {'id': 'restored123456'}),
        ])
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=session, sleeper=lambda _: None)
        result = client.upload_receipt(profile_key='north', folder_id='folder123456', original_name='résumé.pdf',
            stored_filename='résumé (2).pdf', content=b'receipt', mime_type='application/pdf', receipt_id='a' * 32)
        assert result['name'] == 'résumé (2).pdf' and result['description'] == 'résumé.pdf'
        assert [method for method, *_ in session.requests] == ['GET', 'GET', 'POST', 'GET', 'POST']
        uploads = [request[2]['data'] for request in session.requests if request[0] == 'POST']
        import json
        metadata = [json.loads(body.split(b'\r\n\r\n', 1)[1].split(b'\r\n', 1)[0]) for body in uploads]
        assert metadata[0] == metadata[1]
        assert metadata[0]['appProperties']['tabulaRasaReceiptId'] == 'a' * 32

    @pytest.mark.parametrize('existing', ['name', 'identity'])
    def test_explicit_recovery_refuses_filename_or_identity_collision_without_upload(self, existing):
        from tests.drive_fixtures import receipt_drive_file
        checksum = hashlib.sha256(b'receipt').hexdigest()
        file = receipt_drive_file('existing123456', 'other.pdf', profile_key='north', sha256=checksum)
        responses = ([FakeResponse(200, {'files': [file]})] if existing == 'identity' else
                     [FakeResponse(200, {'files': []}), FakeResponse(200, {'files': [{'name': 'receipt.pdf'}]})])
        session = DriveSession(responses)
        client = GoogleDriveClient(oauth_config(), drive_settings(), session=session, sleeper=lambda _: None)
        with pytest.raises(GoogleDriveAccessError):
            client.upload_receipt(profile_key='north', folder_id='folder123456', original_name='receipt.pdf',
                stored_filename='receipt.pdf', content=b'receipt', mime_type='application/pdf', receipt_id='a' * 32)
        assert all(method == 'GET' for method, *_ in session.requests)

    def test_receipt_upload_is_transaction_scoped_and_sanitizes_name(self) -> None:
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"files": []}),
                FakeResponse(
                    200,
                    {
                        "id": "receipt123456",
                        "name": "statement.pdf",
                    },
                ),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        uploaded = client.upload_receipt(
            profile_key="north",
            folder_id="folder123456",
            original_name=r"..\statement.pdf",
            content=b"receipt",
            mime_type="application/pdf",
        )

        assert uploaded["id"] == "receipt123456"
        create_request = session.requests[-1]
        body = create_request[2]["data"]
        assert b'"tabulaRasaProfile":"north"' in body
        assert b'"tabulaRasaTransaction"' not in body
        assert (
            f'"tabulaRasaSha256":"{hashlib.sha256(b"receipt").hexdigest()}"'.encode()
            in body
        )
        assert b'"tabulaRasaReceiptId":' in body
        assert uploaded["size"] == "7"
        assert b"..\\" not in body
        assert safe_receipt_filename("../../statement.pdf") == "statement.pdf"
        assert safe_receipt_filename("CON.pdf") == "_CON.pdf"
        assert suffixed_receipt_filename("statement.pdf", 2) == (
            "statement (2).pdf"
        )
        assert suffixed_receipt_filename("receipt", 3) == "receipt (3)"


    def test_receipt_upload_adds_suffix_for_case_only_name_collision(
        self,
    ) -> None:
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                FakeResponse(
                    200,
                    {
                        "files": [
                            {
                                "id": "receipt123456",
                                "name": "Statement.pdf",
                            }
                        ]
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "id": "receipt234567",
                        "name": "statement (2).pdf",
                    },
                ),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        uploaded = client.upload_receipt(
            profile_key="north",
            folder_id="folder123456",
            original_name="statement.pdf",
            content=b"second receipt",
            mime_type="application/pdf",
        )

        assert uploaded["name"] == "statement (2).pdf"
        assert [request[0] for request in session.requests] == [
            "GET",
            "GET",
            "POST",
        ]
        assert "name =" not in session.requests[0][2]["params"]["q"]
        assert session.requests[0][2]["params"]["pageSize"] == 2
        assert session.requests[1][2]["params"]["pageSize"] == 1000
        assert b'"name":"statement (2).pdf"' in (
            session.requests[2][2]["data"]
        )


    def test_receipt_upload_recovers_an_uncertain_create_response(self) -> None:
        committed = {
            "id": "receipt123456",
            "name": "statement.pdf",
        }
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"files": []}),
                requests.ConnectionError("response lost"),
                FakeResponse(200, {"files": [committed]}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        uploaded = client.upload_receipt(
            profile_key="north",
            folder_id="folder123456",
            original_name="statement.pdf",
            content=b"receipt",
            mime_type="application/pdf",
        )

        assert uploaded["id"] == committed["id"]
        assert uploaded["name"] == committed["name"]
        assert uploaded["appProperties"]["tabulaRasaSha256"] == hashlib.sha256(
            b"receipt"
        ).hexdigest()
        recovery_query = session.requests[-1][2]["params"]["q"]
        assert "value='north'" in recovery_query
        assert "tabulaRasaTransaction" not in recovery_query
        assert "key='tabulaRasaReceiptId'" in recovery_query
        assert "key='tabulaRasaSha256'" in recovery_query


    def test_receipt_upload_retries_once_after_unreadable_success(self) -> None:
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"files": []}),
                FakeResponse(200, payload="not-json"),
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"id": "receipt123456", "name": "statement.pdf"}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        uploaded = client.upload_receipt(
            profile_key="north",
            folder_id="folder123456",
            original_name="statement.pdf",
            content=b"receipt",
            mime_type="application/pdf",
            receipt_id="a" * 32,
        )

        assert uploaded["id"] == "receipt123456"
        assert [request[0] for request in session.requests] == [
            "GET",
            "GET",
            "POST",
            "GET",
            "POST",
        ]


    def test_receipt_upload_retries_once_after_transient_failure(self) -> None:
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"files": []}),
                FakeResponse(500),
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"id": "receipt123456", "name": "statement.pdf"}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        uploaded = client.upload_receipt(
            profile_key="north",
            folder_id="folder123456",
            original_name="statement.pdf",
            content=b"receipt",
            mime_type="application/pdf",
            receipt_id="a" * 32,
        )

        assert uploaded["id"] == "receipt123456"
        assert [request[0] for request in session.requests] == [
            "GET",
            "GET",
            "POST",
            "GET",
            "POST",
        ]


    def test_receipt_upload_keeps_preflight_failure_retryable_without_upload_attempt(
        self,
    ) -> None:
        session = DriveSession([FakeResponse(500) for _ in range(4)])
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        with pytest.raises(GoogleDriveAccessError) as raised:
            client.upload_receipt(
                profile_key="north",
                folder_id="folder123456",
                original_name="statement.pdf",
                content=b"receipt",
                mime_type="application/pdf",
                receipt_id="a" * 32,
            )

        assert raised.value.retryable is True
        assert raised.value.upload_attempted is False
        assert [request[0] for request in session.requests] == [
            "GET",
            "GET",
            "GET",
            "GET",
        ]


    @pytest.mark.parametrize("create_failure", [
        requests.ConnectionError("response lost"),
        drive_error(403, "userRateLimitExceeded"),
    ])
    def test_receipt_upload_preserves_create_uncertainty_when_readback_fails(
        self, create_failure,
    ) -> None:
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"files": []}),
                create_failure,
                FakeResponse(200, payload="not-json"),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        with pytest.raises(GoogleDriveAccessError) as raised:
            client.upload_receipt(
                profile_key="north",
                folder_id="folder123456",
                original_name="statement.pdf",
                content=b"receipt",
                mime_type="application/pdf",
                receipt_id="a" * 32,
            )

        assert raised.value.retryable is True
        assert raised.value.upload_attempted is True
        assert [request[0] for request in session.requests] == [
            "GET",
            "GET",
            "POST",
            "GET",
        ]


    def test_receipt_upload_retries_with_a_stable_identity(self) -> None:
        receipt_id = "a" * 32
        session = DriveSession(
            [
                FakeResponse(200, {"files": []}),
                FakeResponse(200, {"files": []}),
                requests.ConnectionError("response lost"),
                FakeResponse(200, {"files": []}),
                FakeResponse(
                    200,
                    {
                        "id": "receipt123456",
                        "name": "statement.pdf",
                    },
                ),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        uploaded = client.upload_receipt(
            profile_key="north",
            folder_id="folder123456",
            original_name="statement.pdf",
            content=b"receipt",
            mime_type="application/pdf",
            receipt_id=receipt_id,
        )

        assert uploaded["id"] == "receipt123456"
        post_bodies = [
            request[2]["data"]
            for request in session.requests
            if request[0] == "POST"
        ]
        assert len(post_bodies) == 2
        assert all(
            f'"tabulaRasaReceiptId":"{receipt_id}"'.encode() in body
            for body in post_bodies
        )
        assert all(b'"name":"statement.pdf"' in body for body in post_bodies)


    def test_profile_receipt_listing_is_not_transaction_scoped(self) -> None:
        receipt = {
            "id": "receipt123456",
            "name": "txn-1-statement.pdf",
            "appProperties": {
                "tabulaRasaKind": "receipt",
                "tabulaRasaProfile": "north",
                "tabulaRasaTransaction": "txn-1",
            },
        }
        session = DriveSession(
            [FakeResponse(200, {"files": [receipt]})]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        receipts = client.list_profile_receipts(
            profile_key="north",
            folder_id="folder123456",
        )

        assert receipts == [receipt]
        query = session.requests[0][2]["params"]["q"]
        assert "'folder123456' in parents" in query
        assert "value='receipt'" in query
        assert "value='north'" in query
        assert "tabulaRasaTransaction" not in query


    def test_profile_receipt_listing_fetches_every_drive_page(self) -> None:
        first = {"id": "receipt123456", "name": "first.pdf"}
        second = {"id": "receipt789012", "name": "second.pdf"}
        session = DriveSession(
            [
                FakeResponse(
                    200,
                    {"files": [first], "nextPageToken": "next-receipt-page"},
                ),
                FakeResponse(200, {"files": [second]}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        receipts = client.list_profile_receipts(
            profile_key="north",
            folder_id="folder123456",
        )

        assert receipts == [first, second]
        assert len(session.requests) == 2
        assert "pageToken" not in session.requests[0][2]["params"]
        assert (
            session.requests[1][2]["params"]["pageToken"]
            == "next-receipt-page"
        )
        assert "nextPageToken" in session.requests[0][2]["params"]["fields"]


    def test_profile_receipt_listing_rejects_a_repeated_page_token(self) -> None:
        session = DriveSession(
            [
                FakeResponse(200, {"files": [], "nextPageToken": "repeated"}),
                FakeResponse(200, {"files": [], "nextPageToken": "repeated"}),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        with pytest.raises(GoogleDriveAccessError, match="unreadable"):
            client.list_profile_receipts(
                profile_key="north",
                folder_id="folder123456",
            )

        assert len(session.requests) == 2


    def test_legacy_receipt_checksum_backfill_downloads_once_and_patches_metadata(
        self,
    ) -> None:
        legacy = {
            "id": "receipt123456",
            "name": "statement.pdf",
            "mimeType": "application/pdf",
            "size": "7",
            "appProperties": {
                "tabulaRasaKind": "receipt",
                "tabulaRasaProfile": "north",
                "tabulaRasaTransaction": "txn-1",
            },
        }
        session = DriveSession(
            [
                FakeResponse(200, {"files": [legacy]}),
                FakeResponse(200, content=b"receipt"),
                FakeResponse(
                    200,
                    {
                        "id": legacy["id"],
                        "name": legacy["name"],
                        "mimeType": legacy["mimeType"],
                        "size": legacy["size"],
                    },
                ),
            ]
        )
        client = GoogleDriveClient(
            oauth_config(),
            drive_settings(),
            session=session,
            sleeper=lambda _delay: None,
        )

        updated = client.backfill_receipt_checksum(
            profile_key="north",
            folder_id="folder123456",
            file_id="receipt123456",
        )

        assert [request[0] for request in session.requests] == [
            "GET",
            "GET",
            "PATCH",
        ]
        patch_body = session.requests[-1][2]["data"]
        expected_hash = hashlib.sha256(b"receipt").hexdigest()
        expected_receipt_id = hashlib.sha256(
            b"tabula-rasa-legacy-receipt:north:receipt123456"
        ).hexdigest()[:32]
        assert expected_hash in patch_body
        assert updated["appProperties"]["tabulaRasaSha256"] == expected_hash
        assert (
            updated["appProperties"]["tabulaRasaReceiptId"]
            == expected_receipt_id
        )
