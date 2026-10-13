import sys
from pathlib import Path

DEPENDENCY_FLOORS = {
    "fastapi": "0.143.0",
    "uvicorn": "0.54.0",
    "cryptography": "50.0.2",
    "psutil": "7.2.2",
    "starlette": "1.3.1",
    "anyio": "4.14.2",
    "packaging": "25",
}


def ensure_secure_dependencies():
    from importlib.metadata import PackageNotFoundError, version
    from packaging.version import Version

    outdated = []
    for package, minimum in DEPENDENCY_FLOORS.items():
        try:
            if Version(version(package)) < Version(minimum):
                outdated.append(f"{package}>={minimum}")
        except PackageNotFoundError:
            outdated.append(f"{package}>={minimum}")
    if outdated:
        raise RuntimeError(
            "Update this daemon's Python environment before starting Job Server: "
            + ", ".join(outdated)
            + ". Install the current wheel with python -m pip install --upgrade."
        )


def python_executable(settings):
    if settings.python_executable:
        path = Path(settings.python_executable).expanduser()
        if not path.is_file():
            raise ValueError("Configured python_executable is not a file")
        return str(path)
    if getattr(sys, "frozen", False):
        raise ValueError(
            "Set python_executable in settings to a Python environment with Job Server installed; the frozen MoleditPy executable cannot run Python modules"
        )
    return sys.executable
