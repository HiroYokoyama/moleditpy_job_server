import json
import os
import subprocess
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
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
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


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
        self.setWindowTitle("Job Server — Trusted Lab Preview")
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
        self.busy = False
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Trusted users only. Jobs share the service OS account; CPU/RAM reservations are advisory.\n"
            "Use a low-privilege account and HTTPS through Tailscale Serve or a LAN reverse proxy."
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

    def task(self, call, on_ok=None):
        if self.busy:
            return
        self.busy = True
        task = Task(call)

        def finished(value):
            self.busy = False
            if on_ok:
                on_ok(value)

        def failed(message):
            self.busy = False
            self.message.setText(message)

        task.signals.finished.connect(finished)
        task.signals.failed.connect(failed)
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

        self.task(work, display)

    @staticmethod
    def fill(table, rows):
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        table.resizeColumnsToContents()

    def save_settings(self):
        try:
            for key, field in self.fields.items():
                setattr(self.settings, key, field.value())
            self.settings.validate().save(self.config)
            self.message.setText(f"Saved {self.config}. Restart to apply.")
        except Exception as exc:
            self.message.setText(str(exc))

    def start_daemon(self):
        def work():
            try:
                self.client.request("GET", "/health", authenticated=False)
                return "Daemon is already running; reusing it."
            except Exception:
                pass
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
            return "Start requested; see startup.log if the health check stays offline."

        self.task(work, self.message.setText)

    def stop_daemon(self):
        self.task(
            lambda: self.client.request("POST", "/admin/shutdown"),
            lambda _: self.message.setText("Daemon stopping; active supervisors are preserved."),
        )

    def restart_daemon(self):
        self.task(
            lambda: self.client.request("POST", "/admin/shutdown"),
            lambda _: QTimer.singleShot(1500, self.start_daemon),
        )

    def pause(self, value):
        self.task(
            lambda: self.client.request("POST", "/admin/queue/pause", {"paused": value}),
            lambda _: self.refresh(),
        )

    def credentials(self, title):
        username, ok = QInputDialog.getText(self, title, "Username")
        if not ok:
            return None
        password, ok = QInputDialog.getText(
            self, title, "Password (12+ characters)", QLineEdit.EchoMode.Password
        )
        return (username, password) if ok else None

    def sign_in(self):
        if self.client.key_path.exists():
            self.task(lambda: self.client.authenticate(), lambda _: self.refresh())
        else:
            credentials = self.credentials("Register administrator device")
            if credentials:
                self.task(lambda: self.client.enroll(*credentials), lambda _: self.refresh())

    def bootstrap(self):
        credentials = self.credentials("Initial administrator (daemon must be stopped)")
        if not credentials:
            return

        def work():
            from ..auth import Auth
            from ..db import Database
            from ..instance import InstanceLock

            lock = InstanceLock(self.settings.root).acquire()
            db = Database(self.settings.root)
            try:
                if db.one("SELECT 1 FROM users WHERE role='admin' AND enabled=1"):
                    raise ValueError("Initial administrator already exists")
                return Auth(db, self.settings).create_user(*credentials, role="admin")
            finally:
                db.close()
                lock.release()

        self.task(
            work,
            lambda _: self.message.setText(
                "Administrator created. Start server and register this device."
            ),
        )

    def add_user(self):
        credentials = self.credentials("Add user")
        if credentials:
            self.task(
                lambda: self.client.request(
                    "POST", "/admin/users", {"username": credentials[0], "password": credentials[1]}
                ),
                lambda _: self.refresh(),
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
            )

    def closeEvent(self, event):
        self.timer.stop()
        # Window disposal never shuts down the separately owned daemon.
        super().closeEvent(event)
