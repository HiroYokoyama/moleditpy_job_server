"""Adversarial HTTP inputs must fail before storage or expensive file work."""

import hashlib
import json

import pytest

from .conftest import reserve


def test_oversized_decimal_content_length_is_rejected(client):
    response = client.post(
        "/api/v1/auth/login", content=b"", headers={"Content-Length": "9" * 5000}
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    "route,body",
    [
        ("login", {"username": "\ud800", "password": "synthetic-password"}),
        ("challenge", {"device_id": "\udfff"}),
        ("token", {"device_id": "\ud800", "nonce": "test", "signature": "test"}),
    ],
)
def test_invalid_unicode_auth_fields_are_rejected(client, route, body, caplog):
    response = client.post(
        "/api/v1/auth/" + route,
        content=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "synthetic-password" not in response.text + caplog.text
    assert not caplog.records


@pytest.mark.parametrize("route", ["jobs", "admin/jobs", "admin/events"])
def test_database_pagination_cannot_overflow(client, route):
    # Use a fresh admin to reach the numeric query validation on all routes.
    from .conftest import enroll

    admin = enroll(client, "admin", "admin")
    parameter = "after" if route.endswith("events") else "offset"
    response = client.get(
        f"/api/v1/{route}?{parameter}={2**63}", headers=admin["headers"]
    )
    assert response.status_code == 400


def test_log_offset_cannot_overflow(client, alice):
    job_id = reserve(client, alice).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
    (client.app.state.jobs.directory(job_id) / "job.log").write_bytes(b"log")
    response = client.get(
        f"/api/v1/jobs/{job_id}/log?offset={2**63}", headers=alice["headers"]
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "range_value",
    ["bytes=" + "9" * 5000 + "-", "bytes=invalid", "bytes=100-", "bytes=3-1", ""],
    ids=["huge", "malformed", "outside-file", "reversed", "empty"],
)
def test_bad_download_range_rejected_before_hashing(client, alice, monkeypatch, range_value):
    job_id = reserve(client, alice).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
    (client.app.state.jobs.directory(job_id) / "result.txt").write_bytes(b"result")

    def forbidden_hash(*args):
        raise AssertionError("Invalid ranges must not trigger a full-file hash")

    monkeypatch.setattr("job_server.api.sha256", forbidden_hash)
    response = client.get(
        f"/api/v1/jobs/{job_id}/files/result.txt",
        headers={**alice["headers"], "Range": range_value},
    )
    assert response.status_code == 416


def test_oversized_upload_range_is_rejected_without_writing(client, alice):
    job_id = reserve(
        client, alice,
        files=[{"path": "input.txt", "size": 1, "sha256": hashlib.sha256(b"a").hexdigest()}],
    ).json()["id"]
    response = client.put(
        f"/api/v1/jobs/{job_id}/files/input.txt", content=b"a",
        headers={**alice["headers"], "Content-Range": "bytes " + "9" * 5000 + "-0/1"},
    )
    assert response.status_code == 400
    assert not (client.app.state.jobs.directory(job_id, True) / "input.txt").exists()
