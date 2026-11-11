"""Bind local GUI enrollment to this directory's live loopback listener."""

import json
import math
import os
from pathlib import Path

import psutil

from .files import atomic_write, open_regular

_MAX_DESCRIPTOR_BYTES = 4096


def descriptor_path(root):
    return Path(root) / "daemon-owner.json"


def write_owner_descriptor(root, port):
    """Called only after the daemon acquires its data-directory lock."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Invalid daemon port")
    owner = psutil.Process()
    descriptor = {"version": 1, "pid": os.getpid(), "created_at": owner.create_time(), "port": port}
    atomic_write(descriptor_path(root), json.dumps(descriptor).encode("utf-8"))


def daemon_processes(root):
    """This directory's daemon processes, found by OS inspection alone.

    The recovery counterpart to verify_local_daemon: when ownership cannot
    be verified (an older daemon without a descriptor, a port changed under
    a running daemon), no HTTP answer can be trusted, so match the serve
    command line of same-account processes instead and keep only the ones
    whose configuration resolves to this data directory. Nothing is sent
    anywhere; acting on a same-account process is authority the local user
    already has, the same basis as the local create-admin and
    reset-password commands.
    """
    root = Path(root).resolve()
    found = []
    account = psutil.Process().username()
    if os.name == "nt":
        account = account.casefold()
    for process in psutil.process_iter(["cmdline", "username"]):
        try:
            if process.pid == os.getpid():
                continue
            owner = process.info["username"]
            if owner is None:
                continue
            if os.name == "nt":
                owner = owner.casefold()
            if owner == account and _serves_root(
                process.info["cmdline"] or [], root, process.cwd
            ):
                found.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def _serves_root(cmdline, root, cwd=lambda: "."):
    if "serve" not in cmdline:
        return False
    joined = " ".join(cmdline)
    if "job_server" not in joined and "moleditpy-job-server" not in joined:
        return False
    config = None
    for index, token in enumerate(cmdline):
        if token == "--config" and index + 1 < len(cmdline):
            config = cmdline[index + 1]
        elif token.startswith("--config="):
            config = token.partition("=")[2]
    from .settings import Settings

    try:
        if config and not Path(config).is_absolute():
            # A relative --config was resolved in the daemon's folder, not this one.
            config = Path(cwd(), config)
        settings = Settings.load(Path(config)) if config else Settings.load()
    except Exception:
        return False
    return settings.root == root


def verify_local_daemon(root, port):
    """Fail closed before sending passwords or device credentials over loopback.

    A held directory lock alone cannot establish which process owns a port. The
    private descriptor must match a live process birth time and that same OS
    account's listening socket. No HTTP response is trusted as proof of ownership.
    """
    from .instance import DaemonAlreadyRunning, InstanceLock

    guidance = (
        "The listener is not this Job Server or its ownership cannot be verified. "
        "Stop the old daemon using its original administration tool or stop its process "
        "on this PC, then start Job Server again. If its port changed, stop the old "
        "daemon before applying the new port. No credentials were sent."
    )
    try:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid port")
        probe = InstanceLock(Path(root))
        try:
            probe.acquire()
        except DaemonAlreadyRunning:
            pass
        else:
            probe.release()
            raise ValueError("No daemon owns the data directory")
        with open_regular(descriptor_path(root)) as stream:
            raw = stream.read(_MAX_DESCRIPTOR_BYTES + 1)
        if len(raw) > _MAX_DESCRIPTOR_BYTES:
            raise ValueError("Invalid owner descriptor")
        descriptor = json.loads(raw)
        if (
            not isinstance(descriptor, dict)
            or type(descriptor.get("version")) is not int
            or descriptor["version"] != 1
            or type(descriptor.get("pid")) is not int
            or descriptor["pid"] <= 0
            or type(descriptor.get("created_at")) not in (int, float)
            or not math.isfinite(descriptor["created_at"])
            or type(descriptor.get("port")) is not int
            or descriptor["port"] != port
        ):
            raise ValueError("Owner descriptor does not match this listener")
        owner = psutil.Process(descriptor["pid"])
        if owner.create_time() != descriptor["created_at"]:
            raise ValueError("Daemon PID has been reused")
        owner_name, current_name = owner.username(), psutil.Process().username()
        if os.name == "nt":
            owner_name, current_name = owner_name.casefold(), current_name.casefold()
        if owner_name != current_name:
            raise ValueError("Daemon belongs to another OS account")
        for connection in owner.net_connections(kind="tcp"):
            if (
                connection.status == psutil.CONN_LISTEN
                and connection.laddr
                and connection.laddr.ip == "127.0.0.1"
                and connection.laddr.port == port
            ):
                return
        raise ValueError("Daemon does not own this loopback port")
    except Exception as exc:
        raise RuntimeError(guidance) from exc
