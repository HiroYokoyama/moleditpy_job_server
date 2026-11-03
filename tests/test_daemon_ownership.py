"""Local enrollment must not trust an unrelated process answering /health."""

import json
import os
import socket
from types import SimpleNamespace

import psutil
import pytest

from job_server.daemon_ownership import descriptor_path, verify_local_daemon
from job_server.instance import InstanceLock
from job_server.errors import ServerError


@pytest.fixture
def listener():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        stream.listen()
        yield stream.getsockname()[1]


def test_owner_descriptor_is_created_under_lock_and_removed_before_release(tmp_path, listener):
    lock = InstanceLock(tmp_path, port=listener).acquire()
    try:
        descriptor = json.loads(descriptor_path(tmp_path).read_text())
        assert descriptor["pid"] == os.getpid()
        assert descriptor["port"] == listener
        assert descriptor["created_at"] == psutil.Process().create_time()
        if os.name != "nt":
            assert descriptor_path(tmp_path).stat().st_mode & 0o077 == 0
        verify_local_daemon(tmp_path, listener)
    finally:
        lock.release()
    assert not descriptor_path(tmp_path).exists()
    with pytest.raises(RuntimeError, match="No credentials were sent"):
        verify_local_daemon(tmp_path, listener)


def test_held_lock_cannot_authorize_a_different_listener_port(tmp_path, listener):
    lock = InstanceLock(tmp_path, port=listener).acquire()
    try:
        with pytest.raises(RuntimeError, match="not this Job Server"):
            verify_local_daemon(tmp_path, 1 if listener != 1 else 2)
    finally:
        lock.release()


def test_older_daemon_without_owner_descriptor_requires_restart(tmp_path, listener):
    lock = InstanceLock(tmp_path).acquire()
    try:
        with pytest.raises(RuntimeError, match="start Job Server again"):
            verify_local_daemon(tmp_path, listener)
    finally:
        lock.release()


@pytest.mark.parametrize("change", [
    {"created_at": 1}, {"pid": True}, {"pid": -1}, {"port": True},
    {"created_at": float("nan")}, {"created_at": "invalid"}, {"version": 2},
])
def test_invalid_or_reused_process_descriptor_is_rejected(tmp_path, listener, change):
    lock = InstanceLock(tmp_path, port=listener).acquire()
    try:
        descriptor = json.loads(descriptor_path(tmp_path).read_text())
        descriptor.update(change)
        descriptor_path(tmp_path).write_text(json.dumps(descriptor))
        with pytest.raises(RuntimeError, match="No credentials were sent"):
            verify_local_daemon(tmp_path, listener)
    finally:
        lock.release()


def test_live_pid_without_matching_listening_socket_is_rejected(tmp_path):
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
        lock = InstanceLock(tmp_path, port=port).acquire()
        try:
            with pytest.raises(RuntimeError, match="not this Job Server"):
                verify_local_daemon(tmp_path, port)
        finally:
            lock.release()


def test_other_os_account_and_uninspectable_process_are_rejected(tmp_path, listener, monkeypatch):
    lock = InstanceLock(tmp_path, port=listener).acquire()
    descriptor = json.loads(descriptor_path(tmp_path).read_text())
    try:
        def process(pid=None):
            return SimpleNamespace(
                create_time=lambda: descriptor["created_at"],
                username=lambda: "other-account" if pid is not None else "current-account",
            )

        monkeypatch.setattr("job_server.daemon_ownership.psutil.Process", process)
        with pytest.raises(RuntimeError, match="not this Job Server"):
            verify_local_daemon(tmp_path, listener)

        def inaccessible(pid=None):
            raise psutil.AccessDenied(pid)

        monkeypatch.setattr("job_server.daemon_ownership.psutil.Process", inaccessible)
        with pytest.raises(RuntimeError, match="not this Job Server"):
            verify_local_daemon(tmp_path, listener)
    finally:
        lock.release()


def test_oversized_descriptor_is_rejected(tmp_path, listener):
    lock = InstanceLock(tmp_path, port=listener).acquire()
    try:
        descriptor_path(tmp_path).write_bytes(b" " * 4097)
        with pytest.raises(RuntimeError, match="not this Job Server"):
            verify_local_daemon(tmp_path, listener)
    finally:
        lock.release()


def test_hard_linked_runtime_files_are_rejected(tmp_path, listener):
    outside = tmp_path / "unrelated.txt"
    outside.write_text("untouched")
    lock_path = tmp_path / "daemon.lock"
    os.link(outside, lock_path)
    with pytest.raises(ServerError):
        InstanceLock(tmp_path).acquire()
    lock_path.unlink()
    lock = InstanceLock(tmp_path, port=listener).acquire()
    try:
        descriptor_path(tmp_path).unlink()
        os.link(outside, descriptor_path(tmp_path))
        with pytest.raises(RuntimeError, match="not this Job Server"):
            verify_local_daemon(tmp_path, listener)
        assert outside.read_text() == "untouched"
    finally:
        lock.release()


def test_linked_lock_and_descriptor_are_rejected(tmp_path, listener):
    outside = tmp_path / "unrelated.txt"
    outside.write_text("untouched")
    lock_path = tmp_path / "daemon.lock"
    try:
        lock_path.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires Windows Developer Mode or privilege")
    with pytest.raises(ServerError):
        InstanceLock(tmp_path).acquire()
    assert outside.read_text() == "untouched"
    lock_path.unlink()
    lock = InstanceLock(tmp_path, port=listener).acquire()
    try:
        descriptor_path(tmp_path).unlink()
        descriptor_path(tmp_path).symlink_to(outside)
        with pytest.raises(RuntimeError, match="not this Job Server"):
            verify_local_daemon(tmp_path, listener)
        assert outside.read_text() == "untouched"
    finally:
        lock.release()
