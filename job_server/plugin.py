"""Qt is imported only when a menu action opens a window."""


def missing_dependencies():
    from importlib.util import find_spec
    from .runtime import DEPENDENCY_FLOORS

    return [name for name in (*DEPENDENCY_FLOORS, "pydantic") if find_spec(name) is None]


def dependency_dialog(parent, missing):
    from PyQt6.QtWidgets import QMessageBox
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

    def open_tab(index):
        existing = context.get_window("job_server_dashboard")
        if existing is not None:
            existing.tabs.setCurrentIndex(index)
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
        dialog.tabs.setCurrentIndex(index)
        dialog.show()

    context.add_plugin_menu("Job Server/Server Dashboard...", lambda: open_tab(0), icon=icon)
    context.add_plugin_menu("Job Server/Users & Devices...", lambda: open_tab(1), icon=icon)
    context.add_plugin_menu("Job Server/Settings...", lambda: open_tab(2), icon=icon)
    context.add_plugin_menu("Job Server/Open Logs...", lambda: open_logs(), icon=icon)


def open_logs():
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    from .settings import Settings

    directory = Settings.load().root / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
