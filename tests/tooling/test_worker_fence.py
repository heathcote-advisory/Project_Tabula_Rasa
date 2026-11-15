from unittest.mock import MagicMock, Mock

import pytest

from tabula_rasa.deployment.release import ReleaseError
from tabula_rasa.deployment.worker_fence import WorkerLoginFence


ENV = {"WINDOW_WORKER_ROLE": "recurring_worker", "WINDOW_WORKER_COMPATIBLE_SCHEMAS": "source,target"}


@pytest.mark.parametrize("config", [
    {"WINDOW_WORKER_ROLE": "app"},
    {"WINDOW_WORKER_ROLE": 'worker"'},
    {"WINDOW_WORKER_COMPATIBLE_SCHEMAS": "source"},
])
def test_fence_requires_dedicated_and_compatible_worker(config):
    with pytest.raises(ReleaseError, match="dedicated worker"):
        WorkerLoginFence({**ENV, **config}, "app", "source", "target")


def test_fence_waits_for_sessions_after_blocking_logins(monkeypatch):
    fence = WorkerLoginFence(ENV, "app", "source", "target")
    db = MagicMock()
    db.transaction.return_value.__enter__.return_value.scalar.side_effect = [1, 0]
    monkeypatch.setattr(fence, "_database", lambda: db)
    set_login = Mock()
    monkeypatch.setattr(fence, "_set_login", set_login)
    sleep = Mock()
    fence.drain(monotonic=lambda: 0, sleep=sleep)
    set_login.assert_called_once_with(db, previous=True, enabled=False, schema="source")
    sleep.assert_called_once_with(2)
    db.dispose.assert_called_once_with()


def test_fence_timeout_does_not_reopen_login(monkeypatch):
    fence = WorkerLoginFence(ENV, "app", "source", "target")
    db = MagicMock()
    db.transaction.return_value.__enter__.return_value.scalar.return_value = 1
    monkeypatch.setattr(fence, "_database", lambda: db)
    set_login = Mock()
    monkeypatch.setattr(fence, "_set_login", set_login)
    with pytest.raises(ReleaseError, match="did not drain"):
        fence.drain(monotonic=Mock(side_effect=[0, 91]), sleep=Mock())
    set_login.assert_called_once_with(db, previous=True, enabled=False, schema="source")
    db.dispose.assert_called_once_with()


def test_scoped_login_function_is_bound_and_read_back(monkeypatch):
    from tabula_rasa.deployment import worker_fence

    fence = WorkerLoginFence(ENV, "app", "source", "target")
    db = MagicMock()
    connection = db.engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.one_or_none.return_value = (True, True)
    monkeypatch.setattr(worker_fence, "current_revision", lambda _: "source")
    connection.scalar.return_value = True
    with pytest.raises(ReleaseError, match="could not be verified"):
        fence._set_login(db, previous=True, enabled=False, schema="source")
    statement, parameters = connection.execute.call_args.args
    assert "release_control.set_worker_login" in str(statement)
    assert parameters == {"role": "recurring_worker", "enabled": False}
    connection.exec_driver_sql.assert_not_called()
    connection.scalar.return_value = False
    fence._set_login(db, previous=True, enabled=False, schema="source")
