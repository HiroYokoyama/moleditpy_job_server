
from ._qt_callbacks import connect_queued
import json
import os
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QIcon
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
        self.setWindowTitle("Job Server — LAN computational helper")
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
        layout = QVBoxLayout(self)
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

    def _button(self, text, callback, row):
        button = QPushButton(text)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def _dashboard(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self._button("Start", self.start_daemon, row)
        self._button("Stop (preserve jobs)", self.stop_daemon, row)
        self._button("Restart", self.restart_daemon, row)
        self._button("Sign in / register device", self.sign_in, row)
        self._button("Refresh", self.refresh, row)
        self._button("Open Web UI", self.open_web_ui, row)
        layout.addLayout(row)
        row = QHBoxLayout()
        self._button("Pause queue", lambda: self.pause(True), row)
        self._button("Resume queue", lambda: self.pause(False), row)
        self._button("Cancel selected job", self.cancel, row)
        self._button(
            "Copy Tailscale Serve command",
            lambda: QApplication.clipboard().setText(
                f"tailscale serve --bg http://127.0.0.1:{self.settings.port}"
            ),
            row,
        )
        layout.addLayout(row)
        self.resources = QLabel("Sign in to view resources and jobs.")
        layout.addWidget(self.resources)
        self.jobs = QTableWidget(0, 4)
        self.jobs.setHorizontalHeaderLabels(["ID", "Owner", "Name", "State"])
        self.jobs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.jobs)
        self.tabs.addTab(page, "Dashboard")

    def _users(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self._button("Create initial administrator", self.bootstrap, row)
        self._button("Add user", self.add_user, row)
        self._button("Disable selected user", self.disable_user, row)
        self._button("Revoke selected device", self.revoke_device, row)
        layout.addLayout(row)
        self.users = QTableWidget(0, 4)
        self.users.setHorizontalHeaderLabels(["ID", "Username", "Role", "Enabled"])
        self.users.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.users)
        self.devices = QTableWidget(0, 4)
        self.devices.setHorizontalHeaderLabels(["ID", "User", "Revoked", "Last access"])
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

    def task(self, call, on_ok=None, *, interactive=True, success_message=None):
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
            self.fill(
                self.jobs,
                [
                    (j["id"], j["owner_id"], j["spec"]["name"], j["display_state"])
                    for j in data["jobs"]
                ],
            )
            self.fill(
                self.users,
                [(u["id"], u["username"], u["role"], u["enabled"]) for u in data["users"]],
            )
            self.fill(
                self.devices,
                [
                    (d["id"], d["user_id"], d["revoked_at"], d["last_access"])
                    for d in data["devices"]
                ],
            )

        self.task(work, display, interactive=False)

    @staticmethod
    def fill(table, rows):
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        table.resizeColumnsToContents()

    def save_settings(self):
        try:
            self.settings.python_executable = self.python_path.text().strip()
            for key, field in self.fields.items():
                setattr(self.settings, key, field.value())
            self.settings.validate().save(self.config)
            self.message.setText(f"Saved {self.config}. Restart to apply.")
        except Exception as exc:
            self.message.setText(str(exc))

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

    def start_daemon(self):
        def work():
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

        def started(message):
            self.message.setText(message)
            QMessageBox.information(self, "Success", message)

        self.task(work, started)

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
        layout.addRow("Password (12+ characters)", password)
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
            "Use Sign in / register device in this administration window before this operation.\n\n"
            "If no administrator exists yet, use Users & Devices → Create initial administrator "
            "on the server PC. This works while the daemon is running.\n\n"
            "Signing in through the browser or Job Manager does not register this window.",
        )
        return False

    def bootstrap(self):
        credentials = self.credentials("Create initial administrator on this server PC")
        if not credentials:
            return

        def work():
            from ..auth import Auth
            from ..db import Database
            db = Database(self.settings.root)
            try:
                return Auth(db, self.settings).create_initial_admin(*credentials)
            finally:
                db.close()

        def created(_):
            message = (
                "Administrator created. Start the server if needed, then use "
                "Sign in / register device in this window with the new administrator account."
            )
            self.message.setText(message)
            QMessageBox.information(self, "Success", message)

        self.task(
            work,
            created,
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

    def selected(self, table):
        row = table.currentRow()
        return table.item(row, 0).text() if row >= 0 and table.item(row, 0) else None

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
        self.timer.stop()
        # Window disposal never shuts down the separately owned daemon.
        super().closeEvent(event)

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)
