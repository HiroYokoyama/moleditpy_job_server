from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from job_server import lifecycle
from job_server.client import ClientError


def test_start_waits_for_health_and_checks_ownership(monkeypatch, tmp_path):
    client = Mock(url="http://127.0.0.1:8766")
    client.request.side_effect = [ConnectionRefusedError(), {"status": "ok"}]
    verified = Mock()
    monkeypatch.setattr(lifecycle, "verify_local_daemon", verified)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _: None)
    lifecycle.wait_until_ready(client, tmp_path, SimpleNamespace(poll=lambda: None))
    assert client.request.call_count == 2
    verified.assert_called_once_with(tmp_path, 8766)


def test_start_reports_exit_and_timeout(monkeypatch, tmp_path):
    client = Mock(url="http://127.0.0.1:8766")
    with pytest.raises(RuntimeError, match="exited during startup"):
        lifecycle.wait_until_ready(client, tmp_path, SimpleNamespace(poll=lambda: 1))
    client.request.side_effect = ConnectionRefusedError()
    with pytest.raises(TimeoutError, match="did not become ready"):
        lifecycle.wait_until_ready(client, tmp_path, SimpleNamespace(poll=lambda: None), timeout=0)


def test_shutdown_waits_for_lock_after_a_lost_response(monkeypatch, tmp_path):
    client = Mock(url="http://127.0.0.1:8766")
    client.request.side_effect = ConnectionResetError()
    states = iter([False, False, True])
    monkeypatch.setattr(lifecycle, "directory_is_idle", lambda root: next(states))
    verified = Mock()
    monkeypatch.setattr(lifecycle, "verify_local_daemon", verified)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _: None)
    lifecycle.stop_and_wait(client, tmp_path)
    verified.assert_called_once_with(tmp_path, 8766)
    client.request.assert_called_once_with("POST", "/admin/shutdown")


def test_shutdown_auth_failure_is_not_retried_or_treated_as_stopped(monkeypatch, tmp_path):
    client = Mock(url="http://127.0.0.1:8766")
    client.request.side_effect = ClientError(401, {"message": "expired"})
    monkeypatch.setattr(lifecycle, "directory_is_idle", lambda root: False)
    monkeypatch.setattr(lifecycle, "verify_local_daemon", lambda *args: None)
    with pytest.raises(ClientError):
        lifecycle.stop_and_wait(client, tmp_path)
    assert client.request.call_count == 1


def test_shutdown_is_idempotent_when_directory_is_idle(tmp_path):
    client = Mock()
    lifecycle.stop_and_wait(client, tmp_path)
    client.request.assert_not_called()


def test_shutdown_timeout_does_not_report_success(monkeypatch, tmp_path):
    client = Mock(url="http://127.0.0.1:8766")
    monkeypatch.setattr(lifecycle, "directory_is_idle", lambda root: False)
    monkeypatch.setattr(lifecycle, "verify_local_daemon", lambda *args: None)
    with pytest.raises(TimeoutError, match="shutdown was not confirmed"):
        lifecycle.stop_and_wait(client, tmp_path, timeout=0)


def test_real_process_can_start_stop_and_restart_with_saved_credentials(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    from pathlib import Path

    from job_server.auth import Auth
    from job_server.client import JobServerClient
    from job_server.db import Database
    from job_server.runtime import ensure_secure_dependencies
    from job_server.settings import Settings

    try:
        ensure_secure_dependencies()
    except RuntimeError:
        pytest.skip("Real daemon requires the security dependency floors")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(data_dir=str(tmp_path), port=port, allow_privileged=True)
    config = tmp_path / "settings.json"
    settings.save(config)
    db = Database(settings.root)
    try:
        Auth(db, settings).create_initial_admin("fixture-admin", "synthetic-password")
    finally:
        db.close()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    url = f"http://127.0.0.1:{port}"
    key = tmp_path / "fixture-device.key"
    for attempt in range(2):
        with (tmp_path / "startup.log").open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "job_server", "serve", "--config", str(config)],
                env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
        try:
            lifecycle.wait_until_ready(JobServerClient(url, timeout=1), settings.root, process)
            client = JobServerClient(url, key, timeout=3)
            if attempt == 0:
                client.enroll("fixture-admin", "synthetic-password")
            else:
                client.authenticate()
            assert client.request("GET", "/me")["role"] == "admin"
            lifecycle.stop_and_wait(client, settings.root)
            assert process.wait(timeout=10) == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def test_process_stop_terminates_daemon_processes(monkeypatch, tmp_path):
    calls = []
    process = SimpleNamespace(
        terminate=lambda: calls.append("terminate"),
        kill=lambda: calls.append("kill"),
    )
    monkeypatch.setattr(lifecycle, "daemon_processes", lambda root: [process])
    states = iter([False, True])
    monkeypatch.setattr(lifecycle, "directory_is_idle", lambda root: next(states))
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _: None)
    lifecycle.stop_processes_and_wait(tmp_path)
    assert calls == ["terminate"]


def test_process_stop_without_a_process_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle, "daemon_processes", lambda root: [])
    with pytest.raises(RuntimeError, match="No Job Server process"):
        lifecycle.stop_processes_and_wait(tmp_path)


def test_process_stop_escalates_to_kill_then_times_out(monkeypatch, tmp_path):
    calls = []
    process = SimpleNamespace(
        terminate=lambda: calls.append("terminate"),
        kill=lambda: calls.append("kill"),
    )
    monkeypatch.setattr(lifecycle, "daemon_processes", lambda root: [process])
    waits = iter([False, True])
    monkeypatch.setattr(lifecycle, "_wait_until_idle", lambda root, timeout: next(waits))
    lifecycle.stop_processes_and_wait(tmp_path)
    assert calls == ["terminate", "kill"]
    waits = iter([False, False])
    monkeypatch.setattr(lifecycle, "_wait_until_idle", lambda root, timeout: next(waits))
    with pytest.raises(TimeoutError, match="was not confirmed"):
        lifecycle.stop_processes_and_wait(tmp_path)


def test_startup_banner_names_the_time_and_version():
    from datetime import datetime

    from job_server import PLUGIN_VERSION

    banner = lifecycle.startup_banner(datetime(2026, 11, 14, 9, 5, 7))
    assert banner == f"\n===== 2026-11-14 09:05:07 Starting Job Server {PLUGIN_VERSION} =====\n"


def test_uvicorn_lines_in_startup_log_carry_the_time():
    import logging.config

    from job_server.server_app import log_config

    config = log_config()
    for formatter in config["formatters"].values():
        assert formatter["fmt"].startswith("%(asctime)s ")
        assert formatter["datefmt"] == lifecycle.TIME_FORMAT
    logging.config.dictConfig(config)  # Still a valid uvicorn configuration.
