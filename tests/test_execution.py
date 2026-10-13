import json
import os
import subprocess
import sys
import time

import psutil
import pytest

from job_server.core import build_wrapper
from job_server.db import Database
from job_server.instance import InstanceLock
from job_server.jobs import Jobs
from job_server.scheduler import Scheduler
from .conftest import reserve


def wait_job(client, job_id, state, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.app.state.scheduler.tick()
        job = client.app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if job["state"] in state:
            return job
        time.sleep(0.1)
    control = client.app.state.settings.root / "control" / job_id
    details = [p.read_text(errors="replace") for p in control.glob("*.log")]
    pytest.fail(f"Job did not reach {state}: {job}; {details}")


@pytest.mark.parametrize("rc", [0, 7])
def test_native_execution_preserves_explicit_exit(client, alice, rc):
    response = reserve(client, alice, command=f"echo example; exit {rc}")
    job_id = response.json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
    job = wait_job(client, job_id, {"DONE", "FAILED", "LOST"})
    assert job["state"] == ("DONE" if rc == 0 else "FAILED")
    assert job["exit_code"] == rc
    assert (
        "example"
        in client.get(f"/api/v1/jobs/{job_id}/log", headers=alice["headers"]).json()["text"]
    )


def test_bash_wrapper_real_command(tmp_path):
    if os.name == "nt":
        shell = r"C:\Program Files\Git\bin\bash.exe"
        if not os.path.exists(shell):
            pytest.skip("Git Bash unavailable")
    else:
        shell = "/bin/bash"
    input_name = "input ' with {stem}.inp"
    (tmp_path / input_name).write_text("input text")
    spec = {
        "name": "example",
        "command": "cat {input} > result.out; exit 7",
        "files": [{"path": input_name}],
        "resources": {"cpus_per_task": 1, "memory_mb": 512, "walltime_seconds": 30},
    }
    script, _ = build_wrapper(spec, tmp_path.as_posix())
    path = tmp_path / "run.sh"
    path.write_text(script)
    result = subprocess.run([shell, path.as_posix()], capture_output=True, timeout=10)
    assert result.returncode == 7, result.stderr
    assert (tmp_path / "result.out").read_text() == "input text"
    assert (tmp_path / ".moleditpy_rc").read_text().strip() == "7"


def test_cpu_memory_queue_and_restart_reconciliation(client, alice):
    command = "Start-Sleep -Seconds 3" if os.name == "nt" else "sleep 3"
    request = {"cpus_per_task": 2, "memory_mb": 1024, "walltime_seconds": 15}
    first = reserve(client, alice, command=command, resources=request).json()["id"]
    second = reserve(client, alice, command="echo second", resources=request).json()["id"]
    for identifier in (first, second):
        client.post(f"/api/v1/jobs/{identifier}/commit", headers=alice["headers"])
    scheduler = client.app.state.scheduler
    scheduler.tick()
    scheduler.tick()
    assert scheduler.resources()["running"] == 1
    assert scheduler.resources()["reserved_cpus"] == 2
    # Reopen a second store/scheduler after releasing the old dispatcher; active
    # supervisors are identified by pid + creation time and are never relaunched.
    settings = client.app.state.settings
    db = Database(settings.root)
    recovered = Scheduler(db, Jobs(db, settings), settings)
    recovered.reconcile()
    assert db.one("SELECT state FROM jobs WHERE id=?", (first,))["state"] == "RUNNING"
    client.app.state.scheduler = recovered
    try:
        wait_job(client, first, {"DONE"})
        wait_job(client, second, {"DONE"})
        assert (
            len(
                db.rows(
                    "SELECT * FROM events WHERE event='dispatch_claimed' AND job_id=?", (first,)
                )
            )
            == 1
        )
    finally:
        db.close()
        client.app.state.scheduler = scheduler


def test_running_cancel_waits_for_process_tree_exit(client, alice):
    executable = sys.executable.replace("\\", "/")
    code = "import time; time.sleep(30)"
    command = f"& '{executable}' -c '{code}'" if os.name == "nt" else f"'{executable}' -c '{code}'"
    job_id = reserve(client, alice, command=command).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
    client.app.state.scheduler.tick()
    control = client.app.state.settings.root / "control" / job_id
    deadline = time.monotonic() + 10
    while not (control / "worker.json").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    supervisor = json.loads((control / "worker.json").read_text())
    time.sleep(0.6)
    try:
        descendants = psutil.Process(supervisor["pid"]).children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []
    response = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=alice["headers"])
    assert response.json()["state"] == "RUNNING"
    job = wait_job(client, job_id, {"CANCELLED"})
    assert job["reason"] == "cancelled"
    for child in descendants:
        try:
            assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass


def test_walltime_stops_native_payload(client, alice):
    command = "Start-Sleep -Seconds 30" if os.name == "nt" else "sleep 30"
    resources = {"cpus_per_task": 1, "memory_mb": 512, "walltime_seconds": 1}
    job_id = reserve(client, alice, command=command, resources=resources).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
    job = wait_job(client, job_id, {"FAILED"})
    assert job["reason"] == "walltime_exceeded"


def test_single_instance_lock_rejects_double_start(tmp_path):
    first = InstanceLock(tmp_path).acquire()
    try:
        with pytest.raises(RuntimeError):
            InstanceLock(tmp_path).acquire()
    finally:
        first.release()
    second = InstanceLock(tmp_path).acquire()
    second.release()


def test_ambiguous_claim_keeps_allocation_and_never_relaunches(client, alice):
    job_id = reserve(client, alice).json()["id"]
    with client.app.state.db.transaction() as c:
        c.execute(
            "UPDATE jobs SET state='RUNNING',started_at=? WHERE id=?", (time.time() - 20, job_id)
        )
    client.app.state.scheduler.tick()
    job = client.app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["state"] == "RUNNING"
    assert "NEEDS_RECHECK" in job["reason"]
    assert client.app.state.scheduler.resources()["reserved_cpus"] == 1
