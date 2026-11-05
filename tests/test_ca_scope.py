"""Offline checks of what a trusted local CA can vouch for; no trust store is touched."""

import ssl
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from job_server.tls import _new_ca
from job_server.trust import fetch_server_ca, trust_windows_user_ca, validate_ca

from .test_tls import synthetic_ca

INCOMPLETE = [
    pytest.param([x509.IPAddress(ip_network("10.0.0.0/8"))], id="ip-only"),
    pytest.param([x509.DNSName("localhost")], id="dns-only"),
    pytest.param([x509.DNSName("your-pc.local")], id="local-name-only"),
]


@pytest.mark.parametrize("permitted", INCOMPLETE)
def test_ca_missing_a_name_type_is_refused_despite_its_fingerprint(permitted):
    pem, fingerprint = synthetic_ca(permitted=permitted)
    with pytest.raises(ValueError, match="must be limited"):
        validate_ca(pem, fingerprint)


@pytest.mark.parametrize(
    "permitted",
    [
        [x509.IPAddress(ip_network("192.168.0.0/16")), x509.DNSName("localhost")],
        [x509.IPAddress(ip_network("10.0.0.0/8")), x509.DNSName("your-pc.local")],
        [x509.IPAddress(ip_network("fc00::/7")), x509.DNSName("localhost")],
    ],
)
def test_narrower_ca_with_both_name_types_is_accepted(permitted):
    pem, fingerprint = synthetic_ca(permitted=permitted)
    assert validate_ca(pem, fingerprint)


def test_generated_ca_is_accepted():
    now = datetime.now(timezone.utc)
    for hostname in ("", "your-pc.local"):
        _, ca = _new_ca(now, hostname)
        assert validate_ca(ca.public_bytes(serialization.Encoding.PEM))


@pytest.mark.parametrize("permitted", INCOMPLETE)
def test_refusal_comes_before_the_windows_store_opens(monkeypatch, permitted):
    pem, fingerprint = synthetic_ca(permitted=permitted)
    opened = []
    monkeypatch.setattr("job_server.trust.os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        "job_server.trust.ctypes.WinDLL", lambda *args, **kwargs: opened.append(args), raising=False
    )
    with pytest.raises(ValueError, match="must be limited"):
        trust_windows_user_ca(pem, fingerprint)
    assert opened == []


@pytest.mark.parametrize("permitted", INCOMPLETE)
def test_refused_ca_is_not_returned_for_confirmation(monkeypatch, permitted):
    pem, fingerprint = synthetic_ca(permitted=permitted)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return pem

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr("job_server.trust.build_opener", lambda *args: Opener())
    for expected in (None, fingerprint):
        with pytest.raises(ValueError, match="must be limited"):
            fetch_server_ca("https://192.168.1.20:8767", expected)


def handshake(ca_key, ca, tmp_path, name, hostname):
    """A verified TLS handshake in memory, for a leaf the CA signed for `name`."""
    now = datetime.now(timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([name]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    chain = tmp_path / "leaf.pem"
    chain.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        + leaf.public_bytes(serialization.Encoding.PEM)
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(chain)
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.load_verify_locations(
        cadata=ca.public_bytes(serialization.Encoding.PEM).decode("ascii")
    )
    client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    finished = set()
    for _ in range(20):
        for label, peer in (("client", client), ("server", server)):
            if label in finished:
                continue
            try:
                peer.do_handshake()
                finished.add(label)
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLCertVerificationError:
                raise
            except ssl.SSLError:
                if label == "client":
                    raise
        if client_out.pending:
            server_in.write(client_out.read())
        if server_out.pending:
            client_in.write(server_out.read())
        if len(finished) == 2:
            return
    raise AssertionError("handshake did not finish")


LOCAL_NAMES = [
    (x509.DNSName("localhost"), "localhost"),
    (x509.DNSName("your-pc.local"), "your-pc.local"),
    (x509.IPAddress(ip_address("192.168.1.20")), "192.168.1.20"),
    (x509.IPAddress(ip_address("fd12::2")), "fd12::2"),
]
OUT_OF_SCOPE = [
    (x509.DNSName("example.com"), "example.com"),
    (x509.DNSName("other-pc.local"), "other-pc.local"),
    (x509.IPAddress(ip_address("8.8.8.8")), "8.8.8.8"),
    (x509.IPAddress(ip_address("2001:4860:4860::8888")), "2001:4860:4860::8888"),
]


@pytest.mark.parametrize("name, hostname", LOCAL_NAMES)
def test_generated_ca_verifies_for_local_names(tmp_path, name, hostname):
    ca_key, ca = _new_ca(datetime.now(timezone.utc), "your-pc.local")
    handshake(ca_key, ca, tmp_path, name, hostname)


@pytest.mark.parametrize("name, hostname", OUT_OF_SCOPE)
def test_generated_ca_cannot_vouch_for_other_names(tmp_path, name, hostname):
    ca_key, ca = _new_ca(datetime.now(timezone.utc), "your-pc.local")
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(ca_key, ca, tmp_path, name, hostname)
