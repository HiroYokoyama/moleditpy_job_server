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
    if text.count(before) != 1:
        raise RuntimeError("Upstream native menu fixture changed; review the Qt adaptation")
    path.write_text(text.replace(before, after), "utf-8")


def main():
    root = Path(__file__).resolve().parents[1]
    temporary_root = root / "test-results"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="regression-", dir=temporary_root) as temporary:
        directory = Path(temporary)
        build(directory / "job_manager")
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
