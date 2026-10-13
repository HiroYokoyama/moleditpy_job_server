"""Real optional Job Manager client compatibility with the running Job Server API."""

import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from job_server.api import create_app
from job_server.settings import Settings

MANAGER = Path(__file__).resolve().parents[2] / "moleditpy_job_manager"


def test_existing_manager_checkout_is_preserved(tmp_path, monkeypatch):
    from scripts.ensure_job_manager import ensure_checkout

    target = tmp_path / "manager"
    (target / "job_manager").mkdir(parents=True)
    source = target / "job_manager/server_backend.py"
    source.write_text("existing local work")
    monkeypatch.setattr(
        "scripts.ensure_job_manager.subprocess.run",
        lambda *a, **k: pytest.fail("Existing checkout must not run Git"),
    )
    assert ensure_checkout(target) == target
    assert source.read_text() == "existing local work"


@pytest.fixture
def manager_types():
    if not (MANAGER / "job_manager/server_backend.py").is_file():
        pytest.skip(
            "Run python scripts/ensure_job_manager.py to prepare the sibling client checkout"
        )
    sys.path.insert(0, str(MANAGER))
    try:
        from job_manager.models import HostProfile, Job, SubmitPreset
        from job_manager.server_backend import JobServerBackend

        yield HostProfile, Job, SubmitPreset, JobServerBackend
    finally:
        sys.path.remove(str(MANAGER))


@pytest.fixture
def live_server(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(Settings(data_dir=str(tmp_path / "server"), port=port, cpus=2, memory_mb=1024))
    app.state.auth.create_user("example-client", "synthetic-client-password-123")
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            proxy_headers=False,
            access_log=False,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(15)
        assert not thread.is_alive()


def wait_state(backend, job, expected, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = backend.poll([job])[job.id]
        if state in expected:
            return state
        time.sleep(0.1)
    pytest.fail(f"Job did not reach {expected}; last state {state}")


def client_for(manager_types, live_server, tmp_path):
    HostProfile, Job, SubmitPreset, Backend = manager_types
    url, _ = live_server
    host = HostProfile(
        backend="job_server",
        server_url=url,
        server_key_path=str(tmp_path / "client.key"),
        scheduler="windows" if os.name == "nt" else "shell",
    )
    backend = Backend(host)
    backend.client.enroll("example-client", "synthetic-client-password-123")
    return host, backend, Job, SubmitPreset


def test_real_manager_enrollment_execution_logs_and_verified_results(
    manager_types, live_server, tmp_path
):
    host, backend, Job, Preset = client_for(manager_types, live_server, tmp_path)
    source = tmp_path / "input.inp"
    source.write_text("synthetic input\n")
    command = (
        "Write-Output 'manager-client-ok'; 'verified output' | Out-File result.out"
        if os.name == "nt"
        else "printf 'manager-client-ok\\n'; printf 'verified output\\n' > result.out"
    )
    job = Job(name="synthetic-client-job", scheduler=host.scheduler)
    backend.submit(
        Preset(command_template=command, memory="512M", fetch_globs=["*.out"]), job, [str(source)]
    )
    assert wait_state(backend, job, {"DONE", "FAILED", "LOST"}) == "DONE"
    assert job.rc == 0
    assert "manager-client-ok" in backend.tail(job)
    result = backend.fetch(job, tmp_path / "results", ["*.out"])
    assert len(result) == 1 and Path(result[0]).read_text().strip() == "verified output"
    assert backend.tail_file(job, "result.out").strip() == "verified output"
    backend.client.token = None
    assert backend.client.request("GET", "/me")["username"] == "example-client"


def test_real_manager_cancellation_preserves_server_state_until_stopped(
    manager_types, live_server, tmp_path
):
    host, backend, Job, Preset = client_for(manager_types, live_server, tmp_path)
    command = "Start-Sleep -Seconds 30" if os.name == "nt" else "sleep 30"
    job = Job(name="synthetic-cancel-job", scheduler=host.scheduler)
    backend.submit(Preset(command_template=command, memory="512M"), job, [])
    assert wait_state(backend, job, {"RUNNING"}) == "RUNNING"
    response = backend.cancel(job)
    assert response["state"] in {"RUNNING", "CANCELLED"}
    assert wait_state(backend, job, {"CANCELLED"}) == "CANCELLED"


def test_real_manager_results_obey_owner_acl(manager_types, live_server, tmp_path):
    host, backend, Job, Preset = client_for(manager_types, live_server, tmp_path)
    job = Job(name="synthetic-owner-job", scheduler=host.scheduler)
    backend.submit(Preset(command_template="echo hello", memory="512M"), job, [])
    assert wait_state(backend, job, {"DONE"}) == "DONE"
    url, app = live_server
    app.state.auth.create_user("example-other", "synthetic-client-password-123")
    Host, _, _, Backend = manager_types
    other = Backend(
        Host(backend="job_server", server_url=url, server_key_path=str(tmp_path / "other.key"))
    )
    other.client.enroll("example-other", "synthetic-client-password-123")
    with pytest.raises(RuntimeError) as error:
        other.files(job)
    assert error.value.status == 404
