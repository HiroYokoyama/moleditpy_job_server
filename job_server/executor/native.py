import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ..files import is_link


def powershell():
    return (
        shutil.which("powershell")
        or shutil.which("pwsh")
        or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )


def start(wrapper, log, windows):
    command = (
        [
            powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ]
        if windows
        else ["/bin/bash", str(wrapper)]
    )
    containment = None
    if windows:
        from .windows import WindowsJob

        containment = WindowsJob()
    try:
        process = subprocess.Popen(
            command,
            cwd=wrapper.parent,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=not windows,
            creationflags=0x08000000 | 0x00000004 if windows else 0,
        )
        if containment:
            containment.assign_and_resume(process)
        return process, containment
    except BaseException:
        if containment:
            containment.close()
        raise


def _signal_group(process, signum):
    try:
        os.killpg(process.pid, signum)
    except PermissionError:
        # Darwin reports EPERM for a group containing only an unreaped zombie.
        # Reap our leader and retry; a real permission failure must still escape.
        if process.poll() is None:
            raise
        os.killpg(process.pid, signum)


def terminate(process, containment, grace=1):
    if containment:
        containment.terminate()
    else:
        try:
            _signal_group(process, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace
        # The leader may exit first while a child ignores TERM. Always kill
        # the group after the grace period, even when Popen has reaped its leader.
        while time.monotonic() < deadline:
            process.poll()
            time.sleep(0.05)
        try:
            _signal_group(process, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def directory_size(root):
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not is_link(Path(directory) / name)]
        for name in files:
            path = Path(directory) / name
            try:
                if not is_link(path):
                    total += path.stat().st_size
            except OSError:
                pass
    return total
