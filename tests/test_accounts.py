"""Password changes, resets, roles and re-enabling, with their security boundaries."""

import base64
import json
import time

import pytest

from job_server.auth import signing_message
from .conftest import enroll

PASSWORD = "example-password-123"


def web(client, username, password=PASSWORD):
    response = client.post(
        "/api/v1/auth/web-login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def device_token(client, identity):
    challenge = client.post(
        "/api/v1/auth/challenge", json={"device_id": identity["device_id"]}
    )
    if challenge.status_code != 200:
        return challenge
    body = challenge.json()
    signature = identity["key"].sign(
        signing_message(body["server_id"], identity["device_id"], body["nonce"])
    )
    return client.post(
        "/api/v1/auth/token",
        json={"device_id": identity["device_id"], "nonce": body["nonce"],
              "signature": base64.b64encode(signature).decode()},
    )


def change(client, headers, current=PASSWORD, new="another-password", **extra):
    return client.post(
        "/api/v1/me/password", headers=headers,
        json={"current_password": current, "new_password": new, **extra},
    )


@pytest.fixture
def admin(client):
    return enroll(client, "admin", "admin")


def test_change_password_needs_the_current_one_and_keeps_this_session(client, alice):
    assert change(client, alice["headers"], current="wrong").status_code == 403
    # A mistyped current password leaves the session signed in and the hash alone.
    assert client.get("/api/v1/me", headers=alice["headers"]).status_code == 200
    web(client, "alice")
    assert change(client, alice["headers"], new=PASSWORD).status_code == 400
    other = web(client, "alice")
    assert change(client, alice["headers"]).status_code == 200
    assert client.get("/api/v1/me", headers=alice["headers"]).status_code == 200
    assert client.get("/api/v1/me", headers=other).status_code == 401
    assert client.post(
        "/api/v1/auth/web-login", json={"username": "alice", "password": PASSWORD}
    ).status_code == 401
    web(client, "alice", "another-password")
    # An ordinary password change leaves registered devices working.
    assert device_token(client, alice).status_code == 200


def test_old_enrollment_session_dies_with_a_password_change(client, alice):
    pending = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}
    ).json()["token"]
    assert change(client, alice["headers"]).status_code == 200
    assert client.post(
        "/api/v1/auth/devices", headers={"Authorization": "Bearer " + pending},
        json={"device_id": "00000000-0000-4000-8000-000000000001", "public_key": "AAAA"},
    ).status_code == 401


def test_change_can_revoke_other_devices_but_keeps_the_calling_one(client, alice):
    from uuid import uuid4

    second = uuid4()
    with client.app.state.db.transaction() as c:
        c.execute(
            "INSERT INTO devices(id,user_id,public_key,created_at) VALUES (?,?,?,?)",
            (str(second), alice["user"]["id"], "AAAA", time.time()),
        )
    response = change(client, alice["headers"], revoke_devices=True)
    assert response.json()["devices_revoked"] == 1
    assert device_token(client, alice).status_code == 200
    states = {row["id"]: row["revoked_at"] for row in client.app.state.db.rows("SELECT * FROM devices")}
    assert states[alice["device_id"]] is None and states[str(second)] is not None


def test_web_session_revoking_devices_revokes_them_all(client, alice):
    response = change(client, web(client, "alice"), revoke_devices=True)
    assert response.json()["devices_revoked"] == 1
    assert device_token(client, alice).status_code == 401


def test_password_guessing_is_limited_per_account(client, alice):
    auth = client.app.state.auth
    # Different addresses must not buy a stolen token more guesses.
    auth.attempts["password:" + alice["user"]["id"]].extend([time.monotonic()] * 20)
    assert change(client, alice["headers"], current="wrong").status_code == 429


def test_passwords_never_reach_the_activity_log(client, admin, alice):
    secret = "secret-reset-value"
    change(client, alice["headers"], new="secret-change-value")
    client.post(
        f"/api/v1/admin/users/{alice['user']['id']}/password", headers=admin["headers"],
        json={"new_password": secret},
    )
    text = json.dumps(client.app.state.db.rows("SELECT * FROM events"))
    assert "password_changed" in text and "password_reset" in text
    assert secret not in text and "secret-change-value" not in text


def test_admin_reset_ends_sessions_and_optionally_devices(client, admin, alice):
    target = f"/api/v1/admin/users/{alice['user']['id']}/password"
    session = web(client, "alice")
    assert client.post(target, headers=alice["headers"], json={"new_password": "xxxx"}).status_code == 403
    assert client.post(target, headers=admin["headers"], json={"new_password": "new-one"}).status_code == 200
    assert client.get("/api/v1/me", headers=session).status_code == 401
    assert device_token(client, alice).status_code == 200
    assert client.post(
        target, headers=admin["headers"], json={"new_password": "new-two", "revoke_devices": True}
    ).json()["devices_revoked"] == 1
    assert device_token(client, alice).status_code == 401
    web(client, "alice", "new-two")


