import os
import shutil
import signal
import stat
import subprocess
import time


_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


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
    """Bytes in regular files under root, not following links or junctions.

    Uses os.scandir, whose entries carry their stat on Windows, so a large
    scratch folder costs one directory listing per folder rather than a
    system call per file.
    """
    total, pending = 0, [os.fspath(root)]
    while pending:
        try:
            entries = os.scandir(pending.pop())
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(entry.path)
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
    return total
