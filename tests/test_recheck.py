"""When a job is held for an administrator's recheck, and when it is released instead."""

import json
import time
from types import SimpleNamespace

import pytest

from job_server import scheduler as scheduler_module
from job_server import worker
from job_server.settings import Settings

from .conftest import reserve


# Supervisor errors -------------------------------------------------------


def run_failing_supervisor(tmp_path, monkeypatch, stop_works):
    """A supervisor whose quota check fails while the payload is still running."""
    settings = Settings(data_dir=str(tmp_path / "data"), executor="wsl", wsl_root="/home/synthetic")
    job_id = "synthetic-job"
    control = settings.root / "control" / job_id
    control.mkdir(parents=True)
    (tmp_path / "linux" / "jobs" / job_id / "work").mkdir(parents=True)
    spec = {
        "name": "synthetic", "command": "echo synthetic", "files": [],
        "resources": {"cpus_per_task": 1, "memory_mb": 512, "walltime_seconds": 30},
    }
    (control / "request.json").write_text(json.dumps({"spec": spec}), encoding="utf-8")
    monkeypatch.setattr(worker.Settings, "load", lambda _: settings)
    monkeypatch.setattr(worker.wsl, "native_root", lambda _: tmp_path / "linux")
    payload = SimpleNamespace(returncode=None, stopped=False)
    payload.poll = lambda: payload.returncode
    payload.wait = lambda timeout=None: payload.returncode

    def start(settings, identifier, wrapper, log):
        return payload, tmp_path / "linux" / "jobs" / job_id / "work" / ".server_process"

    def terminate(*args, **kwargs):
        if not stop_works:
            raise OSError("process group unknown")
        payload.returncode, payload.stopped = -9, True

    def out_of_memory(root):
        raise MemoryError

    monkeypatch.setattr(worker.wsl, "start", start)
    monkeypatch.setattr(worker.wsl, "terminate", terminate)
    monkeypatch.setattr(worker.native, "directory_size", out_of_memory)
    worker.main("unused-synthetic-config", job_id)
    return json.loads((control / "done.json").read_text()), payload


def test_a_supervisor_error_with_the_job_stopped_is_a_plain_failure(tmp_path, monkeypatch):
    result, payload = run_failing_supervisor(tmp_path, monkeypatch, stop_works=True)
    assert payload.stopped
    assert result["state"] == "FAILED"
    assert result["reason"] == "worker_error:MemoryError"
    assert result["exit_code"] == -9


def test_a_supervisor_error_that_cannot_stop_the_job_needs_a_recheck(tmp_path, monkeypatch):
    result, _ = run_failing_supervisor(tmp_path, monkeypatch, stop_works=False)
    assert result["state"] == "LOST"
    assert result["reason"] == "termination_needs_recheck"


# Lost supervisors --------------------------------------------------------


@pytest.fixture
def running(client, alice, monkeypatch):
    """A running job whose supervisor is recorded and has exited."""
    job_id = reserve(client, alice).json()["id"]
    db = client.app.state.db
    with db.transaction() as c:
        c.execute(
            "UPDATE jobs SET state='RUNNING',started_at=?,pid=4242,process_started=1.0 WHERE id=?",
            (time.time() - 20, job_id),
        )
    monkeypatch.setattr(scheduler_module, "JOBS_END_WITH_SUPERVISOR", True)
    monkeypatch.setattr(scheduler_module.Scheduler, "supervisor_alive", staticmethod(lambda job: False))
    return job_id


def use_executor(client, executor):
    client.app.state.settings.executor = executor


def state(client, job_id):
    return client.app.state.db.one("SELECT state,reason FROM jobs WHERE id=?", (job_id,))


def test_a_lost_windows_supervisor_releases_its_job(client, running):
    use_executor(client, "windows")
    scheduler = client.app.state.scheduler
    scheduler.reconcile()
    assert state(client, running) == {"state": "FAILED", "reason": "supervisor_lost"}
    assert scheduler.resources()["reserved_cpus"] == 0
    events = client.app.state.db.rows("SELECT event,details FROM events WHERE job_id=?", (running,))
    assert {"event": "job_finished", "details": json.dumps({"state": "FAILED", "reason": "supervisor_lost"})} in events


@pytest.mark.parametrize("executor", ["linux", "wsl"])
def test_a_lost_supervisor_elsewhere_still_needs_a_recheck(client, running, executor):
    use_executor(client, executor)
    client.app.state.scheduler.reconcile()
    job = state(client, running)
    assert job["state"] == "RUNNING" and job["reason"] == "NEEDS_RECHECK: supervisor identity missing"


def test_an_unverified_windows_stop_is_released_once_the_supervisor_is_gone(client, running):
    use_executor(client, "windows")
    control = client.app.state.settings.root / "control" / running
    control.mkdir(parents=True)
    (control / "done.json").write_text(json.dumps({
        "state": "LOST", "reason": "termination_needs_recheck", "exit_code": None,
        "ended_at": time.time(), "pid": 4242, "process_started": 1.0,
    }))
    client.app.state.scheduler.reconcile()
    assert state(client, running) == {"state": "FAILED", "reason": "stopped_with_supervisor"}


def test_a_windows_supervisor_still_running_is_not_released(client, running, monkeypatch):
    use_executor(client, "windows")
    monkeypatch.setattr(scheduler_module.Scheduler, "supervisor_alive", staticmethod(lambda job: True))
    control = client.app.state.settings.root / "control" / running
    control.mkdir(parents=True)
    (control / "done.json").write_text(json.dumps({
        "state": "LOST", "reason": "termination_needs_recheck", "pid": 4242, "process_started": 1.0,
    }))
    client.app.state.scheduler.reconcile()
    job = state(client, running)
    assert job["state"] == "RUNNING" and job["reason"].startswith("NEEDS_RECHECK")


def test_a_job_finishing_between_the_checks_is_recorded_normally(client, running, monkeypatch):
    use_executor(client, "linux")
    control = client.app.state.settings.root / "control" / running
    real_exists = type(control).exists
    calls = []

    def exists(path):
        # done.json appears after the first look, as a supervisor exiting just then.
        if path.name == "done.json":
            calls.append(path)
            return len(calls) > 1
        return real_exists(path)

    monkeypatch.setattr(type(control), "exists", exists)
    client.app.state.scheduler.reconcile()
    job = state(client, running)
    assert job["state"] == "RUNNING" and not job["reason"].startswith("NEEDS_RECHECK")
