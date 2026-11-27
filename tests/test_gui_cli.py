"""`python -m job_server gui`: the dashboard without MoleditPy."""

import ctypes
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_server.__main__ import main


def test_gui_dispatches_to_the_standalone_launcher(tmp_path, monkeypatch):
    seen = {}

    def fake_run_gui(config):
        seen["config"] = config
        return 7

    monkeypatch.setattr("job_server.ui.standalone.run_gui", fake_run_gui)
    config = tmp_path / "settings.json"
    monkeypatch.setattr(sys, "argv", ["job-server", "gui", "--config", str(config)])
    assert main() == 7
    assert seen["config"] == config


def test_instance_names_are_stable_and_per_configuration(tmp_path):
    from job_server.ui.standalone import instance_name

    one = tmp_path / "one" / "settings.json"
    two = tmp_path / "two" / "settings.json"
    assert instance_name(one) == instance_name(one)
    assert instance_name(one) != instance_name(two)
    assert instance_name() != instance_name(one)


def test_ask_running_to_show_reaches_a_listening_dashboard(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWidgets import QApplication
    from job_server.ui.standalone import ask_running_to_show, instance_name

    app = QApplication.instance() or QApplication([])
    name = instance_name(tmp_path / "settings.json")
    QLocalServer.removeServer(name)
    server = QLocalServer()
    assert server.listen(name)
    try:
        assert ask_running_to_show(name)
        app.processEvents()
        assert server.hasPendingConnections()
    finally:
        server.close()
        QLocalServer.removeServer(name)
    assert not ask_running_to_show(name)


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_standalone_dashboard_has_its_own_application_icon(tmp_path, monkeypatch, platform):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QDialog
    from job_server.ui import dashboard as dashboard_module
    from job_server.ui import standalone

    app = QApplication.instance() or QApplication([])
    original_name, original_desktop_file = app.applicationName(), app.desktopFileName()
    original_icon = app.windowIcon()
    dashboard = QDialog()
    root = Path(__file__).resolve().parents[1]
    dashboard.setWindowIcon(QIcon(str(root / "job_server" / "web" / "icon.png")))
    startup = []

    def create_app(argv):
        startup.append("QApplication")
        return app

    def set_windows_app_id(app_id):
        startup.append(app_id)

    create_app.instance = lambda: None
    monkeypatch.setattr(QtWidgets, "QApplication", create_app)
    monkeypatch.setattr(standalone, "sys", SimpleNamespace(platform=platform, argv=[]))
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=set_windows_app_id)
        ),
        raising=False,
    )
    monkeypatch.setattr("job_server.plugin.missing_dependencies", lambda: [])
    monkeypatch.setattr(dashboard_module, "Dashboard", lambda config: dashboard)

    def check_dashboard():
        assert dashboard.isVisible()
        assert dashboard.windowType() == Qt.WindowType.Window
        assert not app.windowIcon().isNull()
        assert app.windowIcon().cacheKey() == dashboard.windowIcon().cacheKey()
        if platform == "win32":
            assert startup == [standalone.DESKTOP_APP_ID, "QApplication"]
        else:
            app_id = standalone.DESKTOP_APP_ID
            assert app.applicationName() == app_id
            assert app.desktopFileName() == app_id
        return 0

    monkeypatch.setattr(app, "exec", check_dashboard)
    try:
        assert standalone.run_gui(tmp_path / "settings.json") == 0
    finally:
        dashboard.close()
        app.setApplicationName(original_name)
        app.setDesktopFileName(original_desktop_file)
        app.setWindowIcon(original_icon)


def test_install_launcher_writes_an_entry_named_after_the_app_id(tmp_path, monkeypatch):
    from job_server.ui import standalone

    trusted = []

    monkeypatch.setattr(standalone, "_trust", trusted.append)
    entry, on_desktop = standalone.install_launcher(
        tmp_path / "my config.json", data_home=tmp_path, desktop=tmp_path / "Desktop"
    )
    app_id = standalone.DESKTOP_APP_ID
    assert entry == tmp_path / "applications" / f"{app_id}.desktop"
    assert on_desktop == tmp_path / "Desktop" / f"{app_id}.desktop"
    assert on_desktop.read_text(encoding="utf-8") == entry.read_text(encoding="utf-8")
    assert trusted == [on_desktop]
    icon = tmp_path / "icons" / "hicolor" / "128x128" / "apps" / f"{app_id}.png"
    package = Path(standalone.__file__).resolve().parents[1]
    assert icon.read_bytes() == (package / "web" / "icon.png").read_bytes()
    fields = dict(line.split("=", 1) for line in entry.read_text(encoding="utf-8").splitlines()[1:])
    assert fields["StartupWMClass"] == app_id
    assert fields["Icon"] == str(icon)
    assert fields["Path"] == str(package.parent)
    config = str((tmp_path / "my config.json").resolve())
    assert fields["Exec"].endswith(
        f' -m {package.name} gui --config "{config}"'.replace("\\", "\\\\")
    )


