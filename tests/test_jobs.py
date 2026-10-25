import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from .conftest import enroll, reserve


def test_idempotency_conflict_and_parallel_retry(client, alice):
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(4) as pool:
        responses = list(pool.map(lambda _: reserve(client, alice, key=key), range(4)))
    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert reserve(client, alice, command="echo different", key=key).status_code == 409
    assert len(client.get("/api/v1/jobs", headers=alice["headers"]).json()) == 1


def test_two_phase_resumable_upload_hash_and_commit(client, alice):
    data = b"example input data"
    files = [{"path": "input.inp", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]
    job_id = reserve(client, alice, files=files).json()["id"]
    base = f"/api/v1/jobs/{job_id}"
    assert client.post(base + "/commit", headers=alice["headers"]).status_code == 409
    headers = {**alice["headers"], "Content-Range": f"bytes 0-4/{len(data)}"}
    assert (
        client.put(base + "/files/input.inp", content=data[:5], headers=headers).json()["offset"]
        == 5
    )
    assert (
        client.put(base + "/files/input.inp", content=data[:5], headers=headers).json()["offset"]
        == 5
    )
    headers["Content-Range"] = f"bytes 5-{len(data) - 1}/{len(data)}"
    assert client.put(base + "/files/input.inp", content=data[5:], headers=headers).json()[
        "complete"
    ]
    assert client.post(base + "/commit", headers=alice["headers"]).json()["state"] == "PENDING"
    assert client.post(base + "/commit", headers=alice["headers"]).json()["state"] == "PENDING"
    assert (
        client.put(base + "/files/input.inp", content=data, headers=alice["headers"]).status_code
        == 409
    )
    assert (
        client.get(
            base + "/files/input.inp", headers={**alice["headers"], "Range": "bytes=5-"}
        ).content
        == data[5:]
    )


def test_checksum_failure_resets_resume_offset(client, alice):
    files = [{"path": "input", "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}]
    job_id = reserve(client, alice, files=files).json()["id"]
    base = f"/api/v1/jobs/{job_id}"
    assert (
        client.put(base + "/files/input", content=b"bad", headers=alice["headers"]).status_code
        == 409
    )
    assert client.get(base + "/uploads", headers=alice["headers"]).json()[0]["offset"] == 0
    assert (
        client.put(base + "/files/input", content=b"abc", headers=alice["headers"]).status_code
        == 200
    )


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "C:/drive",
        "a:stream",
        "con.txt",
        "a/../b",
        "a\\b",
        "a.",
        "LPT1",
        ".moleditpy_rc",
        "job.log",
        "a//b",
    ],
)
def test_unsafe_input_paths_rejected(client, alice, path):
    response = reserve(
        client, alice, files=[{"path": path, "size": 0, "sha256": hashlib.sha256(b"").hexdigest()}]
    )
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("files", [["a", "A"], ["a", "a/b"], ["a/b", "a/b/c"], ["a", "a-0", "a/b"]])
def test_colliding_paths_rejected(client, alice, files):
    response = reserve(
        client,
        alice,
        files=[
            {"path": path, "size": 0, "sha256": hashlib.sha256(b"").hexdigest()} for path in files
        ],
    )
    assert response.status_code == 400


def test_owner_acl_every_job_route(client, alice):
    bob = enroll(client, "bob")
    job_id = reserve(client, alice).json()["id"]
    base = f"/api/v1/jobs/{job_id}"
    for path in ("", "/log", "/files", "/uploads", "/files/input"):
        assert client.get(base + path, headers=bob["headers"]).status_code == 404
    for path in ("/cancel", "/commit"):
        assert client.post(base + path, headers=bob["headers"]).status_code == 404
    assert (
        client.put(base + "/files/input", content=b"x", headers=bob["headers"]).status_code == 404
    )
    assert client.get("/api/v1/jobs", headers=bob["headers"]).json() == []
    assert client.get("/api/v1/admin/jobs", headers=bob["headers"]).status_code == 403
    assert reserve(client, bob, after_job=job_id).status_code == 404


def test_admission_rejects_unsupported_options_and_resources(client, alice):
    for extra in (
        {"executor": "wsl"},
        {"force_run": True},
        {"modules": ["example"]},
        {"resources": {"cpus_per_task": 3}},
        {"resources": {"memory_mb": 2048}},
    ):
        assert reserve(client, alice, **extra).status_code in {400, 422}
    assert reserve(client, alice, command="cat {input}").status_code == 400


def test_cancel_pending_is_idempotent(client, alice):
    job_id = reserve(client, alice).json()["id"]
    base = f"/api/v1/jobs/{job_id}"
    client.post(base + "/commit", headers=alice["headers"])
    for _ in range(2):
        assert (
            client.post(base + "/cancel", headers=alice["headers"]).json()["state"] == "CANCELLED"
        )


def test_output_links_not_served(client, alice, tmp_path):
    job_id = reserve(client, alice).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
    work = client.app.state.jobs.directory(job_id)
    outside = tmp_path / "outside"
    outside.write_text("secret")
    os.link(outside, work / "linked")
    assert (
        client.get(f"/api/v1/jobs/{job_id}/files/linked", headers=alice["headers"]).status_code
        == 400
    )
    assert client.get(f"/api/v1/jobs/{job_id}/files", headers=alice["headers"]).json() == []


def test_pause_rejects_new_jobs(client, alice):
    admin = enroll(client, "admin", "admin")
    client.post("/api/v1/admin/queue/pause", json={"paused": True}, headers=admin["headers"])
    assert reserve(client, alice).status_code == 503


@pytest.mark.parametrize("command", [" ", "\t\r\n"])
def test_blank_commands_are_rejected_before_reservation(client, alice, command):
    response = reserve(client, alice, command=command)
    assert response.status_code == 400
    assert client.app.state.db.rows("SELECT id FROM jobs") == []
