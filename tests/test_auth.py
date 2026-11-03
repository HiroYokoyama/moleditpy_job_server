import base64
import time

import pytest

from job_server.auth import signing_message
from job_server import PLUGIN_VERSION
from .conftest import enroll


@pytest.mark.parametrize("password", ["x", "short"])
def test_short_password_initial_admin_and_login(client, password):
    client.app.state.auth.create_initial_admin("short-admin", password)
    response = client.post(
        "/api/v1/auth/login", json={"username": "short-admin", "password": password}
    )
    assert response.status_code == 200
    assert response.json()["scope"] == "enrollment"
    assert client.post(
        "/api/v1/auth/login", json={"username": "short-admin", "password": "wrong"}
    ).status_code == 401


@pytest.mark.parametrize("password", ["x", "short"])
def test_short_password_api_user_creation(client, password):
    admin = enroll(client, "admin", "admin")
    response = client.post(
        "/api/v1/admin/users", headers=admin["headers"],
        json={"username": "short-user", "password": password, "role": "user"},
    )
    assert response.status_code == 201
    assert client.post(
        "/api/v1/auth/login", json={"username": "short-user", "password": password}
    ).status_code == 200


@pytest.mark.parametrize("password", ["", "x" * 1025, None])
def test_password_empty_type_and_upper_bound_still_rejected(password):
    from job_server.auth import password_hash
    from job_server.errors import ServerError
    with pytest.raises(ServerError) as error:
        password_hash(password)
    assert error.value.code == "password_policy"


def test_login_enrollment_cannot_run_jobs(client, alice):
    assert client.get("/api/v1/jobs", headers=alice["enrollment"]).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/devices",
            json={"device_id": alice["device_id"], "public_key": "x"},
            headers=alice["headers"],
        ).status_code
        == 401
    )
    assert client.get("/api/v1/me", headers=alice["headers"]).json()["username"] == "alice"


def test_challenge_one_use_and_server_binding(client, alice):
    challenge = client.post("/api/v1/auth/challenge", json={"device_id": alice["device_id"]}).json()
    signature = alice["key"].sign(
        signing_message("another-server", alice["device_id"], challenge["nonce"])
    )
    body = {
        "device_id": alice["device_id"],
        "nonce": challenge["nonce"],
        "signature": base64.b64encode(signature).decode(),
    }
    assert client.post("/api/v1/auth/token", json=body).status_code == 401
    correct = alice["key"].sign(
        signing_message(challenge["server_id"], alice["device_id"], challenge["nonce"])
    )
    body["signature"] = base64.b64encode(correct).decode()
    assert client.post("/api/v1/auth/token", json=body).status_code == 401


def test_successful_challenge_cannot_replay(client, alice):
    challenge = client.post("/api/v1/auth/challenge", json={"device_id": alice["device_id"]}).json()
    signature = alice["key"].sign(
        signing_message(challenge["server_id"], alice["device_id"], challenge["nonce"])
    )
    body = {
        "device_id": alice["device_id"],
        "nonce": challenge["nonce"],
        "signature": base64.b64encode(signature).decode(),
    }
    assert client.post("/api/v1/auth/token", json=body).status_code == 200
    assert client.post("/api/v1/auth/token", json=body).status_code == 401


def test_revocation_disables_active_tokens(client, alice):
    admin = enroll(client, "admin", "admin")
    assert (
        client.post(
            f"/api/v1/admin/devices/{alice['device_id']}/revoke", headers=admin["headers"]
        ).status_code
        == 200
    )
    assert client.get("/api/v1/me", headers=alice["headers"]).status_code == 401
    assert (
        client.post("/api/v1/auth/challenge", json={"device_id": alice["device_id"]}).status_code
        == 401
    )


def test_disabled_user_tokens_fail(client, alice):
    admin = enroll(client, "admin", "admin")
    assert (
        client.post(
            f"/api/v1/admin/users/{alice['user']['id']}/disable", headers=admin["headers"]
        ).status_code
        == 200
    )
    assert client.get("/api/v1/me", headers=alice["headers"]).status_code == 401


def test_privileged_action_requires_fresh_auth(client, alice):
    admin = enroll(client, "admin", "admin")
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE sessions SET authenticated_at=?", (time.time() - 301,))
    response = client.post(
        "/api/v1/admin/queue/pause", json={"paused": True}, headers=admin["headers"]
    )
    assert response.status_code == 401
    assert response.json()["code"] == "reauthenticate"


def test_rate_limit_and_error_envelope(client):
    for _ in range(20):
        assert (
            client.post("/api/v1/auth/challenge", json={"device_id": "unknown"}).status_code == 401
        )
    response = client.post("/api/v1/auth/challenge", json={"device_id": "unknown"})
    assert response.status_code == 429
    assert {"code", "message", "request_id"} <= response.json().keys()


def test_origin_and_unauthenticated_boundaries(client):
    assert client.get("/api/v1/health").json() == {
        "status": "ok",
        "api_version": "1",
        "server_version": PLUGIN_VERSION,
    }
    assert (
        client.get("/api/v1/health", headers={"Origin": "https://example.org"}).status_code == 403
    )
    assert client.get("/api/v1/capabilities").status_code == 401
    assert client.get("/api/v1/missing").json()["request_id"]
