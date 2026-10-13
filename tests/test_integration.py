import os
import subprocess
import sys
from pathlib import Path

from scripts.build_job_manager import build


def test_companion_job_manager_routes_use_backend_not_transport(tmp_path):
    target = tmp_path / "job_manager"
    patch = build(target)
    assert "Job Server (HTTPS)" in patch
    code = r"""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from PyQt6.QtWidgets import QApplication
app=QApplication([])
from job_manager.models import HostProfile,Job,SubmitPreset
host=HostProfile(backend='job_server',server_url='https://compute.example.org',server_key_path='/tmp/device.key')
assert host.is_job_server and not host.uses_remote_runner
assert HostProfile.from_dict(host.to_dict()).server_url==host.server_url
assert host.target==host.server_url
job=Job(server_job_id='remote',server_id='server',owner_user_id='owner',execution_os='linux')
assert Job.from_dict(job.to_dict()).server_job_id=='remote'
from job_manager.poller import _PollTask
import job_manager.server_backend as bridge
class FakeBackend:
    def poll(self,jobs):
        for job in jobs:job.rc=7
        return {job.id:'FAILED' for job in jobs}
bridge.backend_for=lambda host:FakeBackend()
results=[]
task=_PollTask(host,[job],lambda host: (_ for _ in ()).throw(AssertionError('shell transport used')))
task.signals.finished.connect(lambda *args:results.append(args))
task.run_sync()
assert results and results[0][1][job.id]=='FAILED'
from job_manager.store import JobStore
from job_manager.hosts_dialog import HostsDialog
store=JobStore(directory=os.environ['MOLEDITPY_JOB_MANAGER_DIR'])
from job_manager.service import JobService
service=JobService(store=store)
dialog=HostsDialog(service)
index=dialog.cmb_backend.findData('job_server')
assert index>=0
dialog.cmb_backend.setCurrentIndex(index)
assert 'Authenticated Job Server' in dialog.lbl_backend_hint.text()
dialog.close()
service.shutdown()
print('companion integration passed')
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = (
        str(tmp_path) + os.pathsep + str(Path(__file__).resolve().parents[1])
    )
    environment["MOLEDITPY_JOB_MANAGER_DIR"] = str(tmp_path / "client-data")
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_python_client_tls_policy():
    from job_server.client import JobServerClient
    import pytest

    for url in (
        "http://compute.example.org",
        "http://127.0.0.1.evil.org",
        "https://user:secret@compute.example.org",
        "file:///tmp",
    ):
        with pytest.raises(ValueError):
            JobServerClient(url)
    assert JobServerClient("http://127.0.0.1:8766").url == "http://127.0.0.1:8766"


def test_loopback_client_never_sends_credentials_to_environment_proxy(monkeypatch):
    from urllib.request import ProxyHandler
    from job_server.client import JobServerClient

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.org:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.org:8080")
    client = JobServerClient("http://127.0.0.1:8766")
    assert not any(
        isinstance(handler, ProxyHandler) and handler.proxies for handler in client.opener.handlers
    )


def test_credentials_roundtrip_without_plaintext_private_key(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from job_server.credentials import save_key, load_key

    key = Ed25519PrivateKey.generate()
    path = tmp_path / "device.key"
    save_key(path, "device", "server", key)
    loaded = load_key(path)
    assert loaded["device_id"] == "device"
    assert loaded["private_key"].sign(b"example") == key.sign(b"example")
    if os.name == "nt":
        assert path.read_bytes().startswith(b"DPAPI\n")
        assert b'"key"' not in path.read_bytes()
    else:
        assert path.stat().st_mode & 0o077 == 0
