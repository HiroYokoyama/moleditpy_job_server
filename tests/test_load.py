"""Background loops stay cheap: folder walks, the dispatcher and the supervisor."""

import os
import threading
import time
from unittest import mock

import pytest

from job_server import worker
from job_server.executor.native import directory_size
from job_server.scheduler import TICK_SECONDS, Scheduler


def test_directory_size_counts_nested_files(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "top.dat").write_bytes(b"x" * 10)
    (tmp_path / "a" / "b" / "deep.dat").write_bytes(b"x" * 32)
    assert directory_size(tmp_path) == 42
    assert directory_size(tmp_path / "missing") == 0


def test_directory_size_does_not_follow_links(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.dat").write_bytes(b"x" * 1000)
    work = tmp_path / "work"
    work.mkdir()
    (work / "own.dat").write_bytes(b"x" * 5)
    try:
        os.symlink(outside, work / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert directory_size(work) == 5


def test_size_checks_are_spaced_by_the_cost_of_the_walk():
    assert worker.SIZE_CHECK_SECONDS >= 10
    # A one-second walk is followed by at least 50 seconds without one.
    assert max(worker.SIZE_CHECK_SECONDS, 1.0 * worker.SIZE_CHECK_FACTOR) >= 50


def test_dispatcher_waits_a_full_tick_unless_woken():
    scheduler = Scheduler.__new__(Scheduler)
    scheduler.stop_event, scheduler.wake_event = threading.Event(), threading.Event()
    passes = []

    def tick():
        passes.append(time.monotonic())
        if len(passes) == 2:
            scheduler.stop_event.set()

    scheduler.tick = tick
    thread = threading.Thread(target=scheduler._loop)
    started = time.monotonic()
    thread.start()
    time.sleep(0.05)
    scheduler.wake()
    thread.join(timeout=TICK_SECONDS * 3)
    assert len(passes) == 2
    # Woken, the second pass came long before the one-second tick.
    assert passes[1] - started < TICK_SECONDS / 2


def test_a_successful_change_wakes_the_dispatcher(client, alice):
    from .conftest import reserve

    with mock.patch.object(client.app.state.scheduler, "wake") as wake:
        assert reserve(client, alice).status_code < 400
        assert wake.called
        wake.reset_mock()
        client.get("/api/v1/health")
        assert not wake.called
