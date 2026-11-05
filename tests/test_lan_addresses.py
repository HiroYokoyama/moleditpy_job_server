import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from job_server.connection import is_mdns_name
from job_server.listeners import listener_sockets, tls_listener_sockets
from job_server.settings import Settings
from job_server.tls import _new_ca, ca_path, ensure_certificates, needs_new_ca, retire_ca
from job_server.trust import ca_permits_mdns, validate_ca


def https(tmp_path, **values):
    return Settings(
        data_dir=str(tmp_path), lan_tls=True, lan_bind_address="192.168.1.20", **values
    ).validate()


def san(material):
    leaf = x509.load_pem_x509_certificate(Path(material["certfile"]).read_bytes())
    return leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value


def test_every_address_gets_a_url_and_the_main_one_comes_first(tmp_path):
    settings = https(
        tmp_path,
        allow_lan_http=True,
        lan_extra_addresses=["10.0.0.5", "fd12:0000::2", "192.168.1.20", "10.0.0.5"],
        lan_hostname="Your-PC.local",
    )
    assert settings.lan_addresses == ["192.168.1.20", "10.0.0.5", "fd12::2"]
    assert settings.lan_hostname == "your-pc.local"
    assert settings.lan_urls == [
        "https://192.168.1.20:8767",
        "http://192.168.1.20:8766",
        "https://10.0.0.5:8767",
        "http://10.0.0.5:8766",
        "https://[fd12::2]:8767",
        "http://[fd12::2]:8766",
        "https://your-pc.local:8767",
        "http://your-pc.local:8766",
    ]
    assert settings.tls_url == "https://192.168.1.20:8767"
    assert set(settings.lan_urls) <= set(settings.connection_urls)


@pytest.mark.parametrize(
    "values, message",
    [
        ({"lan_extra_addresses": "10.0.0.5"}, "list"),
        ({"lan_extra_addresses": ["8.8.8.8"]}, "private"),
        ({"lan_extra_addresses": ["127.0.0.1"]}, "loopback"),
        ({"lan_extra_addresses": ["your-pc.local"]}, "private"),
        ({"lan_hostname": "your-pc"}, ".local"),
        ({"lan_hostname": "a.b.local"}, ".local"),
        ({"lan_hostname": "example.com"}, ".local"),
        ({"lan_hostname": 5}, ".local"),
    ],
)
def test_bad_addresses_and_names_are_refused(tmp_path, values, message):
    with pytest.raises(ValueError, match=message):
        https(tmp_path, **values)


def test_extra_addresses_need_a_main_one():
    with pytest.raises(ValueError, match="main LAN address"):
        Settings(allow_lan_http=True, lan_extra_addresses=["10.0.0.5"]).validate()


def test_a_local_name_needs_an_address_to_lead_to():
    with pytest.raises(ValueError, match="LAN address"):
        Settings(allow_lan_http=True, lan_hostname="your-pc.local").validate()


def test_a_local_name_over_http_only_uses_no_ca(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path),
        allow_lan_http=True,
        lan_bind_address="192.168.1.20",
        lan_hostname="your-pc.local",
    ).validate()
    assert settings.lan_urls == ["http://192.168.1.20:8766", "http://your-pc.local:8766"]
    assert not needs_new_ca(settings)


def test_earlier_settings_files_load_unchanged(tmp_path):
    config = tmp_path / "settings.json"
    config.write_text('{"allow_lan_http": true, "lan_bind_address": "192.168.1.20"}')
    settings = Settings.load(config)
    assert settings.lan_addresses == ["192.168.1.20"]
    assert settings.lan_hostname == ""


@pytest.mark.parametrize(
    "name, valid",
    [
        ("your-pc.local", True),
        ("a.local", True),
        ("-bad.local", False),
        ("bad-.local", False),
        ("a.b.local", False),
        ("local", False),
        (".local", False),
        ("your_pc.local", False),
        ("your-pc.local.", False),
        ("your-pc.example.com", False),
        (None, False),
    ],
)
def test_mdns_names(name, valid):
    assert is_mdns_name(name) is valid


def test_listeners_open_on_every_address_for_each_scheme(tmp_path, monkeypatch):
    bound = []

    class Recorder:
        def __init__(self, addresses, port):
            bound.append((addresses, port))

        def __enter__(self):
            return []

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("job_server.listeners.bound_sockets", Recorder)
    settings = https(tmp_path, allow_lan_http=True, lan_extra_addresses=["10.0.0.5"])
    with listener_sockets(settings), tls_listener_sockets(settings):
        pass
    addresses = ["127.0.0.1", "192.168.1.20", "10.0.0.5"]
    assert bound == [(addresses, 8766), (addresses, 8767)]


def test_http_listener_stays_on_loopback_when_only_https_is_on(tmp_path, monkeypatch):
    bound = []

    class Recorder:
        def __init__(self, addresses, port):
            bound.append(addresses)

        def __enter__(self):
            return []

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("job_server.listeners.bound_sockets", Recorder)
    with listener_sockets(https(tmp_path, lan_extra_addresses=["10.0.0.5"])):
        pass
    assert bound == [["127.0.0.1"]]


