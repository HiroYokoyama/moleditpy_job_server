"""Run the upstream suite against a patched copy, without changing upstream."""

import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from shutil import copytree, copy2

from build_job_manager import build


def patch_native_menu_fixture(tests_directory):
    """Adapt the copied upstream Qt fixture without modifying upstream."""
    path = Path(tests_directory) / "test_host_monitor_gui.py"
    text = path.read_text("utf-8")
    before = 'with patch.object(QMenu, "exec", capture):'
    after = (
        'with patch.object(self.dialog.menu_job, "exec", '
        'lambda *args, **kwargs: capture(self.dialog.menu_job, *args, **kwargs)):'
    )
    if 'with patch.object(menu, "exec", return_value=None) as execute:' in text:
        return  # Upstream now patches the menu instance directly.
    if text.count(before) != 1:
        raise RuntimeError("Upstream native menu fixture changed; review the Qt adaptation")
    path.write_text(text.replace(before, after), "utf-8")


# Job Manager a2ba746, inserted after the reaper's first look at the job's pid.
REAPER_CHECK = r"""            "            if ($jobPid -match '^\\d+$' -and "
            "(Get-Process -Id ([int]$jobPid) -ErrorAction SilentlyContinue)) { continue }",
"""
REAPER_SECOND_LOOK = r"""            "            Start-Sleep -Milliseconds 500",
            "            if (Test-Path -LiteralPath ('status\\' + $entry)) { $jobPid = $null }",
            "            else {",
            "                $jobPid = Get-Content -LiteralPath ('pids\\' + $entry) "
            "-ErrorAction SilentlyContinue | Select-Object -First 1",
            "                if ($jobPid -match '^\\d+$' -and "
            "(Get-Process -Id ([int]$jobPid) -ErrorAction SilentlyContinue)) { continue }",
            "            }",
"""


def patch_powershell_reaper(job_manager_directory):
    """Backport an upstream race fix to the copy this suite tests.

    A forced start swaps its own pid for the job's with Move-Item -Force, which can
    delete the old file before renaming the new one. A reap pass between the two
    moved a starting job to done/, so it never ran and the force tests timed out.
    Job Server does not ship this helper, so only the regression copy needs it.
    """
    path = Path(job_manager_directory) / "remote_runner_ps.py"
    text = path.read_text("utf-8")
    if REAPER_SECOND_LOOK in text:
        return  # Upstream already has the fix.
    if text.count(REAPER_CHECK) != 1:
        raise RuntimeError("Upstream PowerShell reaper changed; review the backport")
    path.write_text(text.replace(REAPER_CHECK, REAPER_CHECK + REAPER_SECOND_LOOK), "utf-8")


def main():
    root = Path(__file__).resolve().parents[1]
    temporary_root = root / "test-results"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="regression-", dir=temporary_root) as temporary:
        directory = Path(temporary)
        build(directory / "job_manager")
        patch_powershell_reaper(directory / "job_manager")
        copytree(root / "upstream" / "job_manager" / "tests", directory / "tests")
        patch_native_menu_fixture(directory / "tests")
        for path in (root / "upstream" / "job_manager").iterdir():
            if path.name in {".git", "job_manager", "tests"}:
                continue
            if path.is_dir():
                copytree(path, directory / path.name)
            else:
                copy2(path, directory / path.name)
        environment = os.environ.copy()
        git_utilities = Path(r"C:\Program Files\Git\usr\bin")
        if os.name == "nt" and (git_utilities / "awk.exe").exists():
            environment["PATH"] = str(git_utilities) + os.pathsep + environment.get("PATH", "")
        python = sys.executable
        paths = [str(directory), str(root)]
        if os.name == "nt" and sys.prefix != sys.base_prefix:
            # Windows venv python.exe is a redirector with a second PID. Upstream
            # real-process tests compare Popen.pid with the beacon's os.getpid().
            # Run the actual interpreter with this venv's packages on PYTHONPATH.
            python = sys._base_executable
            paths.append(sysconfig.get_path("purelib"))
        environment["PYTHONPATH"] = os.pathsep.join(paths)
        result = subprocess.run(
            [
                python,
                "-m",
                "pytest",
                "tests",
                "-q",
                "--tb=short",
                "--timeout=300",
                "--timeout-method=thread",
                "-n",
                "4",
                "--basetemp",
                str(directory / "pytest-temp"),
            ],
            cwd=directory,
            env=environment,
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
