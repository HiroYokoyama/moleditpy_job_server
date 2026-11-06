"""Deleting an account removes access for good and keeps finished jobs."""

import json

import pytest

from .conftest import enroll, reserve
from .test_accounts import PASSWORD, device_token, web


@pytest.fixture
def admin(client):
    return enroll(client, "admin", "admin")


def test_delete_user_removes_access_and_frees_the_name_but_keeps_jobs(client, admin, alice):
    db = client.app.state.db
    target = f"/api/v1/admin/users/{alice['user']['id']}"
    session = web(client, "alice")
    job = reserve(client, alice).json()["id"]
    assert client.delete(target, headers=alice["headers"]).status_code == 403
    # An uncommitted or running job still belongs to a working account.
    assert client.delete(target, headers=admin["headers"]).status_code == 409
    with db.transaction() as c:
        c.execute("UPDATE jobs SET state='DONE' WHERE id=?", (job,))
    response = client.delete(target, headers=admin["headers"])
    assert response.status_code == 200 and response.json()["jobs_kept"] == 1
    assert client.get("/api/v1/me", headers=session).status_code == 401
    assert device_token(client, alice).status_code == 401
    assert client.post(
        "/api/v1/auth/web-login", json={"username": "alice", "password": PASSWORD}
    ).status_code == 401
    assert db.rows("SELECT * FROM devices WHERE user_id=?", (alice["user"]["id"],)) == []
    listed = client.get("/api/v1/admin/users", headers=admin["headers"]).json()
    assert [user["username"] for user in listed] == ["admin"]
    assert client.get(f"/api/v1/jobs/{job}", headers=admin["headers"]).status_code == 200
    # The name is free, and whoever takes it next gets none of the old account.
    enroll(client, "alice")
    fresh = web(client, "alice")
    assert client.get(f"/api/v1/jobs/{job}", headers=fresh).status_code == 404


@pytest.mark.parametrize("action, body", [
    ("/enable", {}), ("/password", {"new_password": "revive"}), ("/role", {"role": "admin"}),
])
def test_a_deleted_account_cannot_be_brought_back(client, admin, alice, action, body):
    target = f"/api/v1/admin/users/{alice['user']['id']}"
    assert client.delete(target, headers=admin["headers"]).status_code == 200
    assert client.post(target + action, headers=admin["headers"], json=body).status_code == 404
    assert client.delete(target, headers=admin["headers"]).status_code == 404
    row = client.app.state.db.one("SELECT * FROM users WHERE id=?", (alice["user"]["id"],))
    assert row["enabled"] == 0 and row["role"] == "deleted" and row["password_hash"] == ""


def stuck_job(client, alice, reason="NEEDS_RECHECK: supervisor identity missing", pid=None):
    job = reserve(client, alice).json()["id"]
    with client.app.state.db.transaction() as c:
        c.execute(
            "UPDATE jobs SET state='RUNNING',reason=?,pid=?,process_started=? WHERE id=?",
            (reason, pid, 0, job),
        )
    return f"/api/v1/admin/jobs/{job}/mark-lost"


def test_only_an_admin_can_release_a_stuck_job_and_it_frees_the_reservation(client, admin, alice):
    target = stuck_job(client, alice)
    assert client.post(target, headers=alice["headers"]).status_code == 403
    response = client.post(target, headers=admin["headers"])
    assert response.status_code == 200 and response.json()["state"] == "LOST"
    resources = client.get("/api/v1/system/resources", headers=admin["headers"]).json()
    assert resources["reserved_cpus"] == 0
    assert client.post(target, headers=admin["headers"]).status_code == 409


def test_a_healthy_or_still_supervised_job_cannot_be_marked_lost(client, admin, alice, monkeypatch):
    assert client.post(stuck_job(client, alice, reason=""), headers=admin["headers"]).status_code == 409
    monkeypatch.setattr(client.app.state.scheduler, "supervisor_alive", lambda job: True)
    assert client.post(stuck_job(client, alice), headers=admin["headers"]).status_code == 409


def test_supervisor_check_rejects_a_reused_pid_and_never_guesses():
    import os
    from job_server.scheduler import Scheduler

    assert not Scheduler.supervisor_alive({"pid": None, "process_started": None})
    assert not Scheduler.supervisor_alive({"pid": os.getpid(), "process_started": 1.0})


def test_admin_cannot_delete_themselves(client, admin):
    assert client.delete(
        f"/api/v1/admin/users/{admin['user']['id']}", headers=admin["headers"]
    ).status_code == 409


def test_deleting_records_who_without_a_password(client, admin, alice):
    client.delete(f"/api/v1/admin/users/{alice['user']['id']}", headers=admin["headers"])
    event = client.app.state.db.one("SELECT * FROM events WHERE event='user_deleted'")
    assert event["actor"] == admin["user"]["id"]
    assert json.loads(event["details"])["username"] == "alice"
