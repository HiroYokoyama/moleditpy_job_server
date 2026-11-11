import base64
import time

import pytest

from job_server.auth import signing_message
from job_server import PLUGIN_VERSION
from .conftest import enroll


def test_web_login_needs_no_device_and_logout_revokes_only_its_session(client):
    client.app.state.auth.create_initial_admin("admin", "xxxx")
    sessions = [client.post("/api/v1/auth/web-login", json={"username": "admin", "password": "xxxx"}).json() for _ in range(2)]
    headers = [{"Authorization": "Bearer " + session["token"]} for session in sessions]
    assert client.get("/api/v1/admin/users", headers=headers[0]).status_code == 200
    assert client.app.state.db.rows("SELECT id FROM devices") == []
    assert all(session["scope"] == "web" for session in sessions)
    assert client.post("/api/v1/auth/logout", headers=headers[0]).status_code == 200
    assert client.get("/api/v1/me", headers=headers[0]).status_code == 401
    assert client.get("/api/v1/me", headers=headers[1]).status_code == 200
    assert client.post("/api/v1/auth/web-login", json={"username": "admin", "password": "wrong"}).status_code == 401


def test_web_session_expiry_freshness_and_disabled_user(client):
    user = client.app.state.auth.create_initial_admin("admin", "xxxx")
    session = client.post("/api/v1/auth/web-login", json={"username": "admin", "password": "xxxx"}).json()
    headers = {"Authorization": "Bearer " + session["token"]}
    assert 0 < session["expires_at"] - time.time() <= client.app.state.settings.token_seconds
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE sessions SET authenticated_at=?", (time.time() - 301,))
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 401
    assert client.get("/api/v1/me", headers=headers).status_code == 200
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE users SET enabled=0 WHERE id=?", (user["id"],))
    assert client.get("/api/v1/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/web-login", json={"username": "admin", "password": "xxxx"}).status_code == 401


def test_web_login_still_rejects_unconfigured_browser_origin(client):
    client.app.state.auth.create_initial_admin("admin", "xxxx")
    assert client.post(
        "/api/v1/auth/web-login", json={"username": "admin", "password": "xxxx"},
        headers={"Origin": "https://other.example.ts.net"},
    ).status_code == 403


def test_web_user_cannot_escalate_or_register_persistent_device(client):
    from uuid import uuid4

    client.app.state.auth.create_user("web-user", "xxxx")
    session = client.post("/api/v1/auth/web-login", json={"username": "web-user", "password": "xxxx"}).json()
    headers = {"Authorization": "Bearer " + session["token"]}
    for path in ["/admin/users", "/admin/devices", "/admin/jobs"]:
        assert client.get("/api/v1" + path, headers=headers).status_code == 403
    for path, payload in [
        ("/admin/users", {"username": "intruder", "password": "xxxx", "role": "admin"}),
        ("/admin/shutdown", {}),
        ("/admin/queue/pause", {"paused": True}),
    ]:
        assert client.post("/api/v1" + path, headers=headers, json=payload).status_code == 403
    assert client.post(
        "/api/v1/me/devices", headers=headers,
        json={"device_id": str(uuid4()), "public_key": "AAAA"},
    ).status_code == 403
    assert client.app.state.db.rows("SELECT id FROM users WHERE role='admin'") == []
    assert client.app.state.db.rows("SELECT id FROM devices") == []
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_web_login_schema_does_not_accept_an_asserted_role(client):
    client.app.state.auth.create_user("web-user", "xxxx")
    assert client.post(
        "/api/v1/auth/web-login", json={"username": "web-user", "password": "xxxx", "role": "admin"},
    ).status_code == 400


def test_web_login_disabled_with_web_ui_and_lifetime_is_capped(client):
    client.app.state.auth.create_user("web-user", "xxxx")
    client.app.state.settings.token_seconds = 3600
    session = client.post("/api/v1/auth/web-login", json={"username": "web-user", "password": "xxxx"}).json()
    assert 0 < session["expires_at"] - time.time() <= 900
    client.app.state.settings.web_ui = False
    headers = {"Authorization": "Bearer " + session["token"]}
    assert client.get("/api/v1/me", headers=headers).status_code == 401
    assert client.get("/api/v1/jobs", headers=headers).status_code == 401
    assert client.post(
        "/api/v1/auth/web-login", json={"username": "web-user", "password": "xxxx"},
    ).status_code == 404


def test_disabling_web_access_does_not_disable_desktop_devices(client, alice):
    client.app.state.settings.web_ui = False
    assert client.get("/api/v1/me", headers=alice["headers"]).status_code == 200


def test_web_user_cannot_read_or_cancel_another_users_job(client, alice):
    from uuid import uuid4

    response = client.post(
        "/api/v1/jobs", headers={**alice["headers"], "Idempotency-Key": str(uuid4())},
        json={"name": "Synthetic private job", "command": "echo synthetic"},
    )
    assert response.status_code == 201
    identifier = response.json()["id"]
    client.app.state.auth.create_user("web-other", "xxxx")
    session = client.post(
        "/api/v1/auth/web-login", json={"username": "web-other", "password": "xxxx"},
    ).json()
    headers = {"Authorization": "Bearer " + session["token"]}
    for suffix in ("", "/log", "/files", "/uploads"):
        assert client.get(f"/api/v1/jobs/{identifier}" + suffix, headers=headers).status_code == 404
    assert client.post(f"/api/v1/jobs/{identifier}/cancel", headers=headers).status_code == 404
    assert client.get("/api/v1/jobs", headers=headers).json() == []
    assert client.app.state.db.one("SELECT state FROM jobs WHERE id=?", (identifier,))["state"] == "UPLOADING"


@pytest.mark.parametrize("password", ["four", "short"])
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


@pytest.mark.parametrize("password", ["four", "short"])
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


@pytest.mark.parametrize("password", ["", "x", "abc", "x" * 1025, None])
def test_password_short_type_and_upper_bound_rejected(password):
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


def test_existing_short_password_still_signs_in_but_cannot_be_reused(client):
    """Raising the minimum must not lock out an account created before it."""
    import job_server.auth as auth_module

    original = auth_module.MIN_PASSWORD_LENGTH
    auth_module.MIN_PASSWORD_LENGTH = 1
    try:
        client.app.state.auth.create_user("legacy", "x")
    finally:
        auth_module.MIN_PASSWORD_LENGTH = original
    session = client.post("/api/v1/auth/web-login", json={"username": "legacy", "password": "x"})
    assert session.status_code == 200
    headers = {"Authorization": "Bearer " + session.json()["token"]}
    target = "/api/v1/me/password"
    assert client.post(
        target, headers=headers, json={"current_password": "x", "new_password": "abc"}
    ).status_code == 400
    assert client.post(
        target, headers=headers, json={"current_password": "x", "new_password": "abcd"}
    ).status_code == 200
