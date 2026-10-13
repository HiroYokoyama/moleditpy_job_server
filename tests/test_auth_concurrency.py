"""Exercise revocation while authentication or credential creation is in flight."""

import base64
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from job_server.auth import signing_message
from .conftest import enroll
from .test_security import new_device


@pytest.mark.parametrize("action", ["revoke", "disable"])
def test_revocation_blocks_inflight_admin_creation(client, monkeypatch, action):
    admin = enroll(client, "admin", "admin")
    other = enroll(client, "other", "admin")
    import job_server.auth as auth_module

    original = auth_module.password_hash
    started, proceed = threading.Event(), threading.Event()

    def delayed_hash(password):
        result = original(password)
        started.set()
        assert proceed.wait(10)
        return result

    monkeypatch.setattr(auth_module, "password_hash", delayed_hash)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(
            client.post, "/api/v1/admin/users",
            json={"username": "persistent-admin", "password": "synthetic-password", "role": "admin"},
            headers=admin["headers"],
        )
        try:
            assert started.wait(10)
            route = (
                f"devices/{admin['device_id']}/revoke" if action == "revoke"
                else f"users/{admin['user']['id']}/disable"
            )
            assert client.post("/api/v1/admin/" + route, headers=other["headers"]).status_code == 200
        finally:
            proceed.set()
        assert pending.result(timeout=10).status_code == 401
    assert client.app.state.db.one("SELECT 1 FROM users WHERE username='persistent-admin'") is None


def test_parallel_enrollment_consumes_session_once(client):
    client.app.state.auth.create_user("alice", "synthetic-password")
    session = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "synthetic-password"}
    ).json()
    headers = {"Authorization": "Bearer " + session["token"]}
    devices = [new_device(), new_device()]
    barrier = threading.Barrier(2)

    def register(device):
        barrier.wait(timeout=10)
        return client.post("/api/v1/auth/devices", json=device, headers=headers).status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(register, devices)) == [201, 401]
    assert len(client.app.state.db.rows("SELECT id FROM devices")) == 1


def test_parallel_nonce_replay_issues_one_token(client, alice):
    challenge = client.post("/api/v1/auth/challenge", json={"device_id": alice["device_id"]}).json()
    signature = alice["key"].sign(
        signing_message(challenge["server_id"], alice["device_id"], challenge["nonce"])
    )
    body = {
        "device_id": alice["device_id"], "nonce": challenge["nonce"],
        "signature": base64.b64encode(signature).decode(),
    }
    barrier = threading.Barrier(2)

    def authenticate(_):
        barrier.wait(timeout=10)
        return client.post("/api/v1/auth/token", json=body).status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(authenticate, range(2))) == [200, 401]


def test_forwarded_headers_cannot_bypass_auth_rate_limit(client):
    for index in range(21):
        response = client.post(
            "/api/v1/auth/challenge", json={"device_id": "unknown"},
            headers={"X-Forwarded-For": f"192.0.2.{index}", "X-Real-IP": f"192.0.2.{index}"},
        )
        assert response.status_code == (401 if index < 20 else 429)


def test_disable_during_password_verification_does_not_issue_session(client, monkeypatch):
    user = client.app.state.auth.create_user("alice", "synthetic-password")
    import job_server.auth as auth_module

    original = auth_module.verify_password

    def disable_after_verification(password, encoded):
        valid = original(password, encoded)
        with client.app.state.db.transaction() as connection:
            connection.execute("UPDATE users SET enabled=0 WHERE id=?", (user["id"],))
            connection.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        return valid

    monkeypatch.setattr(auth_module, "verify_password", disable_after_verification)
    response = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "synthetic-password"}
    )
    assert response.status_code == 401
    assert client.app.state.db.rows("SELECT * FROM sessions") == []


@pytest.mark.parametrize("operation", ["disable", "revoke", "pause", "shutdown"])
def test_admin_writes_recheck_revoked_session(client, alice, monkeypatch, operation):
    admin = enroll(client, "admin", "admin")
    auth = client.app.state.auth
    original = auth.admin
    calls = 0

    def revoke_after_dependency(principal):
        nonlocal calls
        current = original(principal)
        calls += 1
        if calls == 1:
            # Model revocation completing between dependency validation and the
            # endpoint acquiring its write transaction.
            auth.revoke(admin["device_id"], admin["user"]["id"])
        return current

    monkeypatch.setattr(auth, "admin", revoke_after_dependency)
    shutdowns = []
    client.app.state.shutdown = lambda: shutdowns.append(True)
    routes = {
        "disable": f"users/{alice['user']['id']}/disable",
        "revoke": f"devices/{alice['device_id']}/revoke",
        "pause": "queue/pause", "shutdown": "shutdown",
    }
    response = client.post(
        "/api/v1/admin/" + routes[operation],
        json={"paused": True} if operation == "pause" else {}, headers=admin["headers"],
    )
    assert response.status_code == 401
    assert client.get("/api/v1/me", headers=alice["headers"]).status_code == 200
    assert client.app.state.db.metadata("paused") == "false"
    assert shutdowns == []
