"""Persistent local CA and renewable LAN server certificates."""

import os
import secrets
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .credentials import dpapi
from .files import atomic_write, is_link, open_regular
from .instance import InstanceLock


def _read(path):
    with open_regular(path) as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Certificate material exceeds the size limit")
    return data


def _save_secret(path, data):
    atomic_write(path, b"DPAPI\n" + dpapi(data) if os.name == "nt" else data)


def _load_secret(path):
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("TLS secrets must be accessible only to their owner")
    data = _read(path)
    if os.name == "nt":
        if not data.startswith(b"DPAPI\n"):
            raise ValueError("Windows TLS secrets must use DPAPI")
        return dpapi(data[6:], decrypt=True)
    if data.startswith(b"DPAPI\n"):
        raise ValueError("This TLS secret belongs to its Windows account")
    return data


def ca_path(settings):
    return settings.root / "tls" / "ca.pem"


def ca_fingerprint(settings):
    certificate = x509.load_pem_x509_certificate(_read(ca_path(settings)))
    return certificate.fingerprint(hashes.SHA256()).hex().upper()


def _new_ca(now):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MoleditPy Job Server Local CA")])
    permitted = [x509.IPAddress(ip_network(value)) for value in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "127.0.0.0/8", "::1/128",
    )]
    permitted.append(x509.DNSName("localhost"))
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def ensure_certificates(settings):
    settings.validate()
    if not settings.lan_tls:
        raise ValueError("Enable local HTTPS before creating certificates")
    directory = settings.root / "tls"
    if is_link(directory):
        raise ValueError("The TLS directory must not be a link or junction")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = InstanceLock(directory).acquire()
    try:
        return _ensure_locked(settings, directory)
    finally:
        lock.release()


def _ensure_locked(settings, directory):
    now = datetime.now(timezone.utc)
    ca_file, ca_key_file = directory / "ca.pem", directory / "ca-key.bin"
    if ca_file.exists() != ca_key_file.exists():
        raise ValueError("The local CA is incomplete. Restore its certificate and key; do not replace trusted CA files automatically.")
    if not ca_file.exists():
        ca_key, ca = _new_ca(now)
        _save_secret(ca_key_file, ca_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
        atomic_write(ca_file, ca.public_bytes(serialization.Encoding.PEM))
    else:
        ca = x509.load_pem_x509_certificate(_read(ca_file))
        ca_key = serialization.load_pem_private_key(_load_secret(ca_key_file), password=None)
        if not isinstance(ca_key, ec.EllipticCurvePrivateKey):
            raise ValueError("Invalid local CA key")
        if ca.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != ca_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
            raise ValueError("The local CA key does not match its certificate")
        ca.verify_directly_issued_by(ca)
        if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError("The local CA certificate is not a CA")
        if not ca.not_valid_before_utc <= now < ca.not_valid_after_utc:
            raise ValueError("The local CA is expired or not yet valid. Renew trust explicitly.")

    certificate_file, key_file = directory / "server.pem", directory / "server-key.pem"
    password_file = directory / "server-key-password.bin"
    addresses = {ip_address("127.0.0.1"), ip_address("::1"), ip_address(settings.lan_bind_address)}
    renew = True
    password = None
    if all(path.exists() for path in (certificate_file, key_file, password_file)):
        try:
            leaf = x509.load_pem_x509_certificate(_read(certificate_file))
            password = _load_secret(password_file)
            key = serialization.load_pem_private_key(_read(key_file), password=password)
            leaf.verify_directly_issued_by(ca)
            names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            renew = (
                set(names.get_values_for_type(x509.IPAddress)) != addresses
                or leaf.not_valid_after_utc <= now + timedelta(days=30)
                or leaf.not_valid_before_utc > now
                or leaf.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                != key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            )
        except (ValueError, TypeError, x509.ExtensionNotFound):
            renew = True
    if renew:
        key = ec.generate_private_key(ec.SECP256R1())
        password = secrets.token_urlsafe(48).encode("ascii")
        leaf = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MoleditPy Job Server")]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(min(now + timedelta(days=365), ca.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), *[x509.IPAddress(address) for address in sorted(addresses, key=str)],
            ]), critical=False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        _save_secret(password_file, password)
        atomic_write(key_file, key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(password),
        ))
        atomic_write(certificate_file, leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    return {"certfile": str(certificate_file), "keyfile": str(key_file), "password": password.decode("ascii")}
