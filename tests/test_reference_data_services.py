"""Reference services validate without persistence and preserve the Sheets contract."""


import pytest

from tabula_rasa.application import (
    AccountService,
    PayeeService,
    TagService,
)
from tabula_rasa.errors import (
    StorageNotFoundError,
    StorageValidationError,
)
from tests.application_import_probe import assert_application_runs_without_infrastructure


class RecordingRepository:
    """A persistence-free port that records calls and returns an opaque result."""

    def __init__(self):
        self.calls = []
        self.result = {"id": "synthetic", "status": "fully_applied"}
        self.records = [self.result]
        self.error = None

    def _call(self, operation, args, kwargs):
        self.calls.append((operation, args, kwargs))
        if self.error is not None:
            raise self.error
        return self.result

    def list(self, **kwargs):
        self._call("list", (), kwargs)
        return self.records

    def create(self, *args, **kwargs):
        return self._call("create", args, kwargs)

    def update(self, *args, **kwargs):
        return self._call("update", args, kwargs)

    def merge(self, *args, **kwargs):
        return self._call("merge", args, kwargs)

    def remove(self, *args, **kwargs):
        return self._call("remove", args, kwargs)


def test_account_service_normalizes_inputs_without_additional_reads():
    repository = RecordingRepository()
    service = AccountService(repository)

    assert service.list() is repository.records
    assert service.list(active_only=False) is repository.records
    assert [call[2] for call in repository.calls] == [
        {"active_only": True},
        {"active_only": False},
    ]
    repository.calls.clear()

    assert service.create(" Owner draws ", " Other ", "-125.505") is repository.result
    assert repository.calls == [
        (
            "create",
            ("Owner draws", "Other", "-125.50"),
            {"classification": "Equity", "parent_id": ""},
        )
    ]
    repository.calls.clear()
    assert (
        service.update(
            " account-1 ",
            name=" Checking ",
            type=" Bank ",
            classification=" asset ",
            opening_balance="1.235",
            active="false",
            parent_id=" parent-1 ",
        )
        is repository.result
    )
    assert repository.calls == [
        (
            "update",
            ("account-1",),
            {
                "changes": {
                    "name": "Checking",
                    "type": "Bank",
                    "classification": "Asset",
                    "opening_balance": "1.24",
                    "active": False,
                    "parent_id": "parent-1",
                }
            },
        )
    ]
    repository.calls.clear()
    service.rename("account-1", " Renamed ")
    service.deactivate("account-1")
    assert repository.calls == [
        ("update", ("account-1",), {"changes": {"name": "Renamed"}}),
        ("update", ("account-1",), {"changes": {"active": False}}),
    ]


@pytest.mark.parametrize(
    "operation",
    [
        lambda service: service.create(" ", "Bank"),
        lambda service: service.create("Checking", "Unknown"),
        lambda service: service.create("Checking", "Bank", classification="Liability"),
        lambda service: service.create("Checking", "Bank", "NaN"),
        lambda service: service.create("Checking", "Bank", "bad amount"),
        lambda service: service.create("Checking", "Bank", "9" * 27),
        lambda service: service.update("account-1", opening_balance="9" * 26 + ".999"),
        lambda service: service.update("", name="Checking"),
        lambda service: service.update("account-1", classification="invalid"),
        lambda service: service.update(
            "account-1", type="Bank", classification="Equity"
        ),
        lambda service: service.update("account-1", active="invalid"),
    ],
)
def test_account_service_rejects_invalid_input_before_repository_call(operation):
    repository = RecordingRepository()
    with pytest.raises(StorageValidationError):
        operation(AccountService(repository))
    assert repository.calls == []


