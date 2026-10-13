"""Run the upstream suite against a patched copy, without changing upstream."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from shutil import copytree, copy2

from build_job_manager import build


def main():
    root = Path(__file__).resolve().parents[1]
    temporary_root = root / "test-results"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="regression-", dir=temporary_root) as temporary:
        directory = Path(temporary)
        build(directory / "job_manager")
        copytree(root / "upstream" / "job_manager" / "tests", directory / "tests")
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
        environment["PYTHONPATH"] = str(directory) + os.pathsep + str(root)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests",
                "-q",
                "--tb=short",
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
