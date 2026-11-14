"""What sign-in writes to the event log: failures, not routine successes."""

import base64
import hashlib
import json
import uuid

from job_server.auth import signing_message
from .conftest import enroll, reserve


def events(client, name):
    rows = client.app.state.db.rows(
        "SELECT actor, details FROM events WHERE event=? ORDER BY seq", (name,)
    )
    return [(row["actor"], json.loads(row["details"] or "{}")) for row in rows]


def sign_in(client, identity, *, nonce=None, signature=None):
    challenge = client.post("/api/v1/auth/challenge", json={"device_id": identity["device_id"]})
    nonce = nonce or challenge.json()["nonce"]
    if signature is None:
        message = signing_message(challenge.json()["server_id"], identity["device_id"], nonce)
        signature = base64.b64encode(identity["key"].sign(message)).decode()
    return client.post(
        "/api/v1/auth/token",
        json={"device_id": identity["device_id"], "nonce": nonce, "signature": signature},
    )


def test_a_device_sign_in_is_not_an_event(client, alice):
    assert sign_in(client, alice).status_code == 200
    assert events(client, "device_authenticated") == []
    assert events(client, "device_auth_failed") == []


def test_a_bad_signature_is_recorded_with_its_device_and_address(client, alice):
    forged = base64.b64encode(b"\0" * 64).decode()
    assert sign_in(client, alice, signature=forged).status_code == 401
    [(actor, details)] = events(client, "device_auth_failed")
    assert actor == alice["user"]["id"]
    assert details == {
        "device_id": alice["device_id"], "reason": "bad_signature", "address": "testclient",
    }


def test_an_unknown_challenge_and_an_unknown_device_are_recorded(client, alice):
    assert sign_in(client, alice, nonce="not-issued").status_code == 401
    assert client.post("/api/v1/auth/challenge", json={"device_id": "nobody"}).status_code == 401
    reasons = [(actor, d["reason"]) for actor, d in events(client, "device_auth_failed")]
    assert reasons == [(alice["user"]["id"], "unknown_challenge"), (None, "unknown_device")]


def test_a_revoked_device_is_recorded(client, alice):
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE devices SET revoked_at=1 WHERE id=?", (alice["device_id"],))
    assert client.post(
        "/api/v1/auth/challenge", json={"device_id": alice["device_id"]}
    ).status_code == 401
    [(_, details)] = events(client, "device_auth_failed")
    assert details["reason"] == "revoked_device"


def test_login_failures_are_recorded_without_the_password(client):
    user = client.app.state.auth.create_user("alice", "example-password-123")
    for username, password in [("alice", "wrong-password"), ("mallory", "whatever")]:
        response = client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 401
    recorded = events(client, "login_failed")
    assert [(actor, d["username"], d["reason"]) for actor, d in recorded] == [
        (user["id"], "alice", "wrong_password"),
        (None, "mallory", "unknown_user"),
    ]
    assert all(d["address"] == "testclient" and d["web"] is False for _, d in recorded)
    text = json.dumps(recorded)
    assert "wrong-password" not in text and "whatever" not in text


def test_every_failure_is_recorded_and_the_limit_once_a_minute(client):
    for _ in range(25):
        client.post("/api/v1/auth/challenge", json={"device_id": "unknown"})
    # The limiter turns away five; each attempt it let through is a failure row.
    assert len(events(client, "device_auth_failed")) == 20
    assert events(client, "auth_rate_limited") == [(None, {"source": "testclient"})]


def test_job_created_records_a_summary_not_the_specification(client, alice):
    data = b"hello"
    files = [{"path": "in.txt", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]
    response = reserve(client, alice, command="secret-command --flag", files=files)
    assert response.status_code in (200, 201), response.text
    [(actor, details)] = events(client, "job_created")
    assert actor == alice["user"]["id"]
    assert details["name"] == "example" and details["files"] == 1 and details["bytes"] == 5
    assert {"executor", "cpus", "memory_mb", "walltime_seconds"} <= details.keys()
    assert "after_job" not in details
    assert "secret-command" not in json.dumps(details) and "sha256" not in details


def test_two_users_fail_independently(client):
    bob = enroll(client, "bob")
    forged = base64.b64encode(b"\1" * 64).decode()
    sign_in(client, bob, signature=forged)
    sign_in(client, bob, nonce=str(uuid.uuid4()))
    assert [d["reason"] for _, d in events(client, "device_auth_failed")] == [
        "bad_signature", "unknown_challenge",
    ]
