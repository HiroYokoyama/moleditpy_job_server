"""Real installed Job Manager GUI/CLI -> Job Server daemon integration."""

import json
import os
import shutil
import subprocess
import sys
import time
from importlib.metadata import distribution
from pathlib import Path


def check_manager(server_client, root, expected_version):
    import job_manager
    from job_manager.api_client import JobManagerClient, discover
    from job_manager.models import HostProfile
    from job_manager.server_backend import JobServerBackend

    package = distribution("moleditpy-job-manager")
    assert package.version == expected_version
    assert Path(job_manager.__file__).resolve().is_relative_to(
        Path(package.locate_file("job_manager")).resolve()
    ), "Job Manager must come from the installed distribution"
    host = HostProfile(
        name="fixture-server", backend="job_server", server_url=server_client.url,
        server_key_path=str(root / "manager-device.key"), server_id=server_client.server_id,
        scheduler="windows" if os.name == "nt" else "shell",
    )
    JobServerBackend(host).client.enroll("wheel-smoke", "synthetic-password-123")
    data = root / "manager"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({
        "version": 1, "hosts": [host.to_dict()], "presets": [],
        "prefs": {
            "api_enabled": True, "api_port": 0, "poll_interval": 5,
            "notify_on_finish": False, "open_result_after_download": False,
        },
    }), encoding="utf-8")
    inputs = root / "inputs with spaces"
    inputs.mkdir()
    source = inputs / "fixture input.inp"
    source.write_text("manager-server-integration-ok\n", encoding="utf-8")
    command = (
        "Get-Content {input} | Set-Content result.out -Encoding ascii"
        if os.name == "nt" else "cat {input} > result.out"
    )
    env = dict(os.environ)
    env["MOLEDITPY_JOB_MANAGER_DIR"] = str(data)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("MOLEDITPY_JOB_API_URL", None)
    env.pop("MOLEDITPY_JOB_API_TOKEN", None)

    def executable(name):
        value = shutil.which(name, path=str(Path(sys.executable).parent))
        assert value, f"Missing installed command {name}"
        return value

    log_path = root / "manager.log"
    with log_path.open("wb") as log:
        window = subprocess.Popen(
            [executable("moleditpy-job-manager")], env=env, stdout=log, stderr=log,
        )
        api = None
        try:
            deadline = time.monotonic() + 60
            while not (data / "api.json").is_file():
                assert window.poll() is None, "Job Manager exited before starting its API"
                assert time.monotonic() < deadline, "Job Manager API did not start"
                time.sleep(0.2)
            endpoint = discover(str(data))
            api = JobManagerClient(url=endpoint["url"], token=endpoint["token"], timeout=15)
            result = subprocess.run([
                executable("moleditpy-job"), "submit", str(source),
                "--host", host.name, "--command", command, "--memory", "512M",
                "--walltime", "00:00:30", "--fetch", "*.out", "--wait",
            ], cwd=inputs, env=env, capture_output=True, text=True, timeout=150)
            assert result.returncode == 0, result.stdout + result.stderr
            jobs = api.jobs()
            assert len(jobs) == 1, jobs
            job = jobs[0]
            assert job["state"] == "DONE" and job["rc"] == 0, job
            assert job["server_job_id"] and job["execution_os"] in {"linux", "windows"}
            remote = server_client.request("GET", "/jobs/" + job["server_job_id"])
            assert remote["state"] == "DONE" and remote["exit_code"] == 0
            assert job["downloaded"], "CLI --wait did not download the server output"
            outputs = [Path(path) for path in job["downloaded_files"] if Path(path).name == "result.out"]
            assert len(outputs) == 1
            assert outputs[0].read_text().strip() == "manager-server-integration-ok"
            assert "result.out" in api.files(job["id"])
            assert "manager-server-integration-ok" in api.log(job["id"], file="result.out")
            manual_dir = root / "manual-download"
            manual_dir.mkdir()
            manual = api.download(job["id"], into=str(manual_dir), names=["result.out"], wait=True)
            assert len(manual["files"]) == 1
            assert Path(manual["files"][0]).read_text().strip() == "manager-server-integration-ok"

            # A failing remote command must reach the manager with its actual exit code.
            failed = api.submit(
                host=host.name, files=[str(source)], command="exit 7", memory="512M",
                auto_download=False,
            )
            failed = api.wait(failed["id"], interval=0.2, timeout=45)
            assert failed["state"] == "FAILED" and failed["rc"] == 7, failed

            # Cancel only once submission has reached the daemon, rather than cancelling
            # the local upload record before there is a server job to stop.
            cancelled = api.submit(
                host=host.name, files=[str(source)],
                command="Start-Sleep -Seconds 30" if os.name == "nt" else "sleep 30",
                memory="512M", auto_download=False,
            )
            deadline = time.monotonic() + 30
            while cancelled["state"] != "RUNNING":
                assert time.monotonic() < deadline, cancelled
                assert cancelled["state"] not in {"DONE", "FAILED", "LOST", "CANCELLED"}, cancelled
                time.sleep(0.2)
                cancelled = api.job(cancelled["id"])
            api.cancel(cancelled["id"])
            cancelled = api.wait(cancelled["id"], interval=0.2, timeout=30)
            assert cancelled["state"] == "CANCELLED", cancelled
            deadline = time.monotonic() + 15
            while True:
                remote = server_client.request("GET", "/jobs/" + cancelled["server_job_id"])
                if remote["state"] == "CANCELLED":
                    break
                assert time.monotonic() < deadline, remote
                time.sleep(0.2)
            assert window.poll() is None
        finally:
            try:
                if api is not None and window.poll() is None:
                    for active in api.jobs(state="ACTIVE"):
                        api.cancel(active["id"])
            finally:
                if window.poll() is None:
                    window.terminate()
                    window.wait(timeout=20)
                print(log_path.read_text(errors="replace"))
    print(f"Installed Job Manager {expected_version}: CLI -> GUI/API -> server execution, upload, "
          "automatic/manual download, file preview, failure and cancellation passed")