@pytest.mark.parametrize("service_type", [PayeeService, TagService])
def test_managed_services_preserve_expected_snapshots_and_delegate_once(service_type):
    repository = RecordingRepository()
    service = service_type(repository)
    expected_record = {"id": "source-1", "name": "Old name", "active": False}
    expected_records = [expected_record, {"id": "target-1", "name": "Target"}]

    assert service.list() is repository.records
    assert repository.calls[-1] == ("list", (), {"active_only": False})
    repository.calls.clear()
    assert service.create("  New name  ", active=False) is repository.result
    assert (
        service.rename(" source-1 ", " Renamed ", expected=expected_record)
        is repository.result
    )
    assert (
        service.update("source-1", active="TRUE", expected=expected_record)
        is repository.result
    )
    assert (
        service.merge(
            " target-1 ",
            [" source-1 ", "source-1", "source-2"],
            expected=expected_records,
        )
        is repository.result
    )
    assert service.remove(" source-1 ", expected=expected_record) is repository.result
    assert repository.calls == [
        ("create", ("New name",), {"active": False}),
        (
            "update",
            ("source-1",),
            {"changes": {"name": "Renamed"}, "expected": expected_record},
        ),
        (
            "update",
            ("source-1",),
            {"changes": {"active": True}, "expected": expected_record},
        ),
        (
            "merge",
            ("target-1", ["source-1", "source-2"]),
            {"expected": expected_records},
        ),
        ("remove", ("source-1",), {"expected": expected_record}),
    ]
    assert repository.calls[1][2]["expected"] is expected_record
    assert repository.calls[3][2]["expected"] is expected_records
    assert expected_record["active"] is False


@pytest.mark.parametrize("service_type", [PayeeService, TagService])
@pytest.mark.parametrize(
    "operation",
    [
        lambda service: service.create(" "),
        lambda service: service.create("Name", active="invalid"),
        lambda service: service.rename("record-1", " "),
        lambda service: service.update("record-1", active="invalid"),
        lambda service: service.merge("target", []),
        lambda service: service.merge("target", [" target "]),
        lambda service: service.merge("target", [""]),
        lambda service: service.remove(""),
    ],
)
def test_managed_services_reject_invalid_input_without_reading_or_writing(
    service_type, operation
):
    repository = RecordingRepository()
    with pytest.raises(StorageValidationError):
        operation(service_type(repository))
    assert repository.calls == []


@pytest.mark.parametrize("service_type", [AccountService, PayeeService, TagService])
@pytest.mark.parametrize(
    "operation",
    [
        lambda service: service.list(),
        lambda service: service.rename("record-1", "Renamed"),
    ],
)
def test_service_failures_are_propagated_without_retry(service_type, operation):
    repository = RecordingRepository()
    failure = StorageNotFoundError("Synthetic record is unavailable")
    repository.error = failure
    with pytest.raises(StorageNotFoundError) as raised:
        operation(service_type(repository))
    assert raised.value is failure
    assert len(repository.calls) == 1


def test_reference_services_run_without_importing_ui_or_persistence():
    assert_application_runs_without_infrastructure("""
from tabula_rasa.application import assemble_application_services
from tabula_rasa.errors import StorageValidationError

class Repository:
    ledger_name = 'Synthetic ledger'
    ledger_currency = 'USD'
    def __init__(self):
        self.calls = []
    def ensure_defaults(self):
        return {'accounts': 0, 'categories': 0}
    def list(self, **kwargs):
        return []
    def create(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {'id': 'synthetic', 'name': args[0]}
    def update(self, record_id, **kwargs):
        self.calls.append((record_id, kwargs))
        return {'id': record_id, **kwargs['changes']}
    def merge(self, target_id, source_ids, **kwargs):
        self.calls.append((target_id, source_ids, kwargs))
        return {'status': 'fully_applied', 'count': str(len(source_ids))}
    def remove(self, record_id, **kwargs):
        return {'status': 'fully_applied'}

accounts, payees, tags = Repository(), Repository(), Repository()
services = assemble_application_services(
    'test', ledger_repository=Repository(), account_repository=accounts,
    payee_repository=payees, tag_repository=tags,
)
assert services.accounts.create(' Checking ', 'Bank')['name'] == 'Checking'
assert accounts.calls[0][1]['classification'] == 'Asset'
assert services.accounts.update('account-1', active='false')['active'] is False
for service, repository in ((services.payees, payees), (services.tags, tags)):
    assert service.create(' Name ')['name'] == 'Name'
    snapshot = {'id': 'source', 'active': False}
    service.rename('source', 'Renamed', expected=snapshot)
    assert repository.calls[-1][1]['expected'] is snapshot
    assert service.merge('target', ['source'])['count'] == '1'
    assert service.remove('source')['status'] == 'fully_applied'
try:
    services.accounts.create('Checking', 'Bank', classification='Liability')
except StorageValidationError:
    pass
else:
    raise AssertionError('Invalid classification reached repository')
assert len(accounts.calls) == 2
""")
