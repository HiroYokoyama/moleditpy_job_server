"""Qt is imported only when a menu action opens a window."""


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
        from .ui.dashboard import Dashboard

        dialog = Dashboard(context.get_main_window())
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