def test_desktop_quote_escapes_the_reserved_characters():
    from job_server.ui.standalone import _desktop_quote

    assert _desktop_quote("/usr/bin/python3") == "/usr/bin/python3"
    assert _desktop_quote('/opt/my env/$x"%') == r'"/opt/my env/\$x\"%%"'


def test_install_launcher_can_skip_the_desktop_icon(tmp_path):
    from job_server.ui import standalone

    entries = standalone.install_launcher(data_home=tmp_path, desktop=False)
    assert entries == [tmp_path / "applications" / f"{standalone.DESKTOP_APP_ID}.desktop"]


def test_launcher_runs_the_plugin_folder_by_its_own_name(tmp_path):
    from job_server.ui.standalone import desktop_entry

    plugins = tmp_path / "plugins"
    text = desktop_entry("/usr/bin/python3", plugins / "moleditpy_job_server", "/i.png")
    assert "Exec=/usr/bin/python3 -m moleditpy_job_server gui" in text.splitlines()
    assert f"Path={plugins}" in text.splitlines()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_standalone_window_keeps_the_dashboard_title_bar_hints(tmp_path, monkeypatch, platform):
    # PySide6's ~WindowType_Mask also cleared WindowCloseButtonHint, so the
    # standalone dashboard's close button was greyed out on Windows.
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from job_server.settings import Settings
    from job_server.ui import dashboard as dashboard_module
    from job_server.ui import standalone

    app = QApplication.instance() or QApplication([])
    original_name, original_desktop_file = app.applicationName(), app.desktopFileName()
    original_icon = app.windowIcon()
    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    dashboard = dashboard_module.Dashboard(config=config)
    dashboard.timer.stop()

    real_windll = getattr(ctypes, "windll", None)

    def create_app(argv):
        return app

    create_app.instance = lambda: None
    monkeypatch.setattr(QtWidgets, "QApplication", create_app)
    monkeypatch.setattr(standalone, "sys", SimpleNamespace(platform=platform, argv=[]))
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=id)),
        raising=False,
    )
    monkeypatch.setattr("job_server.plugin.missing_dependencies", lambda: [])
    monkeypatch.setattr(dashboard_module, "Dashboard", lambda config: dashboard)

    def check_dashboard():
        dashboard.timer.stop()
        flags = dashboard.windowFlags()
        assert dashboard.windowType() == Qt.WindowType.Window
        for hint in (
            Qt.WindowType.WindowCloseButtonHint,
            Qt.WindowType.WindowMinimizeButtonHint,
            Qt.WindowType.WindowMaximizeButtonHint,
        ):
            assert flags & hint
        if os.name == "nt" and app.platformName() == "windows":
            # Ask Windows itself whether the title-bar close item is greyed;
            # the offscreen platform the suite normally uses has no HWND.
            app.processEvents()
            user32 = real_windll.user32
            user32.GetSystemMenu.restype = ctypes.c_void_p
            user32.GetSystemMenu.argtypes = [ctypes.c_void_p, ctypes.c_bool]
            user32.GetMenuState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
            menu = user32.GetSystemMenu(int(dashboard.winId()), False)
            sc_close, mf_grayed_or_disabled = 0xF060, 0x3
            assert menu
            assert not user32.GetMenuState(menu, sc_close, 0) & mf_grayed_or_disabled
        return 0

    monkeypatch.setattr(app, "exec", check_dashboard)
    try:
        assert standalone.run_gui(config) == 0
    finally:
        dashboard.close()
        app.processEvents()
        app.setApplicationName(original_name)
        app.setDesktopFileName(original_desktop_file)
        app.setWindowIcon(original_icon)
