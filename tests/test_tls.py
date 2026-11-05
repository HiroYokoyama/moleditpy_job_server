import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from ipaddress import ip_network
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

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


def synthetic_ca(path_length=0, permitted=None, critical=True):
    now = datetime.now(timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic CA")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=path_length), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
    )
    if permitted is not None:
        builder = builder.add_extension(
            x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None),
            critical=critical,
        )
    certificate = builder.sign(key, hashes.SHA256())
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        certificate.fingerprint(hashes.SHA256()).hex(),
    )


LOCAL = [x509.IPAddress(ip_network("192.168.0.0/16")), x509.DNSName("localhost")]


@pytest.mark.parametrize(
    "arguments",
    [
        {"permitted": None},
        {"permitted": [x509.DNSName("example.org")]},
        {"permitted": [x509.IPAddress(ip_network("8.8.8.0/24"))]},
        {"permitted": [*LOCAL, x509.DNSName("example.org")]},
        {"permitted": LOCAL, "critical": False},
        {"permitted": LOCAL, "path_length": None},
        {"permitted": LOCAL, "path_length": 1},
    ],
)
def test_matching_fingerprint_cannot_install_a_ca_able_to_issue_beyond_local(arguments):
    pem, fingerprint = synthetic_ca(**arguments)
    with pytest.raises(ValueError, match="CA must"):
        validate_ca(pem, fingerprint)


def test_windows_trust_refuses_an_unconstrained_ca_before_opening_the_store(monkeypatch):
    pem, fingerprint = synthetic_ca(permitted=None)
    library = Mock()
    monkeypatch.setattr("job_server.trust.os.name", "nt")
    monkeypatch.setattr("job_server.trust.ctypes.WinDLL", library, raising=False)
    with pytest.raises(ValueError, match="CA must"):
        trust_windows_user_ca(pem, fingerprint)
    library.assert_not_called()


def test_server_refuses_to_issue_from_an_existing_unconstrained_ca(tmp_path):
    settings = configured(tmp_path)
    ensure_certificates(settings)
    pem, _ = synthetic_ca(permitted=None)
    ca_path(settings).write_bytes(pem)
    with pytest.raises(ValueError, match="CA must"):
        ensure_certificates(settings)


def test_replaced_ca_reissues_a_leaf_left_over_from_the_previous_ca(tmp_path):
    settings = configured(tmp_path)
    first = ensure_certificates(settings)
    ca_path(settings).unlink()
    (settings.root / "tls/ca-key.bin").unlink()
    second = ensure_certificates(settings)
    leaf = x509.load_pem_x509_certificate(Path(second["certfile"]).read_bytes())
    ca = x509.load_pem_x509_certificate(ca_path(settings).read_bytes())
    leaf.verify_directly_issued_by(ca)
    assert second["password"] != first["password"]


def test_https_port_must_differ_from_the_loopback_http_port(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path),
        lan_tls=True,
        lan_bind_address="192.168.1.20",
        port=8767,
        tls_port=8767,
    )
    with pytest.raises(ValueError, match="different ports"):
        settings.validate()


def test_https_loopback_origins_are_allowed_only_when_local_https_is_enabled(tmp_path):
    from fastapi.testclient import TestClient
    from job_server.api import create_app

    def status(settings, origin):
        return (
            TestClient(create_app(settings, dispatch=False))
            .get("/api/v1/health", headers={"Origin": origin})
            .status_code
        )

    enabled = configured(tmp_path / "enabled")
    assert status(enabled, f"https://127.0.0.1:{enabled.tls_port}") == 200
    assert status(enabled, f"https://localhost:{enabled.tls_port}") == 200
    assert status(enabled, "https://127.0.0.1:1") == 403
    disabled = Settings(data_dir=str(tmp_path / "disabled"))
    assert status(disabled, f"https://127.0.0.1:{disabled.tls_port}") == 403


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
