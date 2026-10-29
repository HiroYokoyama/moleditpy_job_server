"""Exercise an installed wheel outside the checkout, using only synthetic data."""

import argparse
import os
import shutil
import socket
import subprocess
import sysconfig
import tempfile
import time
from importlib.metadata import distribution
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manager-version", help="Also exercise the installed Job Manager GUI/CLI")
    args = parser.parse_args()
    package = distribution("moleditpy-job-server")
    assert package.version == args.version

    import job_server
    from job_server.auth import Auth
    from job_server.client import JobServerClient
    from job_server.db import Database
    from job_server.settings import Settings

    assert Path(job_server.__file__).resolve().is_relative_to(
        Path(package.locate_file("job_server")).resolve()
    ), "Imported the checkout instead of the installed wheel"
    executable = shutil.which("moleditpy-job-server", path=sysconfig.get_path("scripts"))
    assert executable, "Missing console entry point"
    subprocess.run([executable, "--help"], check=True, timeout=15)
    with tempfile.TemporaryDirectory(prefix="job-server-wheel-") as temporary:
        root = Path(temporary)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        settings = Settings(
            data_dir=str(root / "data"), port=port, memory_mb=1024,
            allow_privileged=True,  # Isolated CI fixture only.
        )
        config = root / "settings.json"
        settings.save(config)
        db = Database(settings.root)
        try:
            Auth(db, settings).create_initial_admin("wheel-smoke", "synthetic-password-123")
        finally:
            db.close()
        with (root / "daemon.log").open("wb") as log:
            process = subprocess.Popen(
                [executable, "--config", str(config), "serve"], stdout=log, stderr=log,
            )
            client = JobServerClient(f"http://127.0.0.1:{port}", root / "device.key", timeout=3)
            try:
                deadline = time.monotonic() + 45
                while True:
                    try:
                        health = client.request("GET", "/health", authenticated=False)
                        assert health["server_version"] == args.version
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("Daemon failed to start")
                        time.sleep(0.2)
                with build_opener(ProxyHandler({})).open(client.url, timeout=3) as response:
                    assert b"<html" in response.read().lower()
                client.enroll("wheel-smoke", "synthetic-password-123")
                command = (
                    "Set-Content -Path result.out -Value 'wheel-smoke-ok' -Encoding ascii"
                    if os.name == "nt" else "printf 'wheel-smoke-ok\\n' > result.out"
                )
                job = client.submit({
                    "name": "wheel-smoke", "command": command,
                    "resources": {"cpus_per_task": 1, "memory_mb": 512, "walltime_seconds": 30},
                })
                deadline = time.monotonic() + 60
                while job["state"] not in {"DONE", "FAILED", "LOST", "CANCELLED"}:
                    assert time.monotonic() < deadline, "Job did not finish"
                    time.sleep(0.2)
                    job = client.request("GET", "/jobs/" + job["id"])
                assert job["state"] == "DONE", job
                destination = root / "download.out"
                client.download(job["id"], "result.out", destination)
                assert destination.read_text().strip() == "wheel-smoke-ok"
                if args.manager_version:
                    from smoke_test_manager import check_manager

                    check_manager(client, root, args.manager_version)
                client.request("POST", "/admin/shutdown")
                process.wait(timeout=20)
                assert process.returncode == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
                print((root / "daemon.log").read_text(errors="replace"))
    print(f"Installed Job Server {args.version}: CLI, daemon, web UI, auth, job and download passed")


if __name__ == "__main__":
    main()
