
from ._qt_callbacks import connect_queued
import json
import os
import subprocess
import time
from datetime import datetime
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenuBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import JobServerClient
from ..settings import Settings


class Signals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, call):
        super().__init__()
        self.call, self.signals = call, Signals()

    def run(self):
        try:
            self.signals.finished.emit(self.call())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class Dashboard(QDialog):
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("Job Server")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1] / "web" / "icon.png")))
        self.resize(900, 600)
        self.config = Path(config) if config else Settings().root / "settings.json"
        self.settings = Settings.load(self.config) if self.config.exists() else Settings()
        self.client = JobServerClient(
            f"http://127.0.0.1:{self.settings.port}",
            self.settings.root / "identity" / "admin-device.key",
        )
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._tasks = set()
        self.busy = False
        self.setup_dialog = None
        self._setup_checked = False
        layout = QVBoxLayout(self)
        self.menu_bar = QMenuBar(self)
        layout.setMenuBar(self.menu_bar)
        self._menus()
        notice = QLabel(
            "Trusted users only. Jobs share the service OS account; CPU/RAM reservations are advisory.\n"
            "WARNING: Never expose to the public internet. Localhost, private LAN or private Tailscale only; "
            "no Funnel or public port forwarding. Use a low-privilege OS account."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.message = QLabel("Server is OFF until you explicitly start it.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._dashboard()
        self._users()
        self._settings()
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def _menus(self):
        def action(menu, text, callback):
            item = QAction(text, self)
            item.triggered.connect(callback)
            menu.addAction(item)
            return item

        files = self.menu_bar.addMenu("File")
        action(files, "Setup guide...", self.open_setup)
        files.addSeparator()
        action(files, "Open Web UI", self.open_web_ui)
        action(files, "Open Logs...", self.open_logs)
        files.addSeparator()
        action(files, "Close", self.close)
        server = self.menu_bar.addMenu("Server")
        action(server, "Start", self.start_daemon)
        action(server, "Stop (preserve jobs)", self.stop_daemon)
        action(server, "Restart", self.restart_daemon)
        server.addSeparator()
        action(server, "Refresh", self.refresh)
        action(server, "Copy Tailscale Serve command", lambda: QApplication.clipboard().setText(
            f"tailscale serve --bg http://127.0.0.1:{self.settings.port}"))
        account = self.menu_bar.addMenu("Account")
        self.sign_in_action = action(account, "Sign in / register device...", self.sign_in)
        account.addSeparator()
        action(account, "Create initial administrator...", self.bootstrap)
        action(account, "Add user...", self.add_user)
        action(account, "Disable selected user...", self.disable_user)
        action(account, "Revoke selected device...", self.revoke_device)
        queue = self.menu_bar.addMenu("Queue")
        action(queue, "Pause queue", lambda: self.pause(True))
        action(queue, "Resume queue", lambda: self.pause(False))
        action(queue, "Cancel selected job", self.cancel)

    def open_logs(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        directory = self.settings.root / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _dashboard(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.resources = QLabel("Sign in to view resources and jobs.")
        self.resources.setWordWrap(True)
        layout.addWidget(self.resources)
        self.jobs = QTableWidget(0, 4)
        self.jobs.setHorizontalHeaderLabels(["ID", "Owner", "Name", "State"])
        self.jobs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.jobs)
        self.tabs.addTab(page, "Dashboard")

    def _users(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.users = QTableWidget(0, 3)
        self.users.setHorizontalHeaderLabels(["Username", "Role", "Status"])
        self.users.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.users)
        self.devices = QTableWidget(0, 4)
        self.devices.setHorizontalHeaderLabels(["Device ID", "Username", "Status", "Last access (local)"])
        self.devices.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.devices)
        self.tabs.addTab(page, "Users & Devices")

    def _settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.fields = {}
        for key, label, maximum in (
            ("port", "Private loopback port", 65535),
            ("cpus", "CPU budget", 4096),
            ("memory_mb", "Memory budget (MB)", 2147483647),
            ("max_active_per_user", "Active jobs per user", 10000),
            ("max_queued_per_user", "Queued jobs per user", 100000),
            ("storage_quota_mb", "Storage per user/job (MB)", 2147483647),
        ):
            field = QSpinBox()
            field.setRange(1, maximum)
            field.setValue(getattr(self.settings, key))
            self.fields[key] = field
            form.addRow(label, field)
        self.public_url = QLineEdit(self.settings.public_url)
        self.public_url.setPlaceholderText("https://server.example.ts.net")
        form.addRow("Web address (remote access)", self.public_url)
        address_help = QLabel(
            "Paste the HTTPS URL you open in your browser (shown by tailscale serve). "
            "Leave blank for local access only. Save, then choose Server > Restart."
        )
        address_help.setWordWrap(True)
        form.addRow(address_help)
        layout.addLayout(form)
        runtime = QHBoxLayout()
        self.python_path = QLineEdit(self.settings.python_executable)
        self.python_path.setPlaceholderText(
            "Automatic for Python-based MoleditPy; select Python for portable builds"
        )
        runtime.addWidget(self.python_path)
        browse = QPushButton("Browse Python...")

        def choose_python():
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Python runtime",
                self.python_path.text(),
                "Python executable (python*.exe python*);;All files (*)",
            )
            if path:
                self.python_path.setText(path)

        browse.clicked.connect(choose_python)
        runtime.addWidget(browse)
        layout.addWidget(QLabel("Daemon Python runtime"))
        layout.addLayout(runtime)
        layout.addWidget(
            QLabel(
                "Bind address is always 127.0.0.1. Save takes effect after restarting the daemon.\n"
                "Executor and approved WSL distribution/root can be set in the JSON configuration.\n"
                "Stopping or reloading the GUI preserves running jobs."
            )
        )
        button = QPushButton("Save configuration")
        button.clicked.connect(self.save_settings)
        layout.addWidget(button)
        layout.addStretch()
        self.tabs.addTab(page, "Settings")

    def task(self, call, on_ok=None, *, interactive=True, success_message=None, on_error=None):
        if self.busy:
            return
        self.busy = True
        task = Task(call)
        self._tasks.add(task)

        def finished(value):
            self._tasks.discard(task)
            self.busy = False
            if on_ok:
                on_ok(value)
            if success_message:
                QMessageBox.information(self, "Success", success_message)

        def failed(message):
            self._tasks.discard(task)
            self.busy = False
            self.message.setText(message)
            if on_error:
                on_error(message)
            if interactive:
                QMessageBox.warning(self, "Job Server", message)

        connect_queued(task.signals.finished, finished, self, (object,), cleanup=(task.signals.finished, task.signals.failed))
        connect_queued(task.signals.failed, failed, self, (str,), cleanup=(task.signals.finished, task.signals.failed))
        self.pool.start(task)

    def refresh(self):
        def work():
            health = self.client.request("GET", "/health", authenticated=False)
            if not self.client.key_path.exists():
                return {"health": health}
            return {
                "health": health,
                "resources": self.client.request("GET", "/system/resources"),
                "jobs": self.client.request("GET", "/admin/jobs"),
                "users": self.client.request("GET", "/admin/users"),
                "devices": self.client.request("GET", "/admin/devices"),
            }

        def display(data):
            self.message.setText(
                f"Running at {self.client.url} — {data['health']['server_version']}"
            )
            if "resources" not in data:
                return
            self.resources.setText(json.dumps(data["resources"]))
            usernames = {u["id"]: u["username"] for u in data["users"]}
            self.fill(
                self.jobs,
                [
                    (j["id"], usernames.get(j["owner_id"], j["owner_id"]), j["spec"]["name"], j["display_state"])
                    for j in data["jobs"]
                ],
            )
            self.fill(
                self.users,
                [(u["username"], u["role"], "Enabled" if u["enabled"] else "Disabled") for u in data["users"]],
                identifiers=[u["id"] for u in data["users"]],
            )
            self.fill(
                self.devices,
                [
                    (d["id"], usernames.get(d["user_id"], d["user_id"]),
                     "Revoked" if d["revoked_at"] is not None else "Active", self.timestamp(d["last_access"]))
                    for d in data["devices"]
                ],
            )

        self.task(work, display, interactive=False)

    @staticmethod
    def timestamp(value):
        if value is None:
            return "Never"
        try:
            return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        except (ValueError, TypeError, OSError, OverflowError):
            return "—"

    @staticmethod
    def fill(table, rows, identifiers=None):
        selected = Dashboard.selected(table)
        sorting = table.isSortingEnabled()
        signals = table.blockSignals(True)
        try:
            table.setSortingEnabled(False)
            table.clearContents()
            table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                identifier = identifiers[row] if identifiers is not None else str(values[0])
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, identifier)
                    table.setItem(row, column, item)
            table.setSortingEnabled(sorting)
            table.clearSelection()
            table.setCurrentCell(-1, -1)
            for row in range(table.rowCount()):
                if selected is not None and table.item(row, 0).data(Qt.ItemDataRole.UserRole) == selected:
                    table.setCurrentCell(row, 0)
                    break
            table.resizeColumnsToContents()
        finally:
            table.setSortingEnabled(sorting)
            table.blockSignals(signals)

    def save_settings(self):
        try:
            candidate = replace(
                self.settings,
                python_executable=self.python_path.text().strip(),
                public_url=self.public_url.text().strip(),
                **{key: field.value() for key, field in self.fields.items()},
            )
            candidate.validate().save(self.config)
            self.settings = candidate
            self.public_url.setText(candidate.public_url)
            self.message.setText(f"Saved {self.config}. Choose Server > Restart to apply.")
            return True
        except Exception as exc:
            self.message.setText(str(exc))
            return False

    def require_own_daemon(self):
        """Refuse a listener that is not the daemon holding this data directory's lock.

        Anything can answer /health on the loopback port, including a process of
        another local account that bound it first; it must never receive a password.
        """
        from ..instance import InstanceLock

        try:
            InstanceLock(self.settings.root).acquire().release()
        except RuntimeError:
            return
        raise RuntimeError(
            f"Port {self.settings.port} is answered by a process that is not this Job Server; "
            "stop it or choose another port"
        )

    def _start_daemon(self):
        self.client = JobServerClient(
            f"http://127.0.0.1:{self.settings.port}",
            self.settings.root / "identity" / "admin-device.key",
        )
        try:
            self.client.request("GET", "/health", authenticated=False)
            running = True
        except Exception:
            running = False
        if running:
            self.require_own_daemon()
            return "Daemon is already running; reusing it."
        self.settings.save(self.config)
        log_dir = self.settings.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = (
            str(Path(__file__).resolve().parents[2])
            + os.pathsep
            + environment.get("PYTHONPATH", "")
        )
        with (log_dir / "startup.log").open("ab") as log:
            from ..runtime import python_executable

            subprocess.Popen(
                [
                    python_executable(self.settings),
                    "-m",
                    "job_server",
                    "serve",
                    "--config",
                    str(self.config),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=os.name != "nt",
                creationflags=0x08000000 | 0x00000008 if os.name == "nt" else 0,
            )
        self.client = JobServerClient(
            f"http://127.0.0.1:{self.settings.port}",
            self.settings.root / "identity" / "admin-device.key",
        )
        return "Start requested; see startup.log if the health check stays offline."

    def start_daemon(self):
        def started(message):
            self.message.setText(message)
            QMessageBox.information(self, "Success", message)

        self.task(self._start_daemon, started)

    def stop_daemon(self):
        if not self.require_admin_registration():
            return
        self.task(
            lambda: self.client.request("POST", "/admin/shutdown"),
            lambda _: self.message.setText("Daemon stopping; active supervisors are preserved."),
            success_message="Shutdown requested. Active job supervisors are preserved.",
        )

    def open_web_ui(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(self.settings.public_url or self.client.url))

    def restart_daemon(self):
        if not self.require_admin_registration():
            return
        def work():
            self.client.request("POST", "/admin/shutdown")
            deadline = time.monotonic() + 20
            health_client = JobServerClient(self.client.url, timeout=2)
            while time.monotonic() < deadline:
                try:
                    health_client.request("GET", "/health", authenticated=False)
                except Exception:
                    return
                time.sleep(0.2)
            raise TimeoutError("Daemon did not stop; inspect its log before restarting")

        self.task(
            work,
            lambda _: self.start_daemon(),
        )

    def pause(self, value):
        self.task(
            lambda: self.client.request("POST", "/admin/queue/pause", {"paused": value}),
            lambda _: self.refresh(),
            success_message="Queue paused." if value else "Queue resumed.",
        )

    def credentials(self, title):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QFormLayout(dialog)
        username, password = QLineEdit(dialog), QLineEdit(dialog)
        username.setObjectName("username")
        password.setObjectName("password")
        password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Username", username)
        layout.addRow("Password", password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        username.setFocus()
        result = None
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = (username.text().strip(), password.text())
        password.clear()
        dialog.deleteLater()
        return result

    def sign_in(self):
        def signed_in(_):
            QMessageBox.information(self, "Success", "This administration window is signed in.")
            self.refresh()

        if self.client.key_path.exists():
            self.task(lambda: self.client.authenticate(), signed_in)
        else:
            credentials = self.credentials("Register administrator device")
            if credentials:

                def work():
                    self.require_own_daemon()
                    return self.client.enroll(*credentials)

                self.task(work, signed_in)

    def require_admin_registration(self):
        if self.client.key_path and self.client.key_path.is_file():
            return True
        QMessageBox.warning(
            self, "Job Server — Administrator sign-in required",
            "Use Account → Sign in / register device in this administration window before this operation.\n\n"
            "If no administrator exists yet, use Account → Create initial administrator "
            "on the server PC. This works while the daemon is running.\n\n"
            "Signing in through the browser or Job Manager does not register this window.",
        )
        return False

    def has_administrator(self):
        import sqlite3

        database = self.settings.root / "server.db"
        if not database.exists():
            return False
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            return connection.execute(
                "SELECT 1 FROM users WHERE role='admin' AND enabled=1 LIMIT 1"
            ).fetchone() is not None
        finally:
            connection.close()

    def open_setup(self):
        from .setup import SetupDialog

        if self.setup_dialog is None or not self.setup_dialog.isVisible():
            self.setup_dialog = SetupDialog(self)
        self.timer.stop()
        self.setup_dialog.show()
        self.setup_dialog.raise_()
        self.setup_dialog.activateWindow()

    def _first_setup(self):
        if not self.isVisible():
            return
        try:
            if not self.has_administrator():
                self.open_setup()
        except Exception as exc:
            self.message.setText("Unable to check setup: " + str(exc))

    def _start_and_sign_in(self, credentials=None):
        self._start_daemon()
        health = JobServerClient(f"http://127.0.0.1:{self.settings.port}", timeout=1)
        deadline = time.monotonic() + 20
        while True:
            try:
                health.request("GET", "/health", authenticated=False)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Server did not start; open File > Open Logs and check the Python runtime") from None
                time.sleep(0.2)
        self.require_own_daemon()
        if self.client.key_path.exists():
            self.client.authenticate()
        elif credentials:
            self.client.enroll(*credentials)
        else:
            raise ValueError("Sign in with the existing administrator username and password")
        me = self.client.request("GET", "/me")
        if me["role"] != "admin":
            raise ValueError("An administrator account is required for this window")

    def create_administrator(self, credentials):
        from ..auth import Auth
        from ..db import Database

        db = Database(self.settings.root)
        try:
            Auth(db, self.settings).create_initial_admin(*credentials)
        finally:
            db.close()

    def create_and_sign_in(self, credentials):
        self.create_administrator(credentials)
        self._start_and_sign_in(credentials)

    def bootstrap(self):
        credentials = self.credentials("Create administrator, start server and sign in")
        if not credentials:
            return

        def created(_):
            self.message.setText("Administrator created. This window is signed in.")
            self.refresh()

        self.task(
            lambda: self.create_and_sign_in(credentials), created,
            success_message="Administrator created. The server is running and this window is signed in.",
        )

    def add_user(self):
        if not self.require_admin_registration():
            return
        credentials = self.credentials("Add user")
        if credentials:
            self.task(
                lambda: self.client.request(
                    "POST", "/admin/users", {"username": credentials[0], "password": credentials[1]}
                ),
                lambda _: self.refresh(),
                success_message="User created.",
            )

    @staticmethod
    def selected(table):
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        identifier = item.data(Qt.ItemDataRole.UserRole)
        return identifier if identifier is not None else item.text()

    def cancel(self):
        job_id = self.selected(self.jobs)
        if job_id:
            self.task(
                lambda: self.client.request("POST", f"/jobs/{job_id}/cancel"),
                lambda _: self.refresh(),
                success_message="Job cancellation requested.",
            )

    def disable_user(self):
        user = self.selected(self.users)
        if (
            user
            and QMessageBox.question(self, "Disable user", "Disable this user and revoke access?")
            == QMessageBox.StandardButton.Yes
        ):
            self.task(
                lambda: self.client.request("POST", f"/admin/users/{user}/disable"),
                lambda _: self.refresh(),
                success_message="User disabled.",
            )

    def revoke_device(self):
        device = self.selected(self.devices)
        if (
            device
            and QMessageBox.question(
                self, "Revoke device", "Revoke this device key and its active tokens?"
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.task(
                lambda: self.client.request("POST", f"/admin/devices/{device}/revoke"),
                lambda _: self.refresh(),
                success_message="Device revoked.",
            )

    def closeEvent(self, event):
        if self.setup_dialog is not None:
            self.setup_dialog.close()
        self.timer.stop()
        # Window disposal never shuts down the separately owned daemon.
        super().closeEvent(event)

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)
        if not self._setup_checked:
            self._setup_checked = True
            QTimer.singleShot(0, self._first_setup)
