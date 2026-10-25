"""Detached supervisor. It owns the payload and outlives daemon/GUI restarts."""

import json
import os
import sys
import time

import psutil

from .core import build_wrapper
from .executor import native, wsl
from .settings import Settings
from .files import atomic_write


def atomic_json(path, value):
    atomic_write(path, json.dumps(value).encode("utf-8"))


def main(config, job_id):
    settings = Settings.load(config)
    control = settings.root / "control" / job_id
    request = json.loads((control / "request.json").read_text("utf-8"))
    spec = request["spec"]
    work = (
        (wsl.native_root(settings) if settings.execution_os == "wsl" else settings.root)
        / "jobs"
        / job_id
        / "work"
    )
    identity = {"pid": os.getpid(), "process_started": psutil.Process().create_time()}
    atomic_json(control / "worker.json", identity)
    process, containment = None, None
    reason = ""
    outcome = {"state": "FAILED", "exit_code": None}
    try:
        if (control / "cancel").exists():
            outcome = {"state": "CANCELLED", "exit_code": None}
            return
        windows = settings.execution_os == "windows"
        execution_dir = (
            settings.wsl_root + "/jobs/" + job_id + "/work"
            if settings.execution_os == "wsl"
            else str(work)
        )
        script, payload = build_wrapper(spec, execution_dir, windows)
        wrapper = work / ("moleditpy_run.ps1" if windows else "moleditpy_run.sh")
        wrapper.write_text(script, encoding="utf-8-sig" if windows else "utf-8", newline="\n")
        if payload:
            (work / "payload.ps1").write_text(payload, encoding="utf-8-sig", newline="\n")
        with (work / "job.log").open("wb") as log:
            process, containment = (
                wsl.start(settings, job_id, wrapper, log)
                if settings.execution_os == "wsl"
                else native.start(wrapper, log, windows)
            )
            deadline = time.monotonic() + spec["resources"]["walltime_seconds"]
            while process.poll() is None:
                cancel = (control / "cancel").exists()
                too_large = native.directory_size(work) > settings.storage_quota_mb * 1024 * 1024
                timed_out = time.monotonic() > deadline
                if cancel or too_large or timed_out:
                    reason = (
                        "cancelled"
                        if cancel
                        else "storage_quota"
                        if too_large
                        else "walltime_exceeded"
                    )
                    if settings.execution_os == "wsl":
                        wsl.terminate(settings, process, containment)
                        containment = None
                    else:
                        native.terminate(process, containment)
                    outcome = {
                        "state": "CANCELLED" if cancel else "FAILED",
                        "exit_code": process.returncode,
                    }
                    break
                time.sleep(0.1)
            else:
                return_code = process.returncode
                sentinel = work / ".moleditpy_rc"
                try:
                    rc = int(sentinel.read_text("ascii").strip())
                    # The separately launched payload is authoritative for native exit.
                    if return_code and rc == 0:
                        rc = return_code
                    outcome = {"state": "DONE" if rc == 0 else "FAILED", "exit_code": rc}
                except (OSError, ValueError):
                    outcome = {"state": "LOST", "exit_code": return_code}
                    reason = "missing_or_invalid_sentinel"
            if containment and settings.execution_os == "wsl":
                # A successful shell may leave background descendants in its
                # Linux process group after wsl.exe itself has returned.
                wsl.terminate(settings, process, containment, grace=0.1)
                containment = None
            elif containment:
                containment.terminate()
                containment.close()
                containment = None
            elif settings.execution_os == "linux":
                # Clean up descendants that backgrounded themselves after the shell exited.
                native.terminate(process, None, grace=0.1)
    except Exception as exc:
        reason = f"worker_error:{type(exc).__name__}"
        outcome = {"state": "FAILED", "exit_code": None}
        if process is not None:
            outcome = {"state": "LOST", "exit_code": None}
            reason = "termination_needs_recheck"
        if process is not None and process.poll() is None:
            try:
                if settings.execution_os == "wsl":
                    wsl.terminate(settings, process, containment)
                else:
                    native.terminate(process, containment)
            except Exception:
                outcome = {"state": "LOST", "exit_code": None}
                reason = "termination_needs_recheck"
        with (control / "error.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{type(exc).__name__}: {exc}\n")
    finally:
        if containment and settings.execution_os != "wsl":
            containment.close()
        atomic_json(
            control / "done.json",
            {**outcome, "reason": reason, "ended_at": time.time(), **identity},
        )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
