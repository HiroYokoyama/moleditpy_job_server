import base64
import os
import stat
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from job_server.credentials import load_key, save_key
from job_server.errors import ServerError
from job_server.files import atomic_write, contained, open_regular
from job_server.runtime import DEPENDENCY_FLOORS, ensure_secure_dependencies
from .conftest import reserve


def new_device():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {"device_id": str(uuid.uuid4()), "public_key": base64.b64encode(public).decode()}


def test_enrollment_session_cannot_register_second_key(client, alice):
    response = client.post("/api/v1/auth/devices", json=new_device(), headers=alice["enrollment"])
    assert response.status_code == 401


def test_old_access_token_cannot_register_persistent_key(client, alice):
    with client.app.state.db.transaction() as connection:
        connection.execute("UPDATE sessions SET authenticated_at=?", (time.time() - 301,))
    response = client.post("/api/v1/me/devices", json=new_device(), headers=alice["headers"])
    assert response.status_code == 401
    assert response.json()["code"] == "reauthenticate"


def test_fresh_device_can_add_key_and_revocation_covers_it(client, alice):
    device = new_device()
    assert (
        client.post("/api/v1/me/devices", json=device, headers=alice["headers"]).status_code == 201
    )
    assert (
        client.post(
            f"/api/v1/me/devices/{device['device_id']}/revoke", headers=alice["headers"]
        ).status_code
        == 200
    )
    assert (
        client.post("/api/v1/auth/challenge", json={"device_id": device["device_id"]}).status_code
        == 401
    )


def test_expired_challenge_is_rejected_and_consumed(client, alice):
    challenge = client.post("/api/v1/auth/challenge", json={"device_id": alice["device_id"]}).json()
    with client.app.state.db.transaction() as connection:
        connection.execute("UPDATE challenges SET expires=?", (time.time() - 1,))
    response = client.post(
        "/api/v1/auth/token",
        json={"device_id": alice["device_id"], "nonce": challenge["nonce"], "signature": "bad"},
    )
    assert response.status_code == 401
    assert client.app.state.db.rows("SELECT * FROM challenges") == []


@pytest.mark.parametrize("route", ["users", "devices", "jobs", "events"])
def test_non_admin_cannot_read_admin_routes(client, alice, route):
    assert client.get("/api/v1/admin/" + route, headers=alice["headers"]).status_code == 403


def test_private_replacement_ignores_fixed_temp_file(tmp_path):
    victim = tmp_path / "settings.tmp"
    victim.write_text("untouched")
    target = tmp_path / "settings.json"
    atomic_write(target, b"private data")
    assert victim.read_text() == "untouched"
    assert target.read_bytes() == b"private data"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_credential_save_does_not_truncate_fixed_temp_path(tmp_path):
    path = tmp_path / "device.key"
    old_temp = path.with_suffix(".tmp")
    old_temp.write_text("untouched")
    key = Ed25519PrivateKey.generate()
    save_key(path, "device", "server", key)
    assert old_temp.read_text() == "untouched"
    assert load_key(path)["server_id"] == "server"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO/symlink behavior")
def test_special_file_rejected_without_blocking(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    # A subprocess deadline proves a missing writer cannot block the HTTP worker.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from job_server.files import open_regular; from job_server.errors import ServerError; "
            "\ntry: open_regular(__import__('sys').argv[1])"
            "\nexcept ServerError: raise SystemExit(0)"
            "\nraise SystemExit(1)",
            str(fifo),
        ],
        timeout=5,
    )
    assert result.returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks do not require admin rights")
def test_linked_credentials_and_partial_files_rejected(tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"do not truncate")
    link = tmp_path / "linked"
    link.symlink_to(victim)
    with pytest.raises(ServerError):
        open_regular(link, "r+b")
    assert victim.read_bytes() == b"do not truncate"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction compatibility")
def test_junction_root_rejected_even_on_python_311(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(victim)], capture_output=True, check=True
    )
    assert result.returncode == 0
    try:
        with pytest.raises(ServerError):
            contained(junction, "file")
    finally:
        os.rmdir(junction)


def test_runtime_rejects_known_old_dependencies(monkeypatch):
    monkeypatch.setattr("importlib.metadata.version", lambda package: "0.0.0")
    with pytest.raises(RuntimeError, match="Update this daemon"):
        ensure_secure_dependencies()


def test_runtime_dependency_floors_match_declared_requirements():
    from packaging.requirements import Requirement

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]
    requirements = {r.name: r for r in map(Requirement, project["dependencies"])}
    for name, version in DEPENDENCY_FLOORS.items():
        assert any(
            s.operator == ">=" and s.version == version for s in requirements[name].specifier
        )


def test_errors_do_not_echo_passwords(client):
    secret = "synthetic-password-to-hide"
    response = client.post("/api/v1/auth/login", json={"username": "missing", "password": secret})
    assert response.status_code == 401
    assert secret not in response.text


def test_output_manifest_excludes_special_files(client, alice):
    if os.name == "nt":
        pytest.skip("POSIX FIFO")
    job_id = reserve(client, alice).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
    work = client.app.state.jobs.directory(job_id)
    os.mkfifo(work / "result")
    assert client.get(f"/api/v1/jobs/{job_id}/files", headers=alice["headers"]).json() == []
