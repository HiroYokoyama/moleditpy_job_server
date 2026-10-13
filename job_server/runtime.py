import sys
from pathlib import Path


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
