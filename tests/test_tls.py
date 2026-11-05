import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography import x509

from job_server.settings import Settings
from job_server.tls import ca_fingerprint, ca_path, ensure_certificates
from job_server.trust import fetch_server_ca, validate_ca, trust_windows_user_ca


def configured(tmp_path):
    return Settings(data_dir=str(tmp_path), lan_tls=True, lan_bind_address="192.168.1.20")


def test_ca_is_retained_when_leaf_is_reused_or_ip_changes(tmp_path):
    settings = configured(tmp_path)
    first = ensure_certificates(settings)
    ca = ca_path(settings).read_bytes()
    fingerprint = ca_fingerprint(settings)
    leaf = Path(first["certfile"]).read_bytes()
    assert ensure_certificates(settings) == first
    assert Path(first["certfile"]).read_bytes() == leaf
    settings.lan_bind_address = "192.168.1.21"
    second = ensure_certificates(settings)
    assert ca_path(settings).read_bytes() == ca
    assert ca_fingerprint(settings) == fingerprint
    assert Path(second["certfile"]).read_bytes() != leaf
    assert b"ENCRYPTED PRIVATE KEY" in Path(second["keyfile"]).read_bytes()
    certificate = validate_ca(ca, fingerprint)
    assert certificate.extensions.get_extension_for_class(x509.NameConstraints).critical
    if os.name == "nt":
        assert (settings.root / "tls/ca-key.bin").read_bytes().startswith(b"DPAPI\n")
        assert (settings.root / "tls/server-key-password.bin").read_bytes().startswith(b"DPAPI\n")
    else:
        assert not ((settings.root / "tls/ca-key.bin").stat().st_mode & 0o077)


def test_incomplete_ca_does_not_silently_rotate_trust(tmp_path):
    settings = configured(tmp_path)
    ensure_certificates(settings)
    ca_path(settings).unlink()
    with pytest.raises(ValueError, match="incomplete"):
        ensure_certificates(settings)


def test_wrong_fingerprint_is_rejected_before_trust(tmp_path):
    settings = configured(tmp_path)
    ensure_certificates(settings)
    with pytest.raises(ValueError, match="fingerprint differs"):
        validate_ca(ca_path(settings).read_bytes(), "00" * 32)


@pytest.mark.parametrize(
    "url", ["http://192.168.1.20:8767", "https://8.8.8.8", "https://host.local"]
)
def test_ca_bootstrap_rejects_nonlocal_or_nonhttps_urls(url):
    with pytest.raises(ValueError):
        fetch_server_ca(url, "00" * 32)


@pytest.mark.skipif(os.name != "nt", reason="Windows certificate store API")
def test_windows_trust_uses_current_user_root_without_modifying_real_store(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    ensure_certificates(settings)
    pem = ca_path(settings).read_bytes()
    crypt = Mock()
    crypt.CertOpenSystemStoreW.return_value = 123
    crypt.CertAddEncodedCertificateToStore.return_value = True
    monkeypatch.setattr("job_server.trust.ctypes.WinDLL", lambda *args, **kwargs: crypt)
    trust_windows_user_ca(pem, ca_fingerprint(settings))
    crypt.CertOpenSystemStoreW.assert_called_once_with(None, "ROOT")
    assert crypt.CertAddEncodedCertificateToStore.call_args.args[0] == 123
    assert crypt.CertAddEncodedCertificateToStore.call_args.args[1] == 1
    crypt.CertCloseStore.assert_called_once_with(123, 0)


def test_native_https_bootstrap_and_verified_authentication(tmp_path):
    from job_server.auth import Auth
    from job_server.client import JobServerClient
    from job_server.db import Database
    from job_server.lifecycle import stop_and_wait, wait_until_ready
    from job_server.listeners import available_lan_addresses
    from job_server.runtime import ensure_secure_dependencies

    try:
        ensure_secure_dependencies()
    except RuntimeError:
        pytest.skip("Real daemon requires security dependency floors")
    addresses = [address for address in available_lan_addresses() if ":" not in address]
    if not addresses:
        pytest.skip("No private IPv4 interface")
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        port, tls_port = first.getsockname()[1], second.getsockname()[1]
    settings = Settings(
        data_dir=str(tmp_path),
        port=port,
        tls_port=tls_port,
        allow_privileged=True,
        lan_tls=True,
        lan_bind_address=addresses[0],
    )
    config = tmp_path / "settings.json"
    settings.save(config)
    db = Database(settings.root)
    try:
        Auth(db, settings).create_initial_admin("fixture-admin", "synthetic-password")
    finally:
        db.close()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    with (tmp_path / "startup.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "job_server", "serve", "--config", str(config)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    try:
        local = f"http://127.0.0.1:{port}"
        wait_until_ready(JobServerClient(local, timeout=1), settings.root, process)
        fingerprint = ca_fingerprint(settings)
        received = fetch_server_ca(settings.tls_url, fingerprint)
        assert received == ca_path(settings).read_bytes()
        downloaded = tmp_path / "downloaded-ca.pem"
        downloaded.write_bytes(received)
        client = JobServerClient(settings.tls_url, tmp_path / "fixture.key", ca_file=downloaded)
        client.enroll("fixture-admin", "synthetic-password")
        assert client.request("GET", "/me")["role"] == "admin"
        assert client.request("GET", "/jobs") == []
        # The default public-root context must not accept this unregistered local CA.
        with pytest.raises(OSError):
            JobServerClient(settings.tls_url, timeout=2).request(
                "GET", "/health", authenticated=False
            )
        local_admin = JobServerClient(local, tmp_path / "fixture.key", timeout=3)
        stop_and_wait(local_admin, settings.root)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
