from tests.google_auth_fixtures import GOOGLE_OAUTH_CONFIG, google_grant
from ._support import (
    GoogleSheetsStorageTestCase,
    FakeWorkbook,
    GoogleSheetsStorage,
    Mock,
    StorageValidationError,
    WORKSHEET_HEADERS,
    gspread,
    patch,
    pytest,
)


class TestGoogleSheetsStorage(GoogleSheetsStorageTestCase):

    def test_accounts_sheet_without_classification_is_rejected(self) -> None:
        headers = dict(WORKSHEET_HEADERS)
        headers["Accounts"] = tuple(
            header for header in headers["Accounts"] if header != "classification"
        )
        with pytest.raises(StorageValidationError):
            GoogleSheetsStorage(FakeWorkbook(headers))

    def test_current_mixed_use_schema_mismatch_is_not_migrated(self) -> None:
        workbook = FakeWorkbook()

        def add_row(title: str, values: dict[str, str]) -> None:
            row = [""] * len(WORKSHEET_HEADERS[title])
            for field, value in values.items():
                row[WORKSHEET_HEADERS[title].index(field)] = value
            workbook.sheets[title].rows.append(row)

        add_row(
            "Accounts",
            {
                "id": "account-1",
                "name": "Checking",
                "type": "Bank",
                "classification": "Asset",
                "opening_balance": "0.00",
                "active": "TRUE",
                "created_at": "2026-09-01T00:00:00Z",
            },
        )
        add_row(
            "Categories",
            {
                "id": "category-1",
                "name": "Office",
                "kind": "Expense",
            },
        )
        add_row(
            "Transactions",
            {
                "id": "transaction-1",
                "txn_date": "2026-09-01",
                "kind": "Expense",
                "amount": "50.00",
                "account_id": "account-1",
                "category_id": "category-1",
                "description": "Office\nAllocation rationale: Measured use",
                "source_type": "mixed_use_expense",
                "source_id": "source-1",
                "tag_ids": '["tag-1"]',
                "created_at": "2026-09-01T00:00:00Z",
            },
        )
        add_row(
            "MixedUseExpenses",
            {
                "id": "source-1",
                "txn_date": "2026-09-01",
                "kind": "Expense",
                "total_expense": "100.00",
                "business_use_percentage": "50.00",
                "allocated_amount": "50.00",
                "account_id": "account-1",
                "category_id": "category-1",
                "description": "Office",
                "allocation_rationale": "Measured use",
                "linked_transaction_id": "transaction-1",
                "created_at": "2026-09-01T00:00:00Z",
            },
        )
        google_client = Mock()
        google_client.open_by_key.return_value = workbook

        with (
            patch(
                "tabula_rasa.storage.google_sheets.client.profile_google_credentials",
                return_value=object(),
            ),
            patch.object(gspread, "authorize", return_value=google_client),
            pytest.raises(StorageValidationError, match="tags do not match"),
        ):
            storage = GoogleSheetsStorage.from_secrets(
                {
                    "spreadsheet_id": "given-spreadsheet-id",
                    "google_drive": google_grant('owner@example.test'), "google_drive_oauth": GOOGLE_OAUTH_CONFIG,
                }
            )
            storage.list_mixed_use_expenses()

        source_sheet = workbook.sheets["MixedUseExpenses"]
        source_tag_index = source_sheet.rows[0].index("tag_ids")
        assert source_sheet.rows[1][source_tag_index] == ""


    def test_missing_header_is_rejected(self) -> None:
        headers = dict(WORKSHEET_HEADERS)
        headers["Categories"] = ("id", "name")
        with pytest.raises(StorageValidationError):
            GoogleSheetsStorage(FakeWorkbook(headers))

    @pytest.mark.parametrize(
        ("title", "missing_header"),
        [("Payees", "active"), ("Transactions", "category_id")],
    )
    def test_current_schema_rejects_missing_required_headers(
        self, title: str, missing_header: str
    ) -> None:
        headers = dict(WORKSHEET_HEADERS)
        headers[title] = tuple(
            header for header in headers[title] if header != missing_header
        )

        with pytest.raises(StorageValidationError, match="is missing headers"):
            GoogleSheetsStorage(FakeWorkbook(headers))

    @pytest.mark.parametrize(("extra_header", "message"), [
        ("", "blank header"),
        ("active", "duplicate headers"),
    ])
    def test_current_schema_rejects_blank_and_duplicate_headers(self, extra_header: str, message: str) -> None:
        headers = dict(WORKSHEET_HEADERS)
        headers["Payees"] = ("id", "name", "active", extra_header)

        with pytest.raises(StorageValidationError, match=message):
            GoogleSheetsStorage(FakeWorkbook(headers))
