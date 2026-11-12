"""The default CPU budget counts physical cores the server may use, not threads."""

import os
import types

import pytest

from job_server import settings


def fake_psutil(monkeypatch, logical, physical, memory_mb=16384):
    module = types.SimpleNamespace(
        cpu_count=lambda logical_count=True, **kwargs: (
            physical if kwargs.get("logical", logical_count) is False else logical
        ),
        virtual_memory=lambda: types.SimpleNamespace(total=memory_mb * 1024 * 1024),
    )
    monkeypatch.setitem(__import__("sys").modules, "psutil", module)


@pytest.fixture
def no_affinity(monkeypatch):
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)


def test_hyper_threaded_pc_defaults_to_its_cores(monkeypatch, no_affinity):
    fake_psutil(monkeypatch, logical=12, physical=8)
    assert settings.pc_cpus() == (8, 12)
    assert settings.pc_resources() == (8, 16384)
    assert settings.pc_size_label("16.0") == "Use this PC's size (8 cores of 12 threads, 16.0 GiB)"


def test_without_hyper_threading_the_label_names_cores_only(monkeypatch, no_affinity):
    fake_psutil(monkeypatch, logical=4, physical=4)
    assert settings.pc_size_label("8.0") == "Use this PC's size (4 cores, 8.0 GiB)"
    fake_psutil(monkeypatch, logical=1, physical=1)
    assert settings.pc_size_label("1.0") == "Use this PC's size (1 core, 1.0 GiB)"


def test_unknown_physical_count_falls_back_to_threads(monkeypatch, no_affinity):
    fake_psutil(monkeypatch, logical=6, physical=None)
    assert settings.pc_cpus() == (6, 6)


def test_linux_affinity_limits_the_default(monkeypatch):
    fake_psutil(monkeypatch, logical=32, physical=16)
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2, 3}, raising=False)
    assert settings.pc_cpus() == (4, 4)
