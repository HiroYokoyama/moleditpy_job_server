import base64
import os
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from job_server.api import create_app
from job_server.auth import signing_message
from job_server.settings import Settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def enroll(client, username="alice", role="user"):
    app = client.app
    user = app.state.auth.create_user(username, "example-password-123", role)
    session = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "example-password-123"}
    )
    assert session.status_code == 200, session.text
    key = Ed25519PrivateKey.generate()
    device_id = str(uuid.uuid4())
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    response = client.post(
        "/api/v1/auth/devices",
        json={"device_id": device_id, "public_key": base64.b64encode(public).decode()},
        headers={"Authorization": "Bearer " + session.json()["token"]},
    )
    assert response.status_code == 201, response.text
    challenge = client.post("/api/v1/auth/challenge", json={"device_id": device_id}).json()
    signature = key.sign(signing_message(challenge["server_id"], device_id, challenge["nonce"]))
    response = client.post(
        "/api/v1/auth/token",
        json={
            "device_id": device_id,
            "nonce": challenge["nonce"],
            "signature": base64.b64encode(signature).decode(),
        },
    )
    assert response.status_code == 200, response.text
    return {
        "headers": {"Authorization": "Bearer " + response.json()["token"]},
        "key": key,
        "device_id": device_id,
        "user": user,
        "enrollment": {"Authorization": "Bearer " + session.json()["token"]},
    }


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=str(tmp_path), cpus=2, memory_mb=1024)
    with TestClient(create_app(settings, dispatch=False)) as client:
        yield client


@pytest.fixture
def alice(client):
    return enroll(client)


def reserve(client, identity, command="echo hello", files=None, resources=None, key=None, **extra):
    payload = {"name": "example", "command": command, "files": files or [], **extra}
    if resources:
        payload["resources"] = resources
    return client.post(
        "/api/v1/jobs",
        json=payload,
        headers={**identity["headers"], "Idempotency-Key": key or str(uuid.uuid4())},
    )
