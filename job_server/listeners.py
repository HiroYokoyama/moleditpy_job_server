"""Explicit private-interface listeners, in addition to local administration."""

import os
import socket
from contextlib import ExitStack, contextmanager
from ipaddress import ip_address

from .connection import validate_server_url


def validate_lan_address(address):
    if not isinstance(address, str) or not address or "%" in address:
        raise ValueError("LAN listen address must be a private IPv4 or IPv6 address")
    try:
        parsed = ip_address(address)
    except ValueError:
        raise ValueError("LAN listen address must be a private IPv4 or IPv6 address") from None
    if parsed.is_loopback:
        raise ValueError("Use Local only instead of a loopback LAN address")
    host = f"[{parsed}]" if parsed.version == 6 else str(parsed)
    validate_server_url(f"http://{host}", allow_lan_http=True)
    return str(parsed)


def available_lan_addresses():
    import psutil

    addresses = []
    for interface in psutil.net_if_addrs().values():
        for entry in interface:
            if entry.family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            try:
                address = validate_lan_address(entry.address)
            except ValueError:
                continue
            if address not in addresses:
                addresses.append(address)
    return addresses


@contextmanager
def listener_sockets(settings):
    settings.validate()
    addresses = ["127.0.0.1"]
    if settings.lan_bind_address:
        addresses.append(settings.lan_bind_address)
    with ExitStack() as stack:
        streams = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            stream = stack.enter_context(socket.socket(family, socket.SOCK_STREAM))
            if os.name == "nt":
                stream.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                stream.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                stream.bind((address, settings.port))
            except OSError as exc:
                raise RuntimeError(
                    f"Cannot listen on {address}:{settings.port}. "
                    "Select an address assigned to this PC and an unused port."
                ) from exc
            streams.append(stream)
        yield streams
