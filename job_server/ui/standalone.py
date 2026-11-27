"""The dashboard run without MoleditPy: ``python -m job_server gui``.

Like Job Manager's standalone monitor, a second launch does not open a
second window: it asks the dashboard already running for this
configuration to come to the front, and exits. The daemon is not involved
either way — this window is only a client of it — but two settings
editors racing on one settings file would still confuse, and one window
is what the user asked for when they clicked again.

Qt is imported only inside the functions, so importing this module (and
the CLI's argument parsing) stays free of Qt.
"""

import hashlib
import sys
from pathlib import Path


DESKTOP_APP_ID = "hyoko.moleditpy.jobserver"


def _desktop_quote(argument):
    """Quote one Exec argument as the Desktop Entry specification asks."""
    if argument and not any(c in argument for c in " \t\n\"'\\><~|&;$*?#()`"):
        return argument
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in argument)
    return '"' + escaped.replace("%", "%%") + '"'


def desktop_entry(python, package, icon, config=None):
    """The text of the launcher that opens this dashboard.

    It runs ``python -m <package> gui`` from the folder holding the
    package, so it works the same for a pip install and for the plugin
    folder MoleditPy loads it from, whatever that folder is called
    (``job_server`` from the Plugin Installer, or a renamed clone). The
    icon is an absolute path: a themed name is not found while a stale
    icon cache hides it.
    """
    package = Path(package)
    command = [str(python), "-m", package.name, "gui"]
    if config:
        command += ["--config", str(Path(config).expanduser().resolve())]
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MoleditPy Job Server\n"
        "Comment=Manage the MoleditPy Job Server\n"
        f"Exec={' '.join(_desktop_quote(part) for part in command)}\n"
        f"Path={package.parent}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        f"StartupWMClass={DESKTOP_APP_ID}\n"
        "Categories=Science;\n"
    )


def desktop_folder():
    """The user's desktop folder: ``~/デスクトップ`` on a Japanese Ubuntu."""
    import subprocess

    try:
        found = subprocess.run(
            ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        found = ""
    # xdg-user-dir answers $HOME itself when no desktop folder is configured.
    if found and Path(found) != Path.home():
        return Path(found)
    return Path.home() / "Desktop"


def _trust(entry):
    """Let GNOME's desktop start the icon without asking first."""
    import subprocess

    try:
        subprocess.run(
            ["gio", "set", str(entry), "metadata::trusted", "true"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def install_launcher(config=None, data_home=None, desktop=None):
    """Write the launcher, its icon and a desktop icon for this dashboard (Linux).

    Returns the paths written: the application-list entry, then the copy on
    the desktop (unless ``desktop`` is False). The file name must be the app
    ID: on Wayland that is how GNOME matches the window to its Dock entry.
    """
    import os
    import shutil

    package = Path(__file__).resolve().parents[1]
    if data_home is None:
        data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    data_home = Path(data_home)
    icon = data_home / "icons" / "hicolor" / "128x128" / "apps" / f"{DESKTOP_APP_ID}.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package / "web" / "icon.png", icon)
    text = desktop_entry(sys.executable, package, icon, config)
    entries = [data_home / "applications" / f"{DESKTOP_APP_ID}.desktop"]
    if desktop is not False:
        entries.append(Path(desktop or desktop_folder()) / f"{DESKTOP_APP_ID}.desktop")
    for entry in entries:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(text, encoding="utf-8")
        entry.chmod(0o755)
    if len(entries) > 1:
        _trust(entries[1])
    return entries


def instance_name(config=None):
    """The single-instance name for one configuration file.

    Keyed by the resolved config path, so dashboards for two different
    configurations can run side by side while a second launch for the
    same one raises the first.
    """
    if config:
        path = Path(config).expanduser().resolve()
    else:
        path = Path.home() / ".moleditpy" / "job_server" / "settings.json"
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"moleditpy-job-server-gui-{digest}"


def ask_running_to_show(name):
    """Ask the dashboard listening on ``name`` to come to the front."""
    from PySide6.QtNetwork import QLocalSocket

    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"show\n")
    socket.flush()
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def run_gui(config=None):
    """Open the dashboard as its own program. Returns the exit code."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtNetwork import QLocalServer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "The Job Server window needs PySide6. Install it with: "
            'python -m pip install "moleditpy-job-server[gui]"',
            file=sys.stderr,
        )
        return 1
    app = QApplication.instance()
    owns_app = app is None
    if owns_app:
        if sys.platform == "win32":
            from ctypes import windll

            windll.shell32.SetCurrentProcessExplicitAppUserModelID(DESKTOP_APP_ID)
        app = QApplication(sys.argv)
        if sys.platform == "linux":
            app.setApplicationName(DESKTOP_APP_ID)
            app.setDesktopFileName(DESKTOP_APP_ID)
    name = instance_name(config)
    if ask_running_to_show(name):
        return 0
    # A window that was killed leaves its name behind; clear it and listen.
    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        server = None  # another window won the race; open ours anyway

    from ..plugin import dependency_dialog, missing_dependencies

    missing = missing_dependencies()
    if missing:
        dependency_dialog(None, missing)
        return 1
    try:
        from .dashboard import Dashboard

        dialog = Dashboard(config=config)
    except ImportError as exc:
        dependency_dialog(None, [exc.name or str(exc)])
        return 1

    if sys.platform in ("win32", "linux"):
        # Give the standalone dashboard a regular application-window entry.
        # Mask in integers: PySide6's ~WindowType_Mask is 0x1ffff00, which
        # also drops WindowCloseButtonHint (0x8000000) and greys out the
        # title-bar close button on Windows.
        flags = int(dialog.windowFlags()) & ~int(Qt.WindowType.WindowType_Mask)
        dialog.setWindowFlags(Qt.WindowType(flags | int(Qt.WindowType.Window)))
        if owns_app:
            app.setWindowIcon(dialog.windowIcon())

    if server is not None:
        pending = []

        def on_connection():
            connection = server.nextPendingConnection()
            if connection is None:
                return
            pending.append(connection)

            def forget():
                if connection in pending:
                    pending.remove(connection)

            connection.disconnected.connect(forget)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

        server.newConnection.connect(on_connection)
    dialog.show()
    return app.exec()
