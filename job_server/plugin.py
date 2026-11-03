"""Qt is imported only when a menu action opens a window."""


def missing_dependencies():
    from importlib.util import find_spec
    from .runtime import DEPENDENCY_FLOORS

    return [name for name in (*DEPENDENCY_FLOORS, "pydantic") if find_spec(name) is None]


def dependency_dialog(parent, missing):
    from PySide6.QtWidgets import QMessageBox
    from .runtime import DEPENDENCY_FLOORS

    requirements = " ".join(f'"{name}>={minimum}"' for name, minimum in DEPENDENCY_FLOORS.items())
    QMessageBox.warning(
        parent,
        "Job Server — Missing libraries",
        "Job Server could not load these Python libraries: " + ", ".join(missing)
        + "\n\nInstall or repair the dependencies in the Python environment used by MoleditPy, "
        "then try opening Job Server again.\n\npython -m pip install --upgrade " + requirements
        + "\n\nFor a portable/frozen MoleditPy application, use its supported dependency "
        "installation method. A separate daemon Python environment also needs these libraries.",
    )


def initialize(context):
    from pathlib import Path

    icon = str(Path(__file__).parent / "web" / "icon.png")

    def open_dashboard():
        existing = context.get_window("job_server_dashboard")
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        parent = context.get_main_window()
        missing = missing_dependencies()
        if missing:
            dependency_dialog(parent, missing)
            return
        try:
            from .ui.dashboard import Dashboard

            dialog = Dashboard(parent)
        except ImportError as exc:
            # Include missing transitive modules and broken binary imports that
            # cannot be detected by find_spec, without hiding unrelated errors.
            dependency_dialog(parent, [exc.name or str(exc)])
            return
        context.register_window("job_server_dashboard", dialog)
        dialog.show()

    # Users & Devices, Settings and Open Logs live inside the dashboard window
    # (tabs and File menu), so MoleditPy needs only this one entry.
    context.add_menu_action("Extensions/Job Server...", open_dashboard, icon=icon)
