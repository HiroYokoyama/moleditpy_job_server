"""Administrator queue overrides remain explicit, atomic and dependency-safe."""

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import psutil
import pytest

from job_server.db import Database, SCHEMA
from .conftest import enroll, reserve


def pending(client, identity, **extra):
    response = reserve(client, identity, **extra)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    response = client.post(f"/api/v1/jobs/{identifier}/commit", headers=identity["headers"])
    assert response.status_code == 200, response.text
    return identifier


@pytest.fixture
def admin(client):
    return enroll(client, "queue-admin", "admin")


@pytest.fixture
def launches(client, monkeypatch):
    calls = []

    def launch(args, **kwargs):
        calls.append(args[-1])
        return SimpleNamespace(pid=123456789, poll=lambda: None)

    def missing_process(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr("job_server.scheduler.subprocess.Popen", launch)
    monkeypatch.setattr("job_server.scheduler.psutil.Process", missing_process)
    return calls


def change(client, admin, identifier, action, value):
    return client.post(f"/api/v1/admin/jobs/{identifier}/{action}", headers=admin["headers"], json=value)


def test_queue_priority_is_higher_first_and_fifo_within_equal_priority(client, admin, alice, launches):
    client.app.state.settings.cpus = 3
    client.app.state.settings.memory_mb = 4096
    client.app.state.settings.max_active_per_user = 3
    first, second, third = [pending(client, alice) for _ in range(3)]
    with client.app.state.db.transaction() as c:
        for index, identifier in enumerate((first, second, third)):
            c.execute("UPDATE jobs SET created_at=? WHERE id=?", (index, identifier))
    for identifier in (second, third):
        response = change(client, admin, identifier, "priority", {"priority": 50})
        assert response.status_code == 200
        assert response.json()["priority"] == 50
    for _ in range(3):
        client.app.state.scheduler.tick()
    assert launches == [second, third, first]


def test_force_run_bypasses_pause_and_capacity_only_for_requested_job(client, admin, alice, launches):
    client.app.state.settings.max_active_per_user = 1
    client.app.state.settings.memory_mb = 4096
    first = pending(client, alice, resources={"cpus_per_task": 2, "memory_mb": 4096})
    waiting = pending(client, alice)
    forced = pending(client, alice, resources={"cpus_per_task": 2, "memory_mb": 4096})
    client.app.state.scheduler.tick()
    assert launches == [first]
    assert client.post("/api/v1/admin/queue/pause", headers=admin["headers"], json={"paused": True}).status_code == 200
    response = change(client, admin, forced, "force-run", {"force_run": True})
    assert response.status_code == 200
    assert response.json()["force_run"] is True
    client.app.state.scheduler.tick()
    client.app.state.scheduler.tick()
    assert launches == [first, forced]
    resources = client.app.state.scheduler.resources()
    assert resources["reserved_cpus"] == 4
    assert resources["reserved_memory_mb"] == 8192
    assert client.app.state.db.one("SELECT state FROM jobs WHERE id=?", (waiting,))["state"] == "PENDING"
    assert client.app.state.db.one("SELECT force_run FROM jobs WHERE id=?", (forced,))["force_run"] == 0
    assert client.post("/api/v1/admin/queue/pause", headers=admin["headers"], json={"paused": False}).status_code == 200
    client.app.state.scheduler.tick()
    assert launches == [first, forced], "Forced reservations must still block normal jobs"
    event = client.app.state.db.one("SELECT actor,details FROM events WHERE event='dispatch_claimed' AND job_id=?", (forced,))
    assert event["actor"] == admin["user"]["id"]
    assert json.loads(event["details"])["forced"] is True


@pytest.mark.parametrize("chain_any", [False, True])
def test_force_run_never_bypasses_dependency_policy(client, admin, alice, launches, chain_any):
    predecessor = pending(client, alice)
    dependent = pending(client, alice, after_job=predecessor, chain_any=chain_any)
    change(client, admin, dependent, "force-run", {"force_run": True})
    client.post("/api/v1/admin/queue/pause", headers=admin["headers"], json={"paused": True})
    client.app.state.scheduler.tick()
    assert launches == []
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE jobs SET state='FAILED' WHERE id=?", (predecessor,))
    client.app.state.scheduler.tick()
    assert launches == ([dependent] if chain_any else [])


@pytest.mark.parametrize("revoke", ["disable", "demote"])
def test_deferred_force_request_loses_override_when_administrator_loses_authority(client, admin, alice, launches, revoke):
    predecessor = pending(client, alice)
    dependent = pending(client, alice, after_job=predecessor)
    occupying = pending(client, alice)
    change(client, admin, dependent, "force-run", {"force_run": True})
    client.post("/api/v1/admin/queue/pause", headers=admin["headers"], json={"paused": True})
    client.app.state.scheduler.tick()
    assert launches == []
    with client.app.state.db.transaction() as c:
        if revoke == "disable":
            c.execute("UPDATE users SET enabled=0 WHERE id=?", (admin["user"]["id"],))
        else:
            c.execute("UPDATE users SET role='user' WHERE id=?", (admin["user"]["id"],))
        c.execute("UPDATE jobs SET state='DONE' WHERE id=?", (predecessor,))
    client.app.state.scheduler.tick()
    assert launches == [], "Revoked authority must not bypass queue pause"
    db = client.app.state.db
    assert db.one("SELECT force_run,force_actor FROM jobs WHERE id=?", (dependent,)) == {"force_run": 0, "force_actor": None}
    assert db.one("SELECT event FROM events WHERE job_id=? AND event='job_force_run_revoked'", (dependent,))
    with db.transaction() as c:
        c.execute("UPDATE metadata SET value='false' WHERE key='paused'")
        # Occupied reservations still block the now-ordinary dependent job.
        c.execute("UPDATE jobs SET state='RUNNING',started_at=? WHERE id=?", (time.time(), occupying))
    client.app.state.settings.max_active_per_user = 1
    client.app.state.scheduler.tick()
    assert launches == []
    with db.transaction() as c:
        c.execute("UPDATE jobs SET state='DONE' WHERE id=?", (occupying,))
    client.app.state.scheduler.tick()
    assert launches == [dependent], "Revoking Force must not cancel an otherwise eligible normal job"


def test_force_request_can_be_withdrawn_before_dispatch(client, admin, alice, launches):
    identifier = pending(client, alice)
    assert change(client, admin, identifier, "force-run", {"force_run": True}).status_code == 200
    response = change(client, admin, identifier, "force-run", {"force_run": False})
    assert response.status_code == 200
    assert response.json()["force_run"] is False
    client.post("/api/v1/admin/queue/pause", headers=admin["headers"], json={"paused": True})
    client.app.state.scheduler.tick()
    assert launches == []


def test_cancelled_force_request_never_dispatches(client, admin, alice, launches):
    identifier = pending(client, alice)
    change(client, admin, identifier, "force-run", {"force_run": True})
    response = client.post(f"/api/v1/jobs/{identifier}/cancel", headers=alice["headers"])
    assert response.status_code == 202
    assert response.json()["force_run"] is False
    client.app.state.scheduler.tick()
    assert launches == []


def test_web_queue_controls_follow_account_role_and_do_not_enroll_devices(client, admin, alice):
    identifier = pending(client, alice)
    count = len(client.app.state.db.rows("SELECT id FROM devices"))
    for identity, expected in [(alice, 403), (admin, 200)]:
        session = client.post(
            "/api/v1/auth/web-login",
            json={"username": identity["user"]["username"], "password": "example-password-123"},
        ).json()
        web = {"headers": {"Authorization": "Bearer " + session["token"]}}
        assert change(client, web, identifier, "priority", {"priority": 100}).status_code == expected
        assert change(client, web, identifier, "force-run", {"force_run": True}).status_code == expected
    assert len(client.app.state.db.rows("SELECT id FROM devices")) == count


@pytest.mark.parametrize("value", [1, "true", None])
def test_force_run_flag_is_strict(client, admin, alice, value):
    identifier = pending(client, alice)
    assert change(client, admin, identifier, "force-run", {"force_run": value}).status_code == 400


@pytest.mark.parametrize("action,payload", [("priority", {"priority": 100}), ("force-run", {"force_run": True})])
def test_nonadmin_and_stale_admin_cannot_change_queue(client, admin, alice, action, payload):
    identifier = pending(client, alice)
    assert change(client, alice, identifier, action, payload).status_code == 403
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE sessions SET authenticated_at=? WHERE user_id=?", (time.time() - 301, admin["user"]["id"]))
    assert change(client, admin, identifier, action, payload).status_code == 401
    job = client.app.state.db.one("SELECT priority,force_run FROM jobs WHERE id=?", (identifier,))
    assert job == {"priority": 0, "force_run": 0}


@pytest.mark.parametrize("state", ["UPLOADING", "RUNNING", "DONE", "CANCELLED", "FAILED", "LOST"])
def test_only_committed_pending_jobs_accept_queue_controls(client, admin, alice, state):
    identifier = pending(client, alice)
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE jobs SET state=? WHERE id=?", (state, identifier))
    assert change(client, admin, identifier, "priority", {"priority": 1}).status_code == 409
    assert change(client, admin, identifier, "force-run", {"force_run": True}).status_code == 409


@pytest.mark.parametrize("priority", [-1001, 1001, True, "100", 1.5])
def test_queue_priority_is_strict_and_bounded(client, admin, alice, priority):
    identifier = pending(client, alice)
    assert change(client, admin, identifier, "priority", {"priority": priority}).status_code == 400


def test_queue_overrides_cannot_be_smuggled_into_submission(client, alice):
    assert reserve(client, alice, priority=1000).status_code == 400
    assert reserve(client, alice, force_run=True).status_code == 400


@pytest.mark.parametrize("action,payload", [("priority", {"priority": 100}), ("force-run", {"force_run": True})])
def test_queue_mutation_rechecks_admin_after_request_authorization(client, admin, alice, action, payload, monkeypatch):
    identifier = pending(client, alice)
    db = client.app.state.db
    other = Database(client.app.state.settings.root)
    transaction = db.transaction

    @contextmanager
    def concurrent_disable():
        # Admission already succeeded. A different local connection disables the
        # administrator before the mutation obtains its write lock.
        with other.transaction() as c:
            c.execute("UPDATE users SET enabled=0 WHERE id=?", (admin["user"]["id"],))
        with transaction() as c:
            yield c

    monkeypatch.setattr(db, "transaction", concurrent_disable)
    try:
        assert change(client, admin, identifier, action, payload).status_code == 401
        assert db.one("SELECT priority,force_run FROM jobs WHERE id=?", (identifier,)) == {"priority": 0, "force_run": 0}
    finally:
        other.close()


def test_concurrent_ticks_consume_force_request_exactly_once(client, admin, alice, launches):
    identifier = pending(client, alice)
    change(client, admin, identifier, "force-run", {"force_run": True})
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: client.app.state.scheduler.tick(), range(2)))
    assert launches == [identifier]
    assert len(client.app.state.db.rows(
        "SELECT seq FROM events WHERE event='dispatch_claimed' AND job_id=?", (identifier,),
    )) == 1


