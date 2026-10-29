"""Darwin zombie-group races must not leave cancelled jobs RUNNING forever."""

import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from job_server.executor import native


def test_zombie_group_permission_error_reaps_and_retries(monkeypatch):
    process = SimpleNamespace(pid=12345, poll=Mock(return_value=0), wait=Mock())
    killpg = Mock(side_effect=[PermissionError(), ProcessLookupError()])
    monkeypatch.setattr(native.os, "killpg", killpg, raising=False)
    native.terminate(process, None, grace=0)
    assert killpg.call_count == 2
    process.poll.assert_called_once()


def test_permission_error_for_live_process_remains_uncertain(monkeypatch):
    process = SimpleNamespace(pid=12345, poll=Mock(return_value=None), wait=Mock())
    monkeypatch.setattr(native.os, "killpg", Mock(side_effect=PermissionError()), raising=False)
    with pytest.raises(PermissionError):
        native.terminate(process, None, grace=0)
    process.wait.assert_not_called()


def test_permission_error_after_reaping_still_propagates(monkeypatch):
    process = SimpleNamespace(pid=12345, poll=Mock(return_value=0), wait=Mock())
    monkeypatch.setattr(native.os, "killpg", Mock(side_effect=PermissionError()), raising=False)
    with pytest.raises(PermissionError):
        native.terminate(process, None, grace=0)


def test_live_descendants_still_receive_kill_after_leader_exits(monkeypatch):
    process = SimpleNamespace(pid=12345, poll=Mock(return_value=0), wait=Mock())
    killpg = Mock()
    monkeypatch.setattr(native.os, "killpg", killpg, raising=False)
    native.terminate(process, None, grace=0)
    assert [call.args for call in killpg.call_args_list] == [
        (12345, signal.SIGTERM), (12345, signal.SIGKILL),
    ]
    process.wait.assert_called_once_with(timeout=10)