def test_certificate_covers_every_address_and_renews_when_they_change(tmp_path):
    settings = https(tmp_path, lan_extra_addresses=["10.0.0.5"])
    first = ensure_certificates(settings)
    ips = {str(ip) for ip in san(first).get_values_for_type(x509.IPAddress)}
    assert ips == {"127.0.0.1", "::1", "192.168.1.20", "10.0.0.5"}
    leaf = Path(first["certfile"]).read_bytes()
    ca = ca_path(settings).read_bytes()
    settings.lan_extra_addresses = []
    second = ensure_certificates(settings)
    assert Path(second["certfile"]).read_bytes() != leaf
    assert ca_path(settings).read_bytes() == ca


def test_new_ca_permits_only_the_chosen_local_name(tmp_path):
    plain = https(tmp_path / "plain")
    ensure_certificates(plain)
    assert not ca_permits_mdns(validate_ca(ca_path(plain).read_bytes()), "your-pc.local")
    named = https(tmp_path / "named", lan_hostname="your-pc.local")
    material = ensure_certificates(named)
    ca = validate_ca(ca_path(named).read_bytes())
    assert ca_permits_mdns(ca, "your-pc.local")
    assert not ca_permits_mdns(ca, "other-pc.local")
    assert not ca_permits_mdns(ca, "local")
    assert set(san(material).get_values_for_type(x509.DNSName)) == {"localhost", "your-pc.local"}


def test_existing_ca_is_never_replaced_without_being_asked(tmp_path):
    settings = https(tmp_path)
    ensure_certificates(settings)
    ca = ca_path(settings).read_bytes()
    settings.lan_hostname = "your-pc.local"
    assert needs_new_ca(settings)
    with pytest.raises(ValueError, match="cannot issue"):
        ensure_certificates(settings)
    assert ca_path(settings).read_bytes() == ca
    retired = retire_ca(settings)
    assert (retired / "ca.pem").read_bytes() == ca
    assert not needs_new_ca(settings)
    ensure_certificates(settings)
    assert ca_path(settings).read_bytes() != ca
    assert not needs_new_ca(settings)


def test_changing_the_local_name_needs_a_new_ca(tmp_path):
    settings = https(tmp_path, lan_hostname="your-pc.local")
    ensure_certificates(settings)
    assert not needs_new_ca(settings)
    settings.lan_hostname = "other-pc.local"
    assert needs_new_ca(settings)
    with pytest.raises(ValueError, match="other-pc.local"):
        ensure_certificates(settings)


def test_clients_refuse_a_ca_for_all_local_names_or_for_two(monkeypatch):
    now = datetime.now(timezone.utc)
    for value in ("local", "example.com"):
        _, ca = _new_ca(now, value)
        with pytest.raises(ValueError, match="one .local name"):
            validate_ca(ca.public_bytes(serialization.Encoding.PEM))

    real = x509.NameConstraints

    def two_names(permitted_subtrees, excluded_subtrees):
        return real([*permitted_subtrees, x509.DNSName("other-pc.local")], excluded_subtrees)

    monkeypatch.setattr("job_server.tls.x509.NameConstraints", two_names)
    _, ca = _new_ca(now, "your-pc.local")
    monkeypatch.undo()
    with pytest.raises(ValueError, match="one .local name"):
        validate_ca(ca.public_bytes(serialization.Encoding.PEM))


def test_tls_rejects_a_certificate_the_ca_issues_for_another_local_name(tmp_path):
    now = datetime.now(timezone.utc)
    ca_key, ca = _new_ca(now, "your-pc.local")
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forged")]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("other-pc.local")]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    chain = tmp_path / "chain.pem"
    chain.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        + leaf.public_bytes(serialization.Encoding.PEM)
    )
    trusted = tmp_path / "ca.pem"
    trusted.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(chain)
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.load_verify_locations(trusted)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()

        def serve():
            connection, _ = listener.accept()
            try:
                with server_context.wrap_socket(connection, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                connection.close()

        thread = threading.Thread(target=serve)
        thread.start()
        with socket.create_connection(listener.getsockname()) as raw:
            with pytest.raises(ssl.SSLCertVerificationError, match="(?i)permitted|constraint"):
                client_context.wrap_socket(raw, server_hostname="other-pc.local")
        thread.join(5)


def test_a_client_verifies_the_local_name_through_the_constrained_ca(tmp_path):
    settings = https(tmp_path, lan_hostname="your-pc.local")
    material = ensure_certificates(settings)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(
        material["certfile"], material["keyfile"], material["password"]
    )
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.load_verify_locations(str(ca_path(settings)))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()

        def serve():
            connection, _ = listener.accept()
            try:
                with server_context.wrap_socket(connection, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                connection.close()

        for name, succeeds in (("your-pc.local", True), ("other-pc.local", False)):
            thread = threading.Thread(target=serve)
            thread.start()
            with socket.create_connection(listener.getsockname()) as raw:
                if succeeds:
                    with client_context.wrap_socket(raw, server_hostname=name) as secure:
                        assert secure.version()
                else:
                    with pytest.raises(ssl.SSLCertVerificationError):
                        client_context.wrap_socket(raw, server_hostname=name)
            thread.join(5)


def test_ca_bootstrap_accepts_a_local_name_but_not_a_public_one(monkeypatch):
    from job_server.trust import fetch_server_ca

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            raise ConnectionError("reached the server")

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr("job_server.trust.build_opener", lambda *args: Opener())
    with pytest.raises(ConnectionError):
        fetch_server_ca("https://your-pc.local:8767")
    with pytest.raises(ValueError, match="CA bootstrap requires"):
        fetch_server_ca("https://example.com")