def test_old_database_queue_migration_preserves_records_and_is_idempotent(tmp_path):
    old = SCHEMA.replace(
        ", priority INTEGER NOT NULL DEFAULT 0,\n force_run INTEGER NOT NULL DEFAULT 0, force_actor TEXT", ""
    )
    connection = sqlite3.connect(tmp_path / "server.db")
    connection.executescript(old)
    connection.execute("INSERT INTO users VALUES ('user','example','synthetic','user',1,0)")
    connection.execute("INSERT INTO jobs(id,owner_id,spec,state,created_at) VALUES ('job','user','{}','PENDING',0)")
    connection.commit()
    connection.close()
    for _ in range(2):
        db = Database(tmp_path)
        try:
            record = db.one("SELECT owner_id,state,priority,force_run,force_actor FROM jobs WHERE id='job'")
            assert record == {"owner_id": "user", "state": "PENDING", "priority": 0, "force_run": 0, "force_actor": None}
        finally:
            db.close()


def test_a_job_that_reserves_nothing_starts_beside_a_full_budget(client, admin, alice, launches):
    settings = client.app.state.settings
    settings.cpus, settings.memory_mb, settings.max_active_per_user = 2, 4096, 5
    full = pending(client, alice, resources={"cpus_per_task": 2, "memory_mb": 4096})
    waiter = pending(client, alice, resources={"cpus_per_task": 0, "memory_mb": 0})
    blocked = pending(client, alice, resources={"cpus_per_task": 1, "memory_mb": 0})
    forced = pending(client, alice, resources={"cpus_per_task": 2, "memory_mb": 4096})
    with client.app.state.db.transaction() as c:
        for index, identifier in enumerate((full, waiter, blocked, forced)):
            c.execute("UPDATE jobs SET created_at=? WHERE id=?", (index, identifier))
    for _ in range(3):
        client.app.state.scheduler.tick()
    assert launches == [full, waiter]
    # A forced job takes the budget past its limit; a job asking for nothing still fits.
    assert change(client, admin, forced, "force-run", {"force_run": True}).status_code == 200
    client.app.state.scheduler.tick()
    second_waiter = pending(client, alice, resources={"cpus_per_task": 0, "memory_mb": 0})
    for _ in range(2):
        client.app.state.scheduler.tick()
    assert launches == [full, waiter, forced, second_waiter]
    resources = client.app.state.scheduler.resources()
    assert (resources["reserved_cpus"], resources["reserved_memory_mb"]) == (4, 8192)
    assert client.app.state.db.one("SELECT state FROM jobs WHERE id=?", (blocked,))["state"] == "PENDING"


def test_a_job_that_reserves_nothing_still_counts_towards_its_owner_limit(client, alice, launches):
    client.app.state.settings.max_active_per_user = 1
    first = pending(client, alice, resources={"cpus_per_task": 0, "memory_mb": 0})
    pending(client, alice, resources={"cpus_per_task": 0, "memory_mb": 0})
    client.app.state.scheduler.tick()
    client.app.state.scheduler.tick()
    assert launches == [first]


@pytest.mark.parametrize("resources", [{"cpus_per_task": -1}, {"memory_mb": -1}])
def test_a_negative_reservation_is_refused(client, alice, resources):
    assert reserve(client, alice, resources=resources).status_code == 400
