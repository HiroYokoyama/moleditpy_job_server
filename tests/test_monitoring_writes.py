"""Monitoring should read unchanged state, without renewing credentials each poll."""

import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from job_server import client as client_module
from job_server.client import ClientError, JobServerClient
from job_server.credentials import save_key
from job_server.server_backend import JobServerBackend


@pytest.fixture(autouse=True)
def clear_auth_cache():
    with client_module._AUTH_LOCK:
        client_module._AUTH_STATES.clear()
    yield
    with client_module._AUTH_LOCK:
        client_module._AUTH_STATES.clear()


def credential(tmp_path, name="device.key"):
    path = tmp_path / name
    save_key(path, "synthetic-device", "synthetic-server", Ed25519PrivateKey.generate())
    return path


def factory(path, **kwargs):
    return JobServerClient("http://127.0.0.1:8766", path, reuse_auth=True, **kwargs)


def fake_auth(monkeypatch):
    calls = []

    def authenticate(self, challenge=None, credential=None):
        calls.append(self.url)
        self.token = f"synthetic-token-{len(calls)}"
        self.expires = time.time() + 900
        self.authenticated_at = time.time()
        self.server_id = credential["server_id"]

    monkeypatch.setattr(JobServerClient, "_authenticate", authenticate)
    return calls


def test_disposable_clients_reuse_tokens_but_not_openers(tmp_path, monkeypatch):
    path = credential(tmp_path)
    calls = fake_auth(monkeypatch)
    clients = [factory(path) for _ in range(5)]
    for client in clients:
        client._ensure_auth("/jobs")
    assert len(calls) == 1
    assert len({id(client.opener) for client in clients}) == len(clients)
    assert {client.token for client in clients} == {"synthetic-token-1"}


def test_concurrent_clients_authenticate_once(tmp_path, monkeypatch):
    path = credential(tmp_path)
    calls = fake_auth(monkeypatch)
    clients = [factory(path) for _ in range(8)]
    barrier = threading.Barrier(len(clients))

    def poll(client):
        barrier.wait(timeout=10)
        client._ensure_auth("/jobs")
        return client.token

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert set(pool.map(poll, clients)) == {"synthetic-token-1"}
    assert len(calls) == 1


