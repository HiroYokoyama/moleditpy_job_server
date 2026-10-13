import shlex
import subprocess
import time
from pathlib import Path


def native_root(settings):
    return Path(
        "\\\\wsl.localhost\\" + settings.wsl_distribution + settings.wsl_root.replace("/", "\\")
    )


def run(settings, *args, **kwargs):
    return subprocess.run(
        ["wsl.exe", "-d", settings.wsl_distribution, "--exec", *args],
        creationflags=0x08000000,
        timeout=15,
        check=True,
        **kwargs,
    )


def start(settings, job_id, wrapper, log):
    linux_work = settings.wsl_root + "/jobs/" + job_id + "/work"
    pidfile = linux_work + "/.server_process"
    # The distribution is admin-configured and every dynamic path is quoted.
    command = (
        f"cd {shlex.quote(linux_work)} || exit 1; "
        f"setsid /bin/bash {shlex.quote(linux_work + '/' + wrapper.name)} & p=$!; "
        f'printf \'%s\\n\' "$p" > {shlex.quote(pidfile)}; wait "$p"'
    )
    process = subprocess.Popen(
        ["wsl.exe", "-d", settings.wsl_distribution, "--exec", "/bin/bash", "-c", command],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=0x08000000,
    )
    return process, wrapper.parent / ".server_process"


def terminate(settings, process, pidfile):
    try:
        pid = int(pidfile.read_text().strip())
        if pid <= 1:
            raise ValueError("Invalid WSL process group")
        for signal in ("-TERM", "-KILL"):
            run(
                settings,
                "/bin/kill",
                signal,
                "--",
                f"-{pid}",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
    except subprocess.CalledProcessError:
        # A vanished group is the expected result of a successful termination.
        pass
    process.wait(timeout=15)
