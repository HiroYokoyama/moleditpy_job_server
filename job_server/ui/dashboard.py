
from ._qt_callbacks import connect_queued
import os
import subprocess
import time
from datetime import datetime
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt
from PySide6.QtGui import QIcon, QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMessageBox,
    QMenuBar,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QAbstractItemView,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import JobServerClient
from .units import GBSpinBox, gb
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


#: Banner colours per server status: indicator dot and a translucent tint that
#: reads on both light and dark palettes.
STATUS_COLORS = {
    "online": ("#2e7d32", "rgba(46, 125, 50, 0.14)"),
    "warning": ("#e08600", "rgba(224, 134, 0, 0.16)"),
    "offline": ("#c62828", "rgba(198, 40, 40, 0.14)"),
    "unknown": ("#757575", "rgba(117, 117, 117, 0.14)"),
}

#: Job state colours in the queue table.
STATE_COLORS = {
    "RUNNING": "#2e7d32",
    "PENDING": "#e08600",
    "QUEUED": "#e08600",
    "BLOCKED": "#e08600",
    "FAILED": "#c62828",
    "CANCELLED": "#757575",
    "CANCELED": "#757575",
}


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
        self.detail_dialog = None
        self.job_records = {}
        self.usernames = {}
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
        queue.addSeparator()
        self.detail_action = action(queue, "Job details...", self.open_job_details)
        self.priority_action = action(queue, "Set priority...", self.set_priority)
        self.force_action = action(queue, "Force run selected job...", lambda: self.force_run(True))
        self.unforce_action = action(queue, "Cancel force-run request", lambda: self.force_run(False))
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
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(self._status_banner())
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metric_values = {}
        self.metric_bars = {}
        for key, title in (("running", "Running jobs"), ("pending", "Waiting jobs"),
                           ("cpu", "CPU reserved / budget"), ("memory", "Memory reserved / budget")):
            card = QGroupBox(title)
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            content = QVBoxLayout(card)
            value = QLabel("—")
            font = value.font()
            font.setPointSize(font.pointSize() + 6)
            font.setBold(True)
            value.setFont(font)
            content.addWidget(value)
            self.metric_values[key] = value
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setRange(0, 100)
            content.addWidget(bar)
            if key in ("cpu", "memory"):
                self.metric_bars[key] = bar
                self._set_usage(key, None, None)
            else:
                # Count cards keep the bar's space so all four cards line up.
                policy = bar.sizePolicy()
                policy.setRetainSizeWhenHidden(True)
                bar.setSizePolicy(policy)
                bar.hide()
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)
        layout.addWidget(QLabel("Jobs — select a row, then use the Queue menu for actions."))
        self.jobs = QTableWidget(0, 5)
        self.jobs.setHorizontalHeaderLabels(["Name", "Owner", "State", "Priority", "Job ID"])
        self.jobs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.jobs.verticalHeader().setVisible(False)
        self.jobs.verticalHeader().setDefaultSectionSize(34)
        self._table_layout(self.jobs, (220, 110, 110, 70, 270))
        self.jobs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.jobs.setShowGrid(False)
        self.jobs.itemSelectionChanged.connect(self._update_job_actions)
        self.jobs.cellDoubleClicked.connect(lambda *_: self.open_job_details())
        self._update_job_actions()
        layout.addWidget(self.jobs)
        self.tabs.addTab(page, "Dashboard")

    def _status_banner(self):
        banner = QFrame()
        banner.setObjectName("statusBanner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(16, 16)
        row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.connection_status = QLabel()
        font = self.connection_status.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        self.connection_status.setFont(font)
        text.addWidget(self.connection_status)
        self.status_detail = QLabel()
        self.status_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addWidget(self.status_detail)
        self.resources = QLabel()
        self.resources.setWordWrap(True)
        text.addWidget(self.resources)
        row.addLayout(text, 1)
        self.status_checked = QLabel()
        row.addWidget(self.status_checked, 0, Qt.AlignmentFlag.AlignTop)
        self.status_banner = banner
        self.set_status("unknown", "Checking server…", "Waiting for the first health check.")
        self.status_checked.clear()
        return banner

    def set_status(self, level, headline, hint, version=None):
        """Show the server state as a coloured banner with its address and next step."""
        dot, tint = STATUS_COLORS[level]
        self.status_level = level
        self.status_banner.setStyleSheet(
            f"#statusBanner {{ background: {tint}; border: 1px solid {dot}; border-radius: 8px; }}"
        )
        self.status_dot.setStyleSheet(f"background: {dot}; border-radius: 8px;")
        self.connection_status.setText(headline)
        address = self.client.url
        if self.settings.public_url:
            address += "  ·  Web " + self.settings.public_url
        if version:
            address += "  ·  v" + version
        self.status_detail.setText(address)
        self.resources.setText(hint)
        self.status_checked.setText("Checked " + datetime.now().strftime("%H:%M:%S"))

    def _users(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.users = QTableWidget(0, 3)
        self.users.setHorizontalHeaderLabels(["Username", "Role", "Status"])
        self.users.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table_layout(self.users, (250, 120, 120))
        layout.addWidget(self.users)
        self.devices = QTableWidget(0, 4)
        self.devices.setHorizontalHeaderLabels(["Device ID", "Username", "Status", "Last access (local)"])
        self.devices.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table_layout(self.devices, (270, 130, 100, 230))
        layout.addWidget(self.devices)
        # "&&" shows a literal ampersand; a single "&" is taken as a mnemonic.
        self.tabs.addTab(page, "Users && Devices")

    @staticmethod
    def _table_layout(table, widths):
        table.setWordWrap(False)
        table.horizontalHeader().setHighlightSections(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate(widths):
            table.horizontalHeader().resizeSection(column, width)

    def _settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.fields = {}
        for key, label, maximum in (
            ("port", "Private loopback port", 65535),
            ("cpus", "CPU budget", 4096),
            ("memory_mb", "Memory budget", 2147483647),
            ("max_active_per_user", "Active jobs per user", 10000),
            ("max_queued_per_user", "Queued jobs per user", 100000),
            ("storage_quota_mb", "Storage per user/job", 2147483647),
        ):
            if key.endswith("_mb"):
                field = GBSpinBox(maximum)
                field.set_mb(getattr(self.settings, key))
            else:
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
            try:
                return {
                    "health": health,
                    "resources": self.client.request("GET", "/system/resources"),
                    "jobs": self.client.request("GET", "/admin/jobs"),
                    "users": self.client.request("GET", "/admin/users"),
                    "devices": self.client.request("GET", "/admin/devices"),
                }
            except Exception as exc:
                # /health answered, so the daemon is online; only the signed-in calls failed.
                return {"health": health, "error": str(exc)}

        def display(data):
            version = data["health"]["server_version"]
            self.message.setText(f"Running at {self.client.url} — {version}")
            if "resources" not in data:
                self._clear_dashboard()
                if "error" in data:
                    self.set_status(
                        "warning", "Server online — sign-in failed",
                        data["error"] + " Use Account > Sign in / register device.", version,
                    )
                else:
                    self.set_status(
                        "warning", "Server online — not signed in",
                        "Use Account > Sign in / register device to view resources and jobs.", version,
                    )
                return
            resource = data["resources"]
            if resource.get("paused"):
                self.set_status(
                    "warning", "Server online — queue paused",
                    "Waiting jobs do not start until Queue > Resume queue. Running jobs continue.",
                    version,
                )
            else:
                self.set_status(
                    "online", "Server online — accepting jobs",
                    "CPU and memory reservations are advisory.", version,
                )
            for key in ("running", "pending"):
                self.metric_values[key].setText(str(resource.get(key, "—")))
            self.metric_values["cpu"].setText(
                f"{resource.get('reserved_cpus', '—')} / {resource.get('cpus', '—')}"
            )
            self.metric_values["memory"].setText(
                f"{gb(resource.get('reserved_memory_mb'))} / {gb(resource.get('memory_mb'))} GB"
            )
            self._set_usage("cpu", resource.get("reserved_cpus"), resource.get("cpus"))
            self._set_usage("memory", resource.get("reserved_memory_mb"), resource.get("memory_mb"))
            usernames = {u["id"]: u["username"] for u in data["users"]}
            self.usernames = usernames
            self.job_records = {job["id"]: job for job in data["jobs"]}
            self.fill(
                self.jobs,
                [
                    (j["spec"]["name"], usernames.get(j["owner_id"], j["owner_id"]),
                     j["display_state"] + (" · force requested" if j.get("force_run") else ""),
                     j.get("priority", 0), j["id"])
                    for j in data["jobs"]
                ], identifiers=[j["id"] for j in data["jobs"]],
            )
            self._color_states()
            self._update_job_actions()
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

        def unavailable(_):
            self.set_status(
                "offline", "Server offline",
                "No daemon answers on this port. Choose Server > Start; "
                "if it stays offline, check File > Open Logs.",
            )
            self._clear_dashboard()

        self.task(work, display, interactive=False, on_error=unavailable)

    def _clear_dashboard(self):
        self.job_records = {}
        self.usernames = {}
        for value in self.metric_values.values():
            value.setText("—")
        for key in self.metric_bars:
            self._set_usage(key, None, None)
        for table in (self.jobs, self.users, self.devices):
            self.fill(table, [])
        self._update_job_actions()
        if self.detail_dialog is not None:
            self.detail_dialog.invalidate("Connection unavailable. Sign in and refresh to view this job.")

    def _set_usage(self, key, used, budget):
        try:
            percent = max(0, min(100, round(100 * used / budget)))
        except (TypeError, ZeroDivisionError):
            percent = 0
        color = "#c62828" if percent >= 90 else "#e08600" if percent >= 70 else "#2e7d32"
        bar = self.metric_bars[key]
        bar.setValue(percent)
        bar.setStyleSheet(
            "QProgressBar { border: none; border-radius: 3px; background: rgba(127, 127, 127, 0.25); }"
            f"QProgressBar::chunk {{ border-radius: 3px; background: {color}; }}"
        )

    def _color_states(self):
        for row in range(self.jobs.rowCount()):
            item = self.jobs.item(row, 2)
            color = STATE_COLORS.get(item.text().split(" ", 1)[0]) if item else None
            if color:
                item.setForeground(QBrush(QColor(color)))
                font = item.font()
                font.setBold(True)
                item.setFont(font)

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
        finally:
            table.setSortingEnabled(sorting)
            table.blockSignals(signals)

    def save_settings(self):
        try:
            candidate = replace(
                self.settings,
                python_executable=self.python_path.text().strip(),
                public_url=self.public_url.text().strip(),
                **{key: field.mb() if isinstance(field, GBSpinBox) else field.value()
                   for key, field in self.fields.items()},
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
        from ..daemon_ownership import verify_local_daemon

        verify_local_daemon(self.settings.root, self.settings.port)

    def _start_daemon(self):
        url = f"http://127.0.0.1:{self.settings.port}"
        if self.client.url != url:
            self.client = JobServerClient(
                url, self.settings.root / "identity" / "admin-device.key",
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

    def _update_job_actions(self):
        identifier = self.selected(self.jobs)
        record = self.job_records.get(identifier, {})
        pending = record.get("state", record.get("display_state")) in {"PENDING", "QUEUED", "BLOCKED"}
        self.detail_action.setEnabled(identifier is not None)
        self.priority_action.setEnabled(pending)
        self.force_action.setEnabled(pending and not record.get("force_run", False))
        self.unforce_action.setEnabled(pending and record.get("force_run", False))

    def open_job_details(self):
        identifier = self.selected(self.jobs)
        if identifier is None or not self.require_admin_registration():
            return
        from .job_detail import JobDetailDialog

        if self.detail_dialog is None:
            self.detail_dialog = JobDetailDialog(self)
        record = self.job_records.get(identifier, {})
        owner = self.usernames.get(record.get("owner_id"), record.get("owner_id", "—"))
        self.detail_dialog.show_job(identifier, owner)

    def set_priority(self):
        identifier = self.selected(self.jobs)
        if identifier is None or not self.require_admin_registration():
            return
        value, accepted = QInputDialog.getInt(
            self, "Set job priority", "Higher numbers run first; equal priorities keep submission order.",
            self.job_records.get(identifier, {}).get("priority", 0), -1000, 1000,
        )
        if accepted:
            self.task(
                lambda: self.client.request("POST", f"/admin/jobs/{identifier}/priority", {"priority": value}),
                lambda _: self.refresh(),
                success_message="Job priority updated.",
            )

    def force_run(self, value=True):
        identifier = self.selected(self.jobs)
        if identifier is None or not self.require_admin_registration():
            return
        if value and QMessageBox.question(
            self, "Force run selected job",
            "Start this waiting job ahead of queue order, even while paused, and override CPU, "
            "memory and active-job limits? Existing jobs keep running. Dependencies still apply. "
            "Overcommitting memory can cause jobs to fail.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.task(
            lambda: self.client.request("POST", f"/admin/jobs/{identifier}/force-run", {"force_run": value}),
            lambda _: self.refresh(),
            success_message="Force-run requested; dependencies still apply." if value else "Force-run request cancelled.",
        )

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
        if self.detail_dialog is not None:
            self.detail_dialog.close()
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