def test_slow_host_does_not_block_other_identity(tmp_path, monkeypatch):
    path = credential(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = fake_auth(monkeypatch)
    authenticate = JobServerClient._authenticate

    def slow(self, *args, **kwargs):
        if self.url.endswith(":8766"):
            entered.set()
            assert release.wait(10)
        return authenticate(self, *args, **kwargs)

    monkeypatch.setattr(JobServerClient, "_authenticate", slow)
    first = factory(path)
    second = JobServerClient("http://127.0.0.1:8767", path, reuse_auth=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow_future = pool.submit(first._ensure_auth, "/jobs")
        try:
            assert entered.wait(10)
            pool.submit(second._ensure_auth, "/jobs").result(timeout=5)
        finally:
            release.set()
        slow_future.result(timeout=5)
    assert len(original) == 2


def test_expiration_admin_freshness_and_bounded_cache(tmp_path, monkeypatch):
    path = credential(tmp_path)
    calls = fake_auth(monkeypatch)
    first = factory(path)
    first._ensure_auth("/jobs")
    key = first._active_auth_key
    token, expires, authenticated_at, server_id = client_module._AUTH_STATES[key]
    client_module._AUTH_STATES[key] = token, expires, authenticated_at - 241, server_id
    factory(path)._ensure_auth("/jobs")
    assert len(calls) == 1
    factory(path)._ensure_auth("/admin/jobs")
    assert len(calls) == 2
    client_module._AUTH_STATES[key] = token, time.time() + 5, authenticated_at, server_id
    factory(path)._ensure_auth("/jobs")
    assert len(calls) == 3
    monkeypatch.setattr(client_module, "_MAX_AUTH_STATES", 2)
    for port in (8767, 8768, 8769):
        JobServerClient(f"http://127.0.0.1:{port}", path, reuse_auth=True)._ensure_auth("/jobs")
    assert len(client_module._AUTH_STATES) == 2


def test_credential_replacement_deletion_and_server_scope(tmp_path, monkeypatch):
    path = credential(tmp_path)
    calls = fake_auth(monkeypatch)
    client = factory(path)
    client._ensure_auth("/jobs")
    save_key(path, "synthetic-device", "synthetic-server", Ed25519PrivateKey.generate())
    client._ensure_auth("/jobs")
    factory(path, server_id="synthetic-server")._ensure_auth("/jobs")
    assert len(calls) == 3
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        client._ensure_auth("/jobs")


def test_trust_scope_and_changes_do_not_reuse_token(tmp_path, monkeypatch):
    path = credential(tmp_path)
    calls = fake_auth(monkeypatch)
    first, second = factory(path), factory(path)
    first._ensure_auth("/jobs")
    second._trust_identity = (*second._trust_identity, "different trust store")
    second._ensure_auth("/jobs")
    assert len(calls) == 2


def test_ca_file_replacement_rebuilds_transport_and_invalidates_auth(tmp_path, monkeypatch):
    path = credential(tmp_path)
    calls = fake_auth(monkeypatch)
    ca_file = tmp_path / "synthetic-ca.pem"
    ca_file.write_bytes(b"synthetic trust revision one")
    create_context = client_module.ssl.create_default_context
    contexts = []

    def context(**kwargs):
        contexts.append(kwargs["cafile"])
        return create_context()

    monkeypatch.setattr(client_module.ssl, "create_default_context", context)
    first = factory(path, ca_file=ca_file)
    first._ensure_auth("/jobs")
    opener = first.opener
    ca_file.write_bytes(b"synthetic trust revision two")
    first._ensure_auth("/jobs")
    assert first.opener is not opener
    assert len(contexts) == len(calls) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes; Windows credentials use DPAPI")
def test_cached_token_does_not_bypass_key_permission_check(tmp_path, monkeypatch):
    path = credential(tmp_path)
    calls = fake_auth(monkeypatch)
    first = factory(path)
    first._ensure_auth("/jobs")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="chmod 600"):
        first._ensure_auth("/jobs")
    assert len(calls) == 1


def test_rejected_token_is_invalidated_without_replaying_mutation(tmp_path, monkeypatch):
    path = credential(tmp_path)
    calls = fake_auth(monkeypatch)
    first = factory(path)
    first._ensure_auth("/jobs")
    requests = []

    def reject(request, **kwargs):
        requests.append(request)
        raise HTTPError(request.full_url, 401, "revoked", {}, io.BytesIO(b'{"message":"revoked"}'))

    first.opener.open = reject
    with pytest.raises(ClientError, match="revoked"):
        first.request("POST", "/jobs")
    assert len(requests) == 1
    assert not client_module._AUTH_STATES
    factory(path)._ensure_auth("/jobs")
    assert len(calls) == 2


def test_real_api_monitoring_does_not_write_auth_events_each_poll(
    client, alice, tmp_path, monkeypatch
):
    path = tmp_path / "api.key"
    save_key(path, alice["device_id"], client.app.state.db.metadata("server_id"), alice["key"])

    def open_request(request, **kwargs):
        response = client.request(
            request.get_method(),
            urlsplit(request.full_url).path,
            content=request.data,
            headers=dict(request.header_items()),
        )
        if response.status_code >= 400:
            raise HTTPError(
                request.full_url, response.status_code, "rejected", {}, io.BytesIO(response.content)
            )
        stream = io.BytesIO(response.content)
        stream.headers = response.headers
        return stream

    monkeypatch.setattr(
        client_module, "build_opener", lambda *args: SimpleNamespace(open=open_request)
    )
    host = SimpleNamespace(
        server_url="http://127.0.0.1:8766", server_key_path=path, server_ca_file="", server_id=""
    )
    db = client.app.state.db
    before = db.connection.total_changes
    JobServerBackend(host).client.request("GET", "/jobs")
    after_auth = db.connection.total_changes
    assert after_auth > before
    for _ in range(5):
        JobServerBackend(host).client.request("GET", "/jobs")
    assert db.connection.total_changes == after_auth
    client.app.state.auth.revoke(alice["device_id"], alice["user"]["id"])
    revoked_changes = db.connection.total_changes
    with pytest.raises(ClientError):
        JobServerBackend(host).client.request("GET", "/jobs")
    assert not client_module._AUTH_STATES
    with pytest.raises(ClientError):
        JobServerBackend(host).client.request("GET", "/jobs")
    assert db.connection.total_changes == revoked_changes