def test_admin_cannot_reset_own_password_past_the_current_one(client, admin):
    assert client.post(
        f"/api/v1/admin/users/{admin['user']['id']}/password", headers=admin["headers"],
        json={"new_password": "xxxx"},
    ).status_code == 409


def test_reset_needs_a_recent_administrator_sign_in(client, admin, alice):
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE sessions SET authenticated_at=?", (time.time() - 301,))
    assert client.post(
        f"/api/v1/admin/users/{alice['user']['id']}/password", headers=admin["headers"],
        json={"new_password": "xxxx"},
    ).status_code == 401


def test_disable_then_enable_restores_devices_but_not_old_sessions(client, admin, alice):
    base = f"/api/v1/admin/users/{alice['user']['id']}"
    session = web(client, "alice")
    assert client.post(base + "/enable", headers=alice["headers"]).status_code == 403
    assert client.post(base + "/disable", headers=admin["headers"]).status_code == 200
    assert device_token(client, alice).status_code == 401
    assert client.post(base + "/enable", headers=admin["headers"]).status_code == 200
    assert client.get("/api/v1/me", headers=session).status_code == 401
    assert client.get("/api/v1/me", headers=alice["headers"]).status_code == 401
    assert device_token(client, alice).status_code == 200
    assert client.post("/api/v1/admin/users/missing/enable", headers=admin["headers"]).status_code == 404


def test_admin_cannot_lock_themselves_out(client, admin):
    base = f"/api/v1/admin/users/{admin['user']['id']}"
    assert client.post(base + "/disable", headers=admin["headers"]).status_code == 409
    assert client.post(base + "/role", headers=admin["headers"], json={"role": "user"}).status_code == 409
    assert client.get("/api/v1/admin/users", headers=admin["headers"]).status_code == 200


def test_role_change_takes_effect_and_is_admin_only(client, admin, alice):
    base = f"/api/v1/admin/users/{alice['user']['id']}/role"
    assert client.post(base, headers=alice["headers"], json={"role": "admin"}).status_code == 403
    assert client.post(base, headers=admin["headers"], json={"role": "root"}).status_code == 400
    assert client.post(base, headers=admin["headers"], json={"role": "admin"}).status_code == 200
    promoted = {"Authorization": "Bearer " + device_token(client, alice).json()["token"]}
    assert client.get("/api/v1/admin/users", headers=promoted).status_code == 200
    assert client.post(base, headers=admin["headers"], json={"role": "user"}).status_code == 200
    assert client.get("/api/v1/admin/users", headers=promoted).status_code == 401
    fresh = {"Authorization": "Bearer " + device_token(client, alice).json()["token"]}
    assert client.get("/api/v1/admin/users", headers=fresh).status_code == 403


def test_create_user_can_add_an_administrator(client, admin):
    created = client.post(
        "/api/v1/admin/users", headers=admin["headers"],
        json={"username": "second", "password": "xxxx", "role": "admin"},
    )
    assert created.status_code == 201 and created.json()["role"] == "admin"
    assert client.get("/api/v1/admin/users", headers=web(client, "second", "xxxx")).status_code == 200


def test_cli_reset_recovers_a_forgotten_password(client, admin, tmp_path, monkeypatch):
    import getpass
    import sys
    from job_server.__main__ import main
    from job_server.settings import Settings

    config = tmp_path / "cli-settings.json"
    Settings(data_dir=str(client.app.state.settings.root)).save(config)
    monkeypatch.setattr(sys, "argv", ["job_server", "reset-password", "admin", "--config", str(config)])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "recovered-password")
    assert main() == 0
    web(client, "admin", "recovered-password")
    monkeypatch.setattr(sys, "argv", ["job_server", "reset-password", "nobody", "--config", str(config)])
    assert main() == 1


def test_resources_report_whole_pc_usage_beside_the_budget(client, alice):
    data = client.get("/api/v1/system/resources", headers=alice["headers"]).json()
    assert 0 <= data["cpu_percent"] <= 100
    assert 0 < data["memory_used_mb"] <= data["memory_total_mb"]
    assert data["cpus"] == 2


def test_activity_log_is_admin_only_and_can_read_newest_first(client, admin, alice):
    assert client.get("/api/v1/admin/events?newest=5", headers=alice["headers"]).status_code == 403
    rows = client.get("/api/v1/admin/events?newest=5", headers=admin["headers"]).json()
    assert len(rows) == 5 and rows[0]["seq"] > rows[-1]["seq"]
    assert client.get("/api/v1/admin/events?newest=5000", headers=admin["headers"]).status_code == 400
