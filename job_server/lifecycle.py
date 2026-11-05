"""Bounded lifecycle waits for the local administration client."""

import time
from http.client import HTTPException

from .daemon_ownership import verify_local_daemon
from .instance import DaemonAlreadyRunning, InstanceLock


def directory_is_idle(root):
    probe = InstanceLock(root)
    try:
        probe.acquire()
    except DaemonAlreadyRunning:
        return False
    probe.release()
    return True


def wait_until_ready(client, root, process, timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError("Server exited during startup. Open File > Open Logs > startup.log.")
        try:
            client.request("GET", "/health", authenticated=False)
        except (OSError, HTTPException, ValueError):
            pass
        else:
            from urllib.parse import urlsplit

            verify_local_daemon(root, urlsplit(client.url).port)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Server did not become ready. Open File > Open Logs > startup.log.")
        time.sleep(0.15)


def stop_and_wait(client, root, timeout=20):
    from urllib.parse import urlsplit

    if directory_is_idle(root):
        return
    verify_local_daemon(root, urlsplit(client.url).port)
    network_error = None
    try:
        client.request("POST", "/admin/shutdown")
    except (OSError, HTTPException) as exc:
        # The process may close the connection after accepting shutdown.
        network_error = exc
    deadline = time.monotonic() + timeout
    while True:
        if directory_is_idle(root):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Server shutdown was not confirmed. Open File > Open Logs before retrying."
            ) from network_error
        time.sleep(0.15)
