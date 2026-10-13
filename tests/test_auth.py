import base64
import time

from job_server.auth import signing_message
from .conftest import enroll


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
        "server_version": "0.1.0",
    }
    assert (
        client.get("/api/v1/health", headers={"Origin": "https://example.org"}).status_code == 403
    )
    assert client.get("/api/v1/capabilities").status_code == 401
    assert client.get("/api/v1/missing").json()["request_id"]
