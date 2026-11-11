"""Bounded lifecycle waits for the local administration client."""

import time
from http.client import HTTPException

import psutil

from .daemon_ownership import daemon_processes, verify_local_daemon
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
        if process is not None and process.poll() is not None:
            raise RuntimeError("Server exited during startup. Open File > Open Logs > startup.log.")
        try:
            health = client.request("GET", "/health", authenticated=False)
        except (OSError, HTTPException, ValueError):
            pass
        else:
            from urllib.parse import urlsplit

            if health.get("status") == "ok":
                verify_local_daemon(root, urlsplit(client.url).port)
                return
        if time.monotonic() >= deadline:
            raise TimeoutError("Server did not become ready. Open File > Open Logs > startup.log.")
        time.sleep(0.15)


def _wait_until_idle(root, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if directory_is_idle(root):
            return True
        time.sleep(0.15)
    return directory_is_idle(root)


def stop_processes_and_wait(root, timeout=20):
    """Stop this directory's daemon by process when it cannot be verified.

    The fallback for a daemon verify_local_daemon rejects — an older
    daemon without an owner descriptor, or one whose port changed while
    it ran. The authenticated shutdown in stop_and_wait is unreachable
    then, and requiring this window's device registration first would
    make such a daemon unstoppable from the window (registration itself
    verifies ownership). Terminating the same-account process needs no
    credentials; job supervisors are separate processes and are
    preserved. Call only after the user confirms in the window.
    """
    processes = daemon_processes(root)
    if not processes:
        raise RuntimeError(
            "No Job Server process for this data directory was found. "
            "Stop the old daemon from the tool that started it."
        )
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    if _wait_until_idle(root, timeout):
        return
    for process in processes:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    if not _wait_until_idle(root, 5):
        raise TimeoutError(
            "Server process stop was not confirmed. Open File > Open Logs before retrying."
        )


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
