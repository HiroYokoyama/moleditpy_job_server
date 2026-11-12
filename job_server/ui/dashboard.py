
from ._qt_callbacks import connect_queued
from .meters import DEFAULT_SPAN, SPANS, Gauge, Trend, percent
import json
import logging
import os
import sqlite3
import subprocess
import time
import uuid
from collections import deque
from urllib.parse import urlsplit
from urllib.error import URLError
from datetime import datetime
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt
from PySide6.QtGui import QIcon, QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QMenu,
    QPlainTextEdit,
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QAbstractItemView,
    QTabWidget,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import ClientError, JobServerClient
from .units import GBSpinBox, FocusSpinBox, gb
from .network import LanAddressList
from ..settings import Settings, pc_resources, pc_size_label


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


# Past a limit, such as CPU use beyond every core into hyper-threads: beyond red.
PAST_LIMIT = "#7b1fa2"

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


TERMINAL_STATES = {"DONE", "FAILED", "CANCELLED", "CANCELED", "LOST"}
JOB_PAGE = 100
EVENT_LIMIT = 200
#: The queue filter's choices and the job states each one shows.
JOB_FILTERS = {
    "All jobs": None,
    "Running": {"RUNNING"},
    "Waiting": {"PENDING", "QUEUED", "BLOCKED", "UPLOADING"},
    "Finished": {"DONE"},
    "Failed or cancelled": {"FAILED", "CANCELLED", "CANCELED", "LOST"},
}
FILE_MANAGER = "File Explorer" if os.name == "nt" else "the file manager"


class Dashboard(QDialog):
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("Job Server")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1] / "web" / "icon.png")))
        self.resize(1000, 780)
        self.config = Path(config) if config else Settings().root / "settings.json"
        if self.config.exists():
            self.settings = Settings.load(self.config)
        else:
            cpus, memory_mb = pc_resources()
            self.settings = Settings(cpus=cpus, memory_mb=memory_mb)
        self.client = JobServerClient(
            f"http://127.0.0.1:{self.settings.port}",
            self.settings.root / "identity" / "admin-device.key", timeout=3,
        )
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._tasks = set()
        self.busy = False
        self._pending_tasks = deque()
        self._pending_lifecycle = set()
        # A save changed the file under a running daemon and no start followed.
        self._unapplied = False
        self._active_lifecycle = None
        self.setup_dialog = None
        self._setup_checked = False
        self.detail_dialog = None
        self.history_rows = deque()
        self.span = DEFAULT_SPAN
        self.job_records = {}
        self.usernames = {}
        self.user_records = {}
        self.device_records = {}
        self.me = None
        self.own_device = None
        self.job_page = 0
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
        self._activity()
        self._settings()
        self.tabs.currentChanged.connect(lambda _: self._refresh_activity())
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
        self.start_action = action(server, "Start", self.start_daemon)
        self.stop_action = action(server, "Stop (preserve jobs)", self.stop_daemon)
        self.restart_action = action(server, "Restart", self.restart_daemon)
        server.addSeparator()
        action(server, "Refresh", self.refresh)
        action(server, "Copy connection addresses", self.copy_connection_addresses)
        action(server, "Copy Tailscale Serve command", lambda: QApplication.clipboard().setText(
            f"tailscale serve --bg http://127.0.0.1:{self.settings.port}"))
        account = self.menu_bar.addMenu("Account")
        self.sign_in_action = action(account, "Sign in / register device...", self.sign_in)
        self.change_password_action = action(account, "Change my password...", self.change_password)
        account.addSeparator()
        action(account, "Create initial administrator...", self.bootstrap)
        self.add_user_action = action(account, "Add user or administrator...", self.add_user)
        self.reset_password_action = action(account, "Reset selected user's password...", self.reset_password)
        self.toggle_user_action = action(account, "Disable selected user...", self.toggle_user)
        self.toggle_role_action = action(account, "Make selected user an administrator...", self.toggle_role)
        self.delete_user_action = action(account, "Delete selected user...", self.delete_user)
        self.copy_user_action = action(account, "Copy username", self.copy_username)
        account.addSeparator()
        self.revoke_device_action = action(account, "Revoke selected device...", self.revoke_device)
        self.copy_device_action = action(account, "Copy device ID", self.copy_device_id)
        queue = self.menu_bar.addMenu("Queue")
        action(queue, "Pause queue", lambda: self.pause(True))
        action(queue, "Resume queue", lambda: self.pause(False))
        queue.addSeparator()
        self.detail_action = action(queue, "Job details...", self.open_job_details)
        self.folder_action = action(queue, f"Open job folder in {FILE_MANAGER}", self.open_job_folder)
        self.copy_job_action = action(queue, "Copy job ID", self.copy_job_id)
        queue.addSeparator()
        self.priority_action = action(queue, "Set priority...", self.set_priority)
        self.force_action = action(queue, "Force run selected job...", lambda: self.force_run(True))
        self.unforce_action = action(queue, "Cancel force-run request", lambda: self.force_run(False))
        self.cancel_action = action(queue, "Cancel selected job...", self.cancel)
        self.lost_action = action(queue, "Mark stuck job as lost...", self.mark_lost)

    def copy_connection_addresses(self):
        urls = self.settings.connection_urls or [f"http://127.0.0.1:{self.settings.port}"]
        QApplication.clipboard().setText("\n".join(urls))
        self.message.setText(f"Copied {len(urls)} connection address(es). No credentials included.")

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
        metrics = QGridLayout()
        metrics.setSpacing(12)
        self.metric_values = {}
        self.metric_bars = {}
        self.metric_trends = {}
        for index, (key, title) in enumerate((
            ("running", "Running jobs"), ("cpu_usage", "CPU in use now (whole PC)"),
            ("cpu", "CPU cores reserved / budget"), ("pending", "Waiting jobs"),
            ("memory_usage", "Memory in use now (whole PC, usable)"), ("memory", "Memory reserved / budget"),
        )):
            card = QGroupBox(title)
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            content = QHBoxLayout(card)
            # The plot runs up behind the figure: the number reads against its own history.
            column = QGridLayout()
            value = QLabel("—")
            value.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            # A veil in the card's own colour keeps the figure readable where the line crosses it.
            veil = value.palette().window().color()
            value.setStyleSheet(
                f"background: rgba({veil.red()}, {veil.green()}, {veil.blue()}, 0.78);"
                " border-radius: 4px; padding: 0 5px 1px 2px;"
            )
            font = value.font()
            font.setPointSize(font.pointSize() + 6)
            font.setBold(True)
            value.setFont(font)
            self.metric_values[key] = value
            share = key in ("cpu", "memory", "cpu_usage", "memory_usage")
            # Recent history under each figure, so a spike between glances still shows.
            trend = Trend("%" if share else "", 100 if share else None)
            column.addWidget(trend, 0, 0)
            column.addWidget(value, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.metric_trends[key] = trend
            if share:
                gauge = Gauge()
                content.addWidget(gauge)
                self.metric_bars[key] = gauge
                self._set_usage(key, None, None)
            else:
                content.addStrut(Gauge().height())  # Count cards line up with dial cards.
            content.addLayout(column, 1)
            metrics.addWidget(card, index // 3, index % 3)
        layout.addLayout(metrics)
        under = QHBoxLayout()
        note = QLabel("Point at a line to read its value. Gaps are times the server was not running.")
        note.setEnabled(False)
        under.addWidget(note, 1)
        # The span the lines cover; the server keeps the longest, so switching is instant.
        self.span_buttons = QButtonGroup(self)
        for seconds, label in SPANS:
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked((seconds, label) == DEFAULT_SPAN)
            button.setAutoRaise(True)
            button.clicked.connect(lambda _=False, span=(seconds, label): self._set_span(span))
            self.span_buttons.addButton(button)
            under.addWidget(button)
        layout.addLayout(under)
        filters = QHBoxLayout()
        self.job_caption = QLabel("Jobs — right-click for actions")
        filters.addWidget(self.job_caption, 1)
        self.job_search = QLineEdit()
        self.job_search.setPlaceholderText("Filter by name, owner or ID")
        self.job_search.setClearButtonEnabled(True)
        self.job_search.setMinimumWidth(210)
        self.job_search.textChanged.connect(lambda _: self._apply_job_filter())
        filters.addWidget(self.job_search)
        self.job_filter = QComboBox()
        self.job_filter.addItems(JOB_FILTERS)
        self.job_filter.currentIndexChanged.connect(lambda _: self._apply_job_filter())
        filters.addWidget(self.job_filter)
        self.newer_jobs = QPushButton("Newer")
        self.newer_jobs.clicked.connect(lambda: self._job_page(-1))
        filters.addWidget(self.newer_jobs)
        self.older_jobs = QPushButton("Older")
        self.older_jobs.clicked.connect(lambda: self._job_page(1))
        filters.addWidget(self.older_jobs)
        self.newer_jobs.setEnabled(False)
        self.older_jobs.setEnabled(False)
        layout.addLayout(filters)
        self.jobs = QTableWidget(0, 6)
        self.jobs.setHorizontalHeaderLabels(["Name", "Owner", "State", "Priority", "Job ID", "Submitted (local)"])
        self.jobs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.jobs.verticalHeader().setVisible(False)
        self.jobs.verticalHeader().setDefaultSectionSize(34)
        self._table_layout(self.jobs, (220, 110, 110, 70, 270, 170))
        self.jobs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.jobs.setShowGrid(False)
        self.jobs.itemSelectionChanged.connect(self._update_job_actions)
        self.jobs.cellDoubleClicked.connect(lambda *_: self.open_job_details())
        self._context_menu(self.jobs)
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
        if self.settings.connection_urls:
            address += "  ·  Web " + " · ".join(self.settings.connection_urls)
        if version:
            address += "  ·  v" + version
        self.status_detail.setText(address)
        self.resources.setText(hint)
        self.status_checked.setText("Checked " + datetime.now().strftime("%H:%M:%S"))

    def _users(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        identity = QHBoxLayout()
        self.account_label = QLabel("Not signed in.")
        identity.addWidget(self.account_label, 1)
        identity.addWidget(self._action_button(self.change_password_action, "Change my password..."))
        layout.addLayout(identity)
        users_box = QGroupBox("Users — right-click a user for actions")
        users_layout = QVBoxLayout(users_box)
        self.users = QTableWidget(0, 5)
        self.users.setHorizontalHeaderLabels(["Username", "Role", "Status", "Active devices", "Created (local)"])
        self._table_layout(self.users, (230, 110, 90, 100, 200))
        users_layout.addWidget(self.users)
        user_buttons = QHBoxLayout()
        for item, text in (
            (self.add_user_action, "Add user..."),
            (self.reset_password_action, "Reset password..."),
            (self.toggle_user_action, None),
            (self.toggle_role_action, None),
            (self.delete_user_action, "Delete user..."),
        ):
            user_buttons.addWidget(self._action_button(item, text))
        user_buttons.addStretch()
        users_layout.addLayout(user_buttons)
        layout.addWidget(users_box, 1)
        devices_box = QGroupBox("Registered devices — right-click a device for actions")
        devices_layout = QVBoxLayout(devices_box)
        self.devices = QTableWidget(0, 5)
        self.devices.setHorizontalHeaderLabels(
            ["Device ID", "Username", "Status", "Last access (local)", "Registered (local)"]
        )
        self._table_layout(self.devices, (270, 130, 130, 200, 200))
        devices_layout.addWidget(self.devices)
        device_buttons = QHBoxLayout()
        device_buttons.addWidget(self._action_button(self.revoke_device_action, "Revoke device..."))
        device_buttons.addStretch()
        devices_layout.addLayout(device_buttons)
        layout.addWidget(devices_box, 1)
        for table in (self.users, self.devices):
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.verticalHeader().setVisible(False)
            table.itemSelectionChanged.connect(self._update_account_actions)
            self._context_menu(table)
        self._update_account_actions()
        # "&&" shows a literal ampersand; a single "&" is taken as a mnemonic.
        self.tabs.addTab(page, "Users && Devices")

    def _action_button(self, item, text=None):
        """A button that follows a menu action's enabled state and, without `text`, its label."""
        button = QPushButton(text or item.text())
        button.clicked.connect(lambda: item.trigger())
        button.setEnabled(item.isEnabled())

        def follow():
            button.setEnabled(item.isEnabled())
            if text is None:
                button.setText(item.text().replace(" selected user", ""))
            button.setToolTip(item.toolTip())

        item.changed.connect(follow)
        follow()
        return button

    def _activity(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            f"The latest {EVENT_LIMIT} recorded actions, newest first. Passwords are never recorded."
        ))
        self.activity = QTableWidget(0, 4)
        self.activity.setHorizontalHeaderLabels(["Time (local)", "Action", "By", "Details"])
        self.activity.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.activity.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.activity.verticalHeader().setVisible(False)
        self._table_layout(self.activity, (210, 190, 130, 380))
        layout.addWidget(self.activity)
        self.tabs.addTab(page, "Activity")

    def _context_actions(self, table):
        """What a right-click offers for the current row; None draws a separator."""
        if table is self.jobs:
            return [
                self.detail_action, self.folder_action, self.copy_job_action, None,
                self.priority_action, self.force_action, self.unforce_action, None,
                self.cancel_action, self.lost_action,
            ]
        if table is self.users:
            return [
                self.add_user_action, None, self.reset_password_action, self.toggle_user_action,
                self.toggle_role_action, None, self.copy_user_action, None,
                self.delete_user_action,
            ]
        return [self.revoke_device_action, None, self.copy_device_action]

    def _context_menu(self, table):
        def show(position):
            item = table.itemAt(position)
            if item is not None:
                table.setCurrentCell(item.row(), item.column())
            else:
                table.setCurrentCell(-1, -1)
                table.clearSelection()
            self._update_job_actions()
            self._update_account_actions()
            menu = QMenu(self)
            for entry in self._context_actions(table):
                if entry is None:
                    menu.addSeparator()
                else:
                    menu.addAction(entry)
            menu.exec(table.viewport().mapToGlobal(position))
            menu.deleteLater()

        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(show)

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
        self.fields = {}

        def number(key, maximum):
            if key.endswith("_mb"):
                field = GBSpinBox(maximum)
                field.set_mb(getattr(self.settings, key))
            else:
                field = FocusSpinBox()
                field.setRange(1, maximum)
                field.setValue(getattr(self.settings, key))
            self.fields[key] = field
            return field

        network = QGroupBox("Network")
        form = QFormLayout(network)
        form.addRow("Local administration port (HTTP)", number("port", 65535))
        self.lan_tls = QCheckBox("Enable local HTTPS with a persistent local CA")
        self.lan_tls.setChecked(self.settings.lan_tls)
        form.addRow(self.lan_tls)
        form.addRow("HTTPS port", number("tls_port", 65535))
        self.ca_button = QPushButton("Create / show CA certificate...")
        self.ca_button.clicked.connect(self.show_local_ca)
        form.addRow("", self.ca_button)
        self.regenerate_ca_button = QPushButton("Regenerate CA certificate...")
        self.regenerate_ca_button.clicked.connect(self.regenerate_ca)
        form.addRow("", self.regenerate_ca_button)
        self.allow_lan_http = QCheckBox("Allow unencrypted HTTP on trusted LAN addresses")
        self.allow_lan_http.setChecked(self.settings.allow_lan_http)
        self.allow_lan_http.setToolTip(
            "Passwords, tokens and job data are readable on the network. "
            "Only private IP addresses and .local names are accepted; HTTPS remains recommended."
        )
        form.addRow(self.allow_lan_http)
        self.lan_addresses = LanAddressList(
            self.settings.lan_addresses, self.settings.lan_hostname
        )
        form.addRow("Reachable from other PCs at", self.lan_addresses)
        self.connect_urls = QLabel()
        self.connect_urls.setWordWrap(True)
        self.connect_urls.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Connect using", self.connect_urls)
        self.public_urls = QPlainTextEdit(
            "\n".join(
                url for url in self.settings.connection_urls if url not in self.settings.lan_urls
            )
        )
        self.public_urls.setPlaceholderText("Tailscale or HTTPS proxy, one per line")
        self.public_urls.setMaximumHeight(60)
        form.addRow("Other web URLs", self.public_urls)
        address_help = QLabel(
            "Local only by default. For another PC, enable HTTPS or LAN HTTP, tick what it will "
            "use and allow the port in the OS firewall. Other web URLs open no listener."
        )
        address_help.setWordWrap(True)
        form.addRow(address_help)

        def update_lan_fields():
            secure = self.lan_tls.isChecked()
            listening = secure or self.allow_lan_http.isChecked()
            self.ca_button.setEnabled(secure)
            form.setRowVisible(self.fields["tls_port"], secure)
            form.setRowVisible(self.ca_button, secure)
            form.setRowVisible(self.regenerate_ca_button, secure)
            form.setRowVisible(self.lan_addresses, listening)
            form.setRowVisible(self.connect_urls, listening)

        def update_connect_urls():
            urls = self._network_preview().lan_urls
            self.connect_urls.setText("\n".join(urls) or "Tick an address above.")

        self.allow_lan_http.toggled.connect(update_lan_fields)
        self.lan_tls.toggled.connect(update_lan_fields)
        self.allow_lan_http.toggled.connect(update_connect_urls)
        self.lan_tls.toggled.connect(update_connect_urls)
        self.lan_addresses.changed.connect(update_connect_urls)
        self.fields["port"].valueChanged.connect(update_connect_urls)
        self.fields["tls_port"].valueChanged.connect(update_connect_urls)
        update_connect_urls()
        update_lan_fields()
        layout.addWidget(network)

        resources = QGroupBox("Resources")
        limits = QFormLayout(resources)
        for key, label, maximum in (
            ("cpus", "CPU budget (cores)", 4096),
            ("memory_mb", "Memory budget", 2147483647),
            ("max_active_per_user", "Active jobs per user", 10000),
            ("max_queued_per_user", "Queued jobs per user", 100000),
            ("storage_quota_mb", "Storage per user/job", 2147483647),
        ):
            limits.addRow(label, number(key, maximum))
        cpus, memory_mb = pc_resources()
        self.match_pc_button = QPushButton(pc_size_label(gb(memory_mb)))

        def match_pc():
            self.fields["cpus"].setValue(cpus)
            self.fields["memory_mb"].set_mb(memory_mb)

        self.match_pc_button.clicked.connect(match_pc)
        limits.addRow("", self.match_pc_button)
        layout.addWidget(resources)

        job_setup = QGroupBox("Commands before each job")
        job_setup_layout = QVBoxLayout(job_setup)
        self.job_setup = QPlainTextEdit("\n".join(self.settings.job_setup_commands))
        self.job_setup.setPlaceholderText(
            '$env:OMP_NUM_THREADS = "4"\nconda activate orca'
            if self.settings.execution_os == "windows"
            else "export OMP_NUM_THREADS=4\nconda activate orca"
        )
        self.job_setup.setMaximumHeight(70)
        job_setup_layout.addWidget(self.job_setup)
        job_setup_help = QLabel(
            "Runs at the start of every new job, before the client's own commands, in the job's "
            "shell. Applies to all users after a restart. Environment variables set here last "
            "for that job."
        )
        job_setup_help.setWordWrap(True)
        job_setup_layout.addWidget(job_setup_help)
        layout.addWidget(job_setup)

        storage = QGroupBox("Job working directory")
        storage_layout = QVBoxLayout(storage)
        location = QHBoxLayout()
        self.job_dir = QLineEdit(self.settings.job_dir)
        self.job_dir.setPlaceholderText(f"Default: {self.settings.root}")
        location.addWidget(self.job_dir)
        browse_jobs = QPushButton("Browse...")

        def choose_job_dir():
            path = QFileDialog.getExistingDirectory(
                self, "Select job working directory", self.job_dir.text()
            )
            if path:
                self.job_dir.setText(path)

        browse_jobs.clicked.connect(choose_job_dir)
        location.addWidget(browse_jobs)
        storage_layout.addLayout(location)
        job_help = QLabel(
            "Applies to new jobs after a restart; existing jobs stay where they are. "
            "Job files keep the folder's permissions, so choose a private folder."
        )
        job_help.setWordWrap(True)
        storage_layout.addWidget(job_help)
        layout.addWidget(storage)

        def folder_row(value, placeholder, title):
            row = QHBoxLayout()
            edit = QLineEdit(value)
            edit.setPlaceholderText(placeholder)
            row.addWidget(edit)
            button = QPushButton("Browse...")

            def choose():
                path = QFileDialog.getExistingDirectory(self, title, edit.text())
                if path:
                    edit.setText(path)

            button.clicked.connect(choose)
            row.addWidget(button)
            return row, edit

        finished = QGroupBox("Finished jobs folder")
        finished_layout = QVBoxLayout(finished)
        row, self.finished_dir = folder_row(
            self.settings.finished_dir, "Off: finished jobs stay in the working directory",
            "Select the finished jobs folder",
        )
        finished_layout.addLayout(row)
        finished_help = QLabel(
            "When set, each job that ends is moved here as <user>/<job name> (a number is "
            "added if the name is taken). Clients can still browse and download it. Applies to "
            "jobs that end after a restart. Choose a private folder."
        )
        finished_help.setWordWrap(True)
        finished_layout.addWidget(finished_help)
        move_existing = QPushButton("Move existing finished jobs now")
        move_existing.setToolTip(
            "Also move jobs that ended before the folder was set. Uses the folder the running "
            "server was started with."
        )
        move_existing.clicked.connect(self.move_finished_jobs)
        finished_layout.addWidget(move_existing, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(finished)

        backup = QGroupBox("Database backups")
        backup_form = QFormLayout(backup)
        self.db_backup = QCheckBox("Back up server.db automatically")
        self.db_backup.setChecked(self.settings.db_backup)
        backup_form.addRow(self.db_backup)
        backup_form.addRow("Every (hours)", number("db_backup_hours", 8760))
        backup_form.addRow("Copies to keep", number("db_backup_keep", 1000))
        row, self.db_backup_dir = folder_row(
            self.settings.db_backup_dir, f"Default: {self.settings.root / 'backups'}",
            "Select the database backup folder",
        )
        backup_form.addRow("Folder", row)
        backup_help = QLabel(
            "Backups hold accounts and password hashes: keep the folder private. "
            "Another disk protects against a disk failure."
        )
        backup_help.setWordWrap(True)
        backup_form.addRow(backup_help)
        layout.addWidget(backup)

        runtime_box = QGroupBox("Daemon Python runtime")
        runtime_layout = QVBoxLayout(runtime_box)
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
        runtime_layout.addLayout(runtime)
        runtime_note = QLabel(
            "Executor and WSL settings are in the JSON configuration. "
            "Stopping or reloading this window preserves running jobs."
        )
        runtime_note.setWordWrap(True)
        runtime_layout.addWidget(runtime_note)
        layout.addWidget(runtime_box)
        layout.addWidget(QLabel(
            "When the server is running, saving offers a restart so the changes apply at once."
        ))
        self.save_settings_button = QPushButton("Save configuration")
        self.save_settings_button.clicked.connect(self.save_settings_and_offer_restart)
        layout.addWidget(self.save_settings_button)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, "Settings")

    def task(self, call, on_ok=None, *, interactive=True, success_message=None, on_error=None, on_start=None):
        if self.busy:
            if interactive:
                self._pending_tasks.append((call, on_ok, success_message, on_error, on_start))
                self.message.setText("Operation queued; waiting for the current operation to finish.")
            return
        if not interactive and self._pending_tasks:
            return
        self.busy = True
        if on_start:
            on_start()
        task = Task(call)
        self._tasks.add(task)

        def finished(value):
            self._tasks.discard(task)
            self.busy = False
            if on_ok:
                on_ok(value)
            if success_message:
                QMessageBox.information(self, "Success", success_message)
            self._run_pending_task()

        def failed(message):
            self._tasks.discard(task)
            self.busy = False
            self.message.setText(message)
            if on_error:
                on_error(message)
            if interactive:
                QMessageBox.warning(self, "Job Server", message)
            self._run_pending_task()

        connect_queued(task.signals.finished, finished, self, (object,), cleanup=(task.signals.finished, task.signals.failed))
        connect_queued(task.signals.failed, failed, self, (str,), cleanup=(task.signals.finished, task.signals.failed))
        self.pool.start(task)

    def _run_pending_task(self):
        if self._pending_tasks and not self.busy:
            call, on_ok, success_message, on_error, on_start = self._pending_tasks.popleft()
            self.task(call, on_ok, success_message=success_message, on_error=on_error, on_start=on_start)

    def refresh(self):
        if self._pending_lifecycle:
            if self._active_lifecycle:
                title, started = self._active_lifecycle
                self.message.setText(f"{title} ({time.monotonic() - started:.0f}s)")
            return

        page = self.job_page
        since = self.history_rows[-1][0] if self.history_rows else 0
        client = self.client

        def history():
            # The lines are extra: a daemon from before 0.12 has no history, and a failed
            # read must not take the figures and job list down with it.
            try:
                return client.request("GET", f"/system/resources/history?since={since}")
            except Exception as exc:  # noqa: BLE001 - logged; the rest of the refresh goes on
                logging.info("Job Server: load history unavailable: %s", exc)
                return None

        def work():
            try:
                health = self.client.request("GET", "/health", authenticated=False)
            except URLError as exc:
                if isinstance(exc.reason, ConnectionRefusedError):
                    return {"offline": True}
                raise
            except ConnectionRefusedError:
                return {"offline": True}
            if not self.client.key_path.exists():
                return {"health": health}
            try:
                return {
                    "health": health,
                    "me": self.client.request("GET", "/me"),
                    "own_device": self._own_device_id(),
                    "resources": self.client.request("GET", "/system/resources"),
                    "jobs": self.client.request(
                        "GET",
                        "/admin/jobs" if not page else f"/admin/jobs?limit={JOB_PAGE}&offset={page * JOB_PAGE}",
                    ),
                    "users": self.client.request("GET", "/admin/users"),
                    "devices": self.client.request("GET", "/admin/devices"),
                    "history": history(),
                }
            except Exception as exc:
                # /health answered, so the daemon is online; only the signed-in calls failed.
                return {"health": health, "error": str(exc)}

        def display(data):
            if self._pending_lifecycle:
                return
            if data.get("offline"):
                self.message.setText("Server is OFF. Choose Server > Start.")
                self.set_status("offline", "Server is OFF", "Choose Server > Start when ready.")
                self._clear_dashboard()
                return
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
            self.me, self.own_device = data["me"], data["own_device"]
            self.account_label.setText(f"Signed in as {self.me['username']} ({self.me['role']}).")
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
                f"{gb(resource.get('reserved_memory_mb'))} / {gb(resource.get('memory_mb'))} GiB"
            )
            self._set_usage("cpu", resource.get("reserved_cpus"), resource.get("cpus"))
            self._set_usage("memory", resource.get("reserved_memory_mb"), resource.get("memory_mb"))
            cpu_now, memory_now = resource.get("cpu_percent"), resource.get("memory_percent")
            self.metric_values["cpu_usage"].setText("—" if cpu_now is None else f"{cpu_now:.0f}%")
            self.metric_values["memory_usage"].setText(
                "—" if memory_now is None
                else f"{gb(resource.get('memory_used_mb'))} / {gb(resource.get('memory_total_mb'))} GiB"
            )
            # 100% is every thread; past the dotted line the hyper-threads are in use.
            cores, threads = resource.get("cpu_cores"), resource.get("cpu_threads")
            hyper = isinstance(cores, int) and isinstance(threads, int) and 0 < cores < threads
            cores_share = 100 * cores / threads if hyper else None
            self.metric_trends["cpu_usage"].set_marker(cores_share, "Phys. cores" if hyper else "")
            self.metric_bars["cpu_usage"].setMark(cores_share)
            self._set_usage("cpu_usage", cpu_now, 100, limit=cores_share)
            self._set_usage("memory_usage", memory_now, 100)
            self._merge_history(data.get("history"))
            usernames = {u["id"]: u["username"] for u in data["users"]}
            self.usernames = usernames
            self.job_records = {job["id"]: job for job in data["jobs"]}
            self.fill(
                self.jobs,
                [
                    (j["spec"]["name"], usernames.get(j["owner_id"], "(deleted user)"),
                     j["display_state"] + (" · force requested" if j.get("force_run") else "")
                     + (" · needs recheck" if self._stuck(j) else ""),
                     j.get("priority", 0), j["id"], self.timestamp(j.get("created_at")))
                    for j in data["jobs"]
                ], identifiers=[j["id"] for j in data["jobs"]],
            )
            self._color_states()
            self._apply_job_filter()
            self._update_job_actions()
            first = page * JOB_PAGE + 1
            count = len(data["jobs"])
            self.job_caption.setText(
                f"Jobs {first}–{first + count - 1} — right-click for actions" if count
                else "No jobs on this page."
            )
            self.newer_jobs.setEnabled(page > 0)
            self.older_jobs.setEnabled(count == JOB_PAGE)
            self.user_records = {u["id"]: u for u in data["users"]}
            self.device_records = {d["id"]: d for d in data["devices"]}
            active = {}
            for device in data["devices"]:
                if device["revoked_at"] is None:
                    active[device["user_id"]] = active.get(device["user_id"], 0) + 1
            self.fill(
                self.users,
                [
                    (u["username"] + (" (you)" if u["id"] == self.me["user_id"] else ""),
                     "administrator" if u["role"] == "admin" else "user",
                     "Enabled" if u["enabled"] else "Disabled", active.get(u["id"], 0),
                     self.timestamp(u.get("created_at")))
                    for u in data["users"]
                ],
                identifiers=[u["id"] for u in data["users"]],
            )
            self.fill(
                self.devices,
                [
                    (d["id"], usernames.get(d["user_id"], d["user_id"]),
                     ("Revoked" if d["revoked_at"] is not None else "Active")
                     + (" · this window" if d["id"] == self.own_device else ""),
                     self.timestamp(d["last_access"]), self.timestamp(d.get("created_at")))
                    for d in data["devices"]
                ],
            )
            self._dim_rows(self.users, lambda row: self.users.item(row, 2).text() == "Disabled")
            self._dim_rows(self.devices, lambda row: self.devices.item(row, 2).text().startswith("Revoked"))
            self._update_account_actions()
            self._refresh_activity()

        def unavailable(_):
            if self._pending_lifecycle:
                return
            self.message.setText("Server is not responding. Check Server > Start or File > Open Logs.")
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
        self.user_records = {}
        self.device_records = {}
        self.me = None
        self.own_device = None
        self.account_label.setText("Not signed in.")
        self.newer_jobs.setEnabled(False)
        self.older_jobs.setEnabled(False)
        for value in self.metric_values.values():
            value.setText("—")
        for key in self.metric_bars:
            self._set_usage(key, None, None)
        self.history_rows.clear()
        for trend in self.metric_trends.values():
            trend.set_samples([])
        for table in (self.jobs, self.users, self.devices, self.activity):
            self.fill(table, [])
        self._update_job_actions()
        self._update_account_actions()
        if self.detail_dialog is not None:
            self.detail_dialog.invalidate("Connection unavailable. Sign in and refresh to view this job.")

    # Where each figure sits in a history sample from the server.
    HISTORY_COLUMNS = {"running": 1, "pending": 2, "cpu_usage": 3, "memory_usage": 4, "cpu": 5, "memory": 6}

    def _merge_history(self, history):
        if not history:
            return
        last = self.history_rows[-1][0] if self.history_rows else 0
        self.history_rows.extend(row for row in history["samples"] if row[0] > last)
        horizon = time.time() - history.get("keep_seconds", SPANS[-1][0])
        while self.history_rows and self.history_rows[0][0] < horizon:
            self.history_rows.popleft()
        gap = 3 * history.get("interval", 10)
        for key, column in self.HISTORY_COLUMNS.items():
            self.metric_trends[key].set_samples(
                [(row[0], row[column]) for row in self.history_rows], gap=gap
            )

    def _set_span(self, span):
        self.span = span
        for trend in self.metric_trends.values():
            trend.set_span(*span)

    def _set_usage(self, key, used, budget, limit=None):
        """Colour by the share of ``limit`` (100% unless given): green, amber from 70%,
        red from 90%, and purple past the limit, such as every core busy on a
        hyper-threaded PC."""
        share = percent(used, budget)
        value = 0 if share is None else round(share)
        of_limit = 100 * (share or 0) / (limit or 100)
        color = (PAST_LIMIT if of_limit > 100.5 else "#c62828" if of_limit >= 90
                 else "#e08600" if of_limit >= 70 else "#2e7d32")
        gauge = self.metric_bars[key]
        gauge.setValue(value)
        gauge.setKnown(share is not None)
        gauge.setColor(color)

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
    def _dim_rows(table, dimmed):
        color = QBrush(QColor("#8a8a8a"))
        for row in range(table.rowCount()):
            if dimmed(row):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        item.setForeground(color)

    def _apply_job_filter(self):
        states = JOB_FILTERS[self.job_filter.currentText()]
        words = self.job_search.text().casefold().split()
        for row in range(self.jobs.rowCount()):
            cells = [self.jobs.item(row, column) for column in range(self.jobs.columnCount())]
            text = " ".join(cell.text() for cell in cells if cell is not None).casefold()
            state = cells[2].text().split(" ", 1)[0] if cells[2] is not None else ""
            self.jobs.setRowHidden(
                row,
                (states is not None and state not in states) or not all(word in text for word in words),
            )

    def _job_page(self, step):
        self.job_page = max(0, self.job_page + step)
        self.refresh()

    def _refresh_activity(self):
        """Read the log only while it is on screen; it is not worth polling otherwise."""
        if self.tabs.currentWidget() is not self.activity.parentWidget() or self.me is None:
            return
        names = dict(self.usernames)

        def describe(event):
            try:
                details = json.loads(event["details"])
            except (TypeError, ValueError):
                details = {}
            if event.get("job_id"):
                details["job"] = event["job_id"]
            if details.get("user_id") in names:
                details["user"] = names[details.pop("user_id")]
            return " · ".join(f"{key}: {value}" for key, value in details.items())

        def display(events):
            self.fill(
                self.activity,
                [
                    (self.timestamp(e["timestamp"]), e["event"].replace("_", " "),
                     names.get(e["actor"], "(deleted user)") if e["actor"] else "local / system",
                     describe(e))
                    for e in events
                ],
                identifiers=[e["seq"] for e in events],
            )

        self.task(
            lambda: self.client.request("GET", f"/admin/events?newest={EVENT_LIMIT}"),
            display, interactive=False,
        )

    @staticmethod
    def timestamp(value):
        if value is None:
            return "Never"
        try:
            return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        except (ValueError, TypeError, OSError, OverflowError):
            return "—"

    @staticmethod
    def _table_contents(table):
        """Each row's identifier and texts, in the table's current order."""
        contents = []
        for row in range(table.rowCount()):
            texts = tuple(
                item.text() if (item := table.item(row, column)) is not None else None
                for column in range(table.columnCount())
            )
            first = table.item(row, 0)
            contents.append((first.data(Qt.ItemDataRole.UserRole) if first else None, texts))
        return contents

    @staticmethod
    def fill(table, rows, identifiers=None):
        wanted = [
            (
                identifiers[row] if identifiers is not None else str(values[0]),
                tuple(str(value) for value in values)
                + (None,) * (table.columnCount() - len(values)),
            )
            for row, values in enumerate(rows)
        ]
        current = Dashboard._table_contents(table)
        # A periodic refresh that changes nothing must not touch the table: rebuilding
        # it moved the view back to the selected row while the user was scrolling.
        if table.isSortingEnabled():
            if sorted(current, key=repr) == sorted(wanted, key=repr):
                return
        elif current == wanted:
            return
        selected = Dashboard.selected(table)
        current_column = max(table.currentColumn(), 0)
        scroll = (table.verticalScrollBar().value(), table.horizontalScrollBar().value())
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
                    table.setCurrentCell(row, min(current_column, table.columnCount() - 1))
                    break
        finally:
            table.setSortingEnabled(sorting)
            table.blockSignals(signals)
            # Selecting scrolls to the row; the user's own scroll position wins.
            table.verticalScrollBar().setValue(scroll[0])
            table.horizontalScrollBar().setValue(scroll[1])

    def _network_preview(self):
        """The network settings as currently chosen in the form."""
        listening = self.allow_lan_http.isChecked() or self.lan_tls.isChecked()
        addresses = self.lan_addresses.addresses() if listening else []
        return replace(
            self.settings,
            allow_lan_http=self.allow_lan_http.isChecked(),
            lan_tls=self.lan_tls.isChecked(),
            port=self.fields["port"].value(),
            tls_port=self.fields["tls_port"].value(),
            lan_bind_address=next(iter(addresses), ""),
            lan_extra_addresses=addresses[1:],
            lan_hostname=self.lan_addresses.hostname() if addresses else "",
        )

    def _confirm_ca_replacement(self, candidate):
        """A `.local` name needs a new CA, which every connecting PC must trust again."""
        if not candidate.lan_hostname:
            return True
        from ..tls import needs_new_ca, retire_ca

        if not needs_new_ca(candidate):
            return True
        answer = QMessageBox.question(
            self,
            "Replace the local CA?",
            f"The current local CA cannot issue a certificate for {candidate.lan_hostname}, "
            "so it must be replaced.\n\nEvery PC and browser that trusts it must trust the new "
            "CA, and older Job Manager releases cannot do so. Replace it?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.message.setText(f"Not saved. {candidate.lan_hostname} needs a new local CA.")
            return False
        retire_ca(candidate)
        return True

    def save_settings(self):
        if self._pending_lifecycle:
            self.message.setText("Wait for the server operation to finish before saving configuration.")
            return False
        try:
            preview = self._network_preview()
            urls = [
                url for url in self.public_urls.toPlainText().split()
                if url.rstrip("/") not in self.settings.lan_urls
            ]
            candidate = replace(
                self.settings,
                python_executable=self.python_path.text().strip(),
                job_dir=self.job_dir.text().strip(),
                finished_dir=self.finished_dir.text().strip(),
                db_backup=self.db_backup.isChecked(),
                db_backup_dir=self.db_backup_dir.text().strip(),
                job_setup_commands=[
                    line.strip()
                    for line in self.job_setup.toPlainText().splitlines()
                    if line.strip()
                ],
                public_url="",
                public_urls=urls,
                allow_lan_http=self.allow_lan_http.isChecked(),
                lan_tls=self.lan_tls.isChecked(),
                lan_bind_address=preview.lan_bind_address,
                lan_extra_addresses=preview.lan_extra_addresses,
                lan_hostname=preview.lan_hostname,
                **{key: field.mb() if isinstance(field, GBSpinBox) else field.value()
                   for key, field in self.fields.items()},
            )
            candidate.validate()
            if not self._confirm_ca_replacement(candidate):
                return False
            candidate.save(self.config)
            if candidate != self.settings:
                self._unapplied = True
            self.settings = candidate
            self.public_urls.setPlainText(
                "\n".join(url for url in candidate.connection_urls if url not in candidate.lan_urls)
            )
            self.message.setText(f"Saved {self.config}. Choose Server > Restart to apply.")
            return True
        except Exception as exc:
            self.message.setText(str(exc))
            return False

    def save_settings_and_offer_restart(self):
        if self.save_settings():
            self.offer_restart_after_save()

    def offer_restart_after_save(self):
        """Saving only writes the file; offer the restart that applies it.

        Called after a save from the Settings tab or the setup guide, never
        from save_settings() itself: several flows save as a step of their
        own (showing the CA, regenerating it) and must not be interrupted.
        A stopped server needs no offer; it reads the file at its next start.
        """
        from ..lifecycle import directory_is_idle

        if directory_is_idle(self.settings.root):
            self._unapplied = False
            self.message.setText(f"Saved {self.config}. The server uses it when it starts.")
            return
        if not self._unapplied:
            # Saving unchanged settings is no reason to interrupt clients.
            self.message.setText(f"Saved {self.config}. No setting changed.")
            return
        answer = QMessageBox.question(
            self,
            "Restart the server?",
            "The saved settings apply only after a restart. Running jobs are "
            "preserved; connected clients reconnect. Restart now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.restart_daemon()

    def regenerate_ca(self):
        if not self.save_settings():
            return
        from ..tls import ca_path, retire_ca

        if ca_path(self.settings).exists():
            answer = QMessageBox.question(
                self,
                "Regenerate the local CA?",
                "A new CA and server certificate replace the current ones. Every PC and browser "
                "that trusts the current CA must trust the new one. The old files move to a "
                "retired- folder. Restart the server to use the new CA. Regenerate it?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                retire_ca(self.settings)
            except (OSError, ValueError) as exc:
                self.message.setText(f"The CA was not regenerated: {exc}")
                return
        self.show_local_ca()

    def show_local_ca(self):
        if not self.save_settings():
            return
        settings = self.settings

        def work():
            from ..tls import ca_path, ensure_certificates, _read
            from ..trust import validate_ca
            from cryptography.hazmat.primitives import hashes, serialization

            ensure_certificates(settings)
            certificate = validate_ca(_read(ca_path(settings)))
            return (
                certificate.public_bytes(serialization.Encoding.PEM),
                certificate.fingerprint(hashes.SHA256()).hex().upper(), ca_path(settings),
            )

        def display(info):
            pem, fingerprint, path = info
            dialog = QDialog(self)
            dialog.setWindowTitle("Local HTTPS CA certificate")
            dialog.resize(760, 270)
            layout = QFormLayout(dialog)
            explanation = QLabel("Compare this SHA-256 fingerprint on the connecting PC before trusting the CA. "
                                 "Only the public certificate is exported.")
            explanation.setWordWrap(True)
            layout.addRow(explanation)
            field = QLineEdit(fingerprint)
            field.setReadOnly(True)
            layout.addRow("SHA-256 fingerprint", field)
            location = QLineEdit(str(path))
            location.setReadOnly(True)
            layout.addRow("Public CA certificate", location)
            copy = QPushButton("Copy fingerprint")
            copy.clicked.connect(lambda: QApplication.clipboard().setText(fingerprint))
            layout.addRow(copy)
            export = QPushButton("Export public CA certificate...")

            def save():
                target, _ = QFileDialog.getSaveFileName(dialog, "Export public CA", "job-server-ca.pem", "CA certificate (*.pem)")
                if target:
                    Path(target).write_bytes(pem)

            export.clicked.connect(save)
            layout.addRow(export)
            if os.name == "nt":
                trust = QPushButton("Trust CA for this Windows user...")

                def install():
                    if QMessageBox.question(
                        dialog, "Trust local CA",
                        "Add this CA to the current Windows user's trusted roots? Applications using "
                        "that store will trust certificates issued by this CA.\n\nSHA-256: " + fingerprint,
                    ) != QMessageBox.StandardButton.Yes:
                        return
                    from ..trust import trust_windows_user_ca
                    self.task(lambda: trust_windows_user_ca(pem, fingerprint),
                              success_message="CA trusted for the current Windows user.")

                trust.clicked.connect(install)
                layout.addRow(trust)
            close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            close.rejected.connect(dialog.reject)
            layout.addRow(close)
            dialog.show()
            self.ca_dialog = dialog

        self.task(work, display)

    def require_own_daemon(self):
        from ..daemon_ownership import verify_local_daemon

        verify_local_daemon(self.settings.root, urlsplit(self.client.url).port)

    def _start_daemon(self):
        settings = self.settings
        url = f"http://127.0.0.1:{settings.port}"
        if self.client.url != url:
            self.client = JobServerClient(
                url, settings.root / "identity" / "admin-device.key", timeout=3,
            )
        try:
            health = self.client.request("GET", "/health", authenticated=False)
            running = True
        except Exception:
            running = False
        if running:
            self.require_own_daemon()
            if health.get("status", "ok") != "ok":
                from ..lifecycle import wait_until_ready
                wait_until_ready(JobServerClient(self.client.url, timeout=1), settings.root, None)
            return "Daemon is already running; reusing it."
        settings.save(self.config)
        log_dir = settings.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = (
            str(Path(__file__).resolve().parents[2])
            + os.pathsep
            + environment.get("PYTHONPATH", "")
        )
        with (log_dir / "startup.log").open("ab") as log:
            from ..runtime import python_executable

            process = subprocess.Popen(
                [
                    python_executable(settings),
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
            f"http://127.0.0.1:{settings.port}",
            settings.root / "identity" / "admin-device.key", timeout=3,
        )
        self._unapplied = False  # the new process read the file just saved
        from ..lifecycle import wait_until_ready

        wait_until_ready(JobServerClient(self.client.url, timeout=1), settings.root, process)
        return "Server is running. Active jobs are preserved."

    def _server_task(self, name, call, title, done):
        action = {"start": self.start_action, "stop": self.stop_action, "restart": self.restart_action}[name]
        if name in self._pending_lifecycle:
            self.message.setText(f"Server {name} is already pending.")
            return
        self._pending_lifecycle.add(name)
        self.save_settings_button.setEnabled(False)
        action.setEnabled(False)

        def starting():
            self._active_lifecycle = (title, time.monotonic())
            self.message.setText(title)
            self.set_status("warning", title, "Waiting for the server to confirm completion.")

        def finished(value):
            self._pending_lifecycle.discard(name)
            self.save_settings_button.setEnabled(not self._pending_lifecycle)
            self._active_lifecycle = None
            action.setEnabled(True)
            done(value)

        def failed(message):
            self._pending_lifecycle.discard(name)
            self.save_settings_button.setEnabled(not self._pending_lifecycle)
            self._active_lifecycle = None
            action.setEnabled(True)
            self.set_status("warning", "Server operation failed", message)

        if self.busy:
            self.set_status("warning", title + " — queued", "Waiting for the current operation.")
        self.task(call, finished, on_error=failed, on_start=starting)

    def _server_started(self, message):
        self.message.setText(message)
        self.set_status("online", "Server is running", "Ready for connections.")
        self.refresh()

    def start_daemon(self):
        # A daemon this window cannot reach (its port changed while it ran, or
        # an older version started it) still holds the data folder, so a new
        # one would only exit at startup. Offer to replace it instead.
        fallback = self._offer_process_stop()
        if fallback is False:
            return

        def work():
            if fallback is not None:
                fallback()
            return self._start_daemon()

        self._server_task("start", work, "Starting server…", self._server_started)

    def _offer_process_stop(self):
        """Offer to stop an unverifiable daemon by process, for Stop/Restart.

        Returns a stop callable when this directory's daemon is running
        but cannot be verified and the user confirms stopping its process;
        False when the user declines; None when the verified path applies
        (daemon stopped, or ownership verifies). The process stop sends no
        credentials and so does not require this window's device
        registration — requiring it would deadlock the window, because
        registration itself verifies ownership before enrolling.
        """
        from ..daemon_ownership import daemon_processes
        from ..lifecycle import directory_is_idle, stop_processes_and_wait

        if self._pending_lifecycle or directory_is_idle(self.settings.root):
            return None
        try:
            self.require_own_daemon()
        except RuntimeError:
            pass
        else:
            return None
        processes = daemon_processes(self.settings.root)
        if not processes:
            return None
        listing = ", ".join(str(process.pid) for process in processes)
        answer = QMessageBox.question(
            self,
            "Stop the unverified daemon?",
            "A daemon is using this server's data folder, but it cannot be "
            "verified as this Job Server — for example it was started by an "
            "older version, or its port was changed while it ran. It cannot "
            "be stopped with credentials, and this window cannot sign in to "
            f"it. Stop its process directly (PID {listing})? No credentials "
            "are sent, and running jobs are preserved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        return lambda: stop_processes_and_wait(self.settings.root)

    def stop_daemon(self):
        from ..lifecycle import stop_and_wait

        def stopped(_):
            self.message.setText("Server is OFF. Active job supervisors are preserved.")
            self.set_status("offline", "Server is OFF", "Active job supervisors are preserved.")

        fallback = self._offer_process_stop()
        if fallback is False:
            return
        if fallback is not None:
            self._server_task("stop", fallback, "Stopping server…", stopped)
            return
        if not self.require_admin_registration():
            return
        self._server_task(
            "stop", lambda: stop_and_wait(self.client, self.settings.root), "Stopping server…", stopped,
        )

    def open_web_ui(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(next(iter(self.settings.connection_urls), self.client.url)))

    def restart_daemon(self):
        from ..lifecycle import stop_and_wait

        fallback = self._offer_process_stop()
        if fallback is False:
            return
        if fallback is not None:
            def work():
                fallback()
                return self._start_daemon()

            self._server_task("restart", work, "Restarting server…", self._server_started)
            return
        if not self.require_admin_registration():
            return

        def work():
            stop_and_wait(self.client, self.settings.root)
            return self._start_daemon()

        self._server_task("restart", work, "Restarting server…", self._server_started)

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
            def authenticate():
                try:
                    self.client.authenticate()
                except ClientError as exc:
                    if exc.status == 401 and exc.body.get("code") == "invalid_device":
                        return "revoked"
                    raise
                return None

            def authenticated(result):
                if result == "revoked":
                    self._register_again(signed_in)
                else:
                    signed_in(result)

            self.task(authenticate, authenticated)
        else:
            credentials = self.credentials("Register administrator device")
            if credentials:

                def work():
                    self.require_own_daemon()
                    return self.client.enroll(*credentials)

                self.task(work, signed_in)

    def _own_device_id(self):
        from ..credentials import load_key

        try:
            return load_key(self.client.key_path)["device_id"]
        except Exception:
            return None

    def _register_again(self, signed_in):
        """A revoked key cannot sign in again, so replace it only after a new one is accepted."""
        if QMessageBox.question(
            self, "Register this window again",
            "This window's device was revoked or its account was disabled. Register a new device "
            "with your username and password?",
        ) != QMessageBox.StandardButton.Yes:
            return
        credentials = self.credentials("Register administrator device")
        if not credentials:
            return
        target = self.client.key_path
        pending = target.with_name(target.name + ".new")

        def work():
            self.require_own_daemon()
            pending.unlink(missing_ok=True)
            try:
                JobServerClient(self.client.url, pending, timeout=3).enroll(*credentials)
                os.replace(pending, target)
            finally:
                pending.unlink(missing_ok=True)
            self.client.token, self.client.expires = None, 0

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
        from .account import ask_new_user

        created = ask_new_user(self)
        if created:
            username, password, role = created
            self.task(
                lambda: self.client.request(
                    "POST", "/admin/users", {"username": username, "password": password, "role": role}
                ),
                lambda _: self.refresh(),
                success_message="Administrator created." if role == "admin" else "User created.",
            )

    def change_password(self):
        if not self.require_admin_registration():
            return
        from .account import ask_password

        answer = ask_password(
            self, "Change my password",
            "Other sign-ins for this account end. This window stays signed in.",
            ask_current=True,
            revoke_label="Also revoke my other registered devices",
        )
        if not answer:
            return
        current, new, revoke = answer

        def changed(result):
            self.refresh()
            revoked = result.get("devices_revoked", 0) if isinstance(result, dict) else 0
            self.message.setText(f"Password changed. {revoked} other device(s) revoked.")

        self.task(
            lambda: self.client.request(
                "POST", "/me/password",
                {"current_password": current, "new_password": new, "revoke_devices": revoke},
            ),
            changed, success_message="Your password was changed.",
        )

    def _user_name(self, identifier):
        return self.user_records.get(identifier, {}).get("username", "this user")

    def reset_password(self):
        user = self.selected(self.users)
        if not user or not self.require_admin_registration():
            return
        from .account import ask_password

        answer = ask_password(
            self, "Reset password",
            f"Set a new password for {self._user_name(user)} and end their current sign-ins. "
            "Give it to them over a private channel; they can change it afterwards.",
            revoke_label="Also revoke their registered devices (recommended if the account may be compromised)",
        )
        if not answer:
            return
        _, new, revoke = answer
        self.task(
            lambda: self.client.request(
                "POST", f"/admin/users/{user}/password",
                {"new_password": new, "revoke_devices": revoke},
            ),
            lambda _: self.refresh(), success_message="Password reset.",
        )

    def toggle_user(self):
        user = self.selected(self.users)
        if user and self.user_records.get(user, {}).get("enabled", 1) == 0:
            self.enable_user()
        else:
            self.disable_user()

    def enable_user(self):
        user = self.selected(self.users)
        if not user or not self.require_admin_registration():
            return
        if QMessageBox.question(
            self, "Enable user",
            f"Enable {self._user_name(user)}? Their password and any devices they did not lose "
            "access to start working again.",
        ) == QMessageBox.StandardButton.Yes:
            self.task(
                lambda: self.client.request("POST", f"/admin/users/{user}/enable"),
                lambda _: self.refresh(), success_message="User enabled.",
            )

    def toggle_role(self):
        user = self.selected(self.users)
        if not user or not self.require_admin_registration():
            return
        current = self.user_records.get(user, {}).get("role", "user")
        role = "user" if current == "admin" else "admin"
        text = (
            f"Make {self._user_name(user)} an administrator? Administrators can manage every "
            "user and job on this server, including other administrators."
            if role == "admin" else
            f"Make {self._user_name(user)} a regular user? They lose administration at once and "
            "see only their own jobs."
        )
        if QMessageBox.question(self, "Change account type", text) == QMessageBox.StandardButton.Yes:
            self.task(
                lambda: self.client.request("POST", f"/admin/users/{user}/role", {"role": role}),
                lambda _: self.refresh(),
                success_message="Account type changed.",
            )

    def delete_user(self):
        user = self.selected(self.users)
        if not user or not self.require_admin_registration():
            return
        name = self._user_name(user)
        typed, accepted = QInputDialog.getText(
            self, "Delete user",
            f"Delete {name} permanently? Their sign-in and devices are removed and this cannot be "
            "undone. Their finished jobs and files stay on the server under a deleted owner; "
            "administrators can still open them.\n\n"
            "To keep the account recoverable, disable it instead.\n\n"
            f"Type {name} to confirm:",
        )
        if not accepted:
            return
        if typed.strip() != name:
            self.message.setText("Not deleted: the name did not match.")
            return
        self.task(
            lambda: self.client.request("DELETE", f"/admin/users/{user}"),
            lambda _: self.refresh(), success_message="User deleted.",
        )

    def copy_username(self):
        user = self.selected(self.users)
        if user in self.user_records:
            QApplication.clipboard().setText(self.user_records[user]["username"])

    def copy_device_id(self):
        device = self.selected(self.devices)
        if device:
            QApplication.clipboard().setText(device)

    def copy_job_id(self):
        job = self.selected(self.jobs)
        if job:
            QApplication.clipboard().setText(job)

    def _update_account_actions(self):
        user = self.selected(self.users)
        record = self.user_records.get(user)
        own = bool(self.me) and user == self.me.get("user_id")
        signed_in = self.me is not None
        self.change_password_action.setEnabled(signed_in)
        self.add_user_action.setEnabled(signed_in)
        self.reset_password_action.setEnabled(bool(record) and not own)
        self.toggle_user_action.setEnabled(bool(record) and not own)
        self.toggle_role_action.setEnabled(bool(record) and not own)
        self.delete_user_action.setEnabled(bool(record) and not own)
        self.copy_user_action.setEnabled(bool(record))
        disabled = bool(record) and not record["enabled"]
        self.toggle_user_action.setText(
            "Enable selected user..." if disabled else "Disable selected user..."
        )
        self.toggle_role_action.setText(
            "Make selected user a regular user..."
            if record and record["role"] == "admin"
            else "Make selected user an administrator..."
        )
        tip = "Your own account: use Change my password, or ask another administrator." if own else ""
        for item in (
            self.reset_password_action, self.toggle_user_action, self.toggle_role_action,
            self.delete_user_action,
        ):
            item.setToolTip(tip)
        device = self.selected(self.devices)
        known = self.device_records.get(device)
        self.revoke_device_action.setEnabled(bool(known) and known["revoked_at"] is None)
        self.copy_device_action.setEnabled(device is not None)

    def _job_folder(self, identifier):
        """The job's real folder on this PC. Opens a folder only; never runs a job's file."""
        from ..files import is_link
        from ..jobs import work_directory

        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError("Not a job ID")
        database = self.settings.root / "server.db"
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ValueError("This job is not on this server.")
        archive = row["archive_path"] if "archive_path" in row.keys() else ""
        folder = work_directory(self.settings, row["work_root"], identifier, archive_path=archive)
        if not folder.is_dir():
            raise ValueError("This job has no folder yet: its files are still being uploaded.")
        if is_link(folder) or is_link(folder.parent):
            raise ValueError("This job's folder is a link, so it was not opened.")
        return folder

    def open_job_folder(self, identifier=None):
        identifier = identifier or self.selected(self.jobs)
        if not identifier or not self.require_admin_registration():
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        def opened(folder):
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                QMessageBox.warning(self, "Job Server", f"Could not open {folder}")

        # Off the GUI thread: a stopped WSL distribution can stall any access to its path.
        self.task(lambda: self._job_folder(identifier), opened)

    @staticmethod
    def selected(table):
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        identifier = item.data(Qt.ItemDataRole.UserRole)
        return identifier if identifier is not None else item.text()

    @staticmethod
    def _stuck(job):
        return job.get("state") == "RUNNING" and str(job.get("reason", "")).startswith("NEEDS_RECHECK")

    def move_finished_jobs(self):
        if not self.require_admin_registration():
            return
        if QMessageBox.question(
            self, "Move finished jobs",
            "Move every job that has already ended into the finished jobs folder, as "
            "<user>/<job name>? Running and waiting jobs are not touched, and clients can "
            "still download the moved results.\n\nThe running server's folder is used: save "
            "and restart first if you have just changed it.",
        ) != QMessageBox.StandardButton.Yes:
            return

        def done(result):
            self.message.setText(
                f"{result['queued']} finished job(s) are being moved to {result['destination']}."
            )
            self.refresh()

        self.task(lambda: self.client.request("POST", "/admin/jobs/move-finished"), done)

    def mark_lost(self):
        identifier = self.selected(self.jobs)
        if not identifier or not self.require_admin_registration():
            return
        if QMessageBox.question(
            self, "Mark job as lost",
            "The server can no longer follow this job, for example after a restart of this PC. "
            "Marking it lost releases its CPU and memory reservation.\n\n"
            f"{self.job_records.get(identifier, {}).get('reason', '')}\n\n"
            "If its calculation might still be running, check first. Mark it lost?",
        ) == QMessageBox.StandardButton.Yes:
            self.task(
                lambda: self.client.request("POST", f"/admin/jobs/{identifier}/mark-lost"),
                lambda _: self.refresh(), success_message="Job marked as lost.",
            )

    def _update_job_actions(self):
        identifier = self.selected(self.jobs)
        record = self.job_records.get(identifier, {})
        pending = record.get("state", record.get("display_state")) in {"PENDING", "QUEUED", "BLOCKED"}
        self.lost_action.setEnabled(self._stuck(record))
        self.detail_action.setEnabled(identifier is not None)
        self.folder_action.setEnabled(identifier is not None)
        self.copy_job_action.setEnabled(identifier is not None)
        self.cancel_action.setEnabled(
            identifier is not None
            and record.get("state", record.get("display_state")) not in TERMINAL_STATES
        )
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
        if (
            job_id
            and self.require_admin_registration()
            and QMessageBox.question(self, "Cancel job", "Stop this job and its child processes?")
            == QMessageBox.StandardButton.Yes
        ):
            self.task(
                lambda: self.client.request("POST", f"/jobs/{job_id}/cancel"),
                lambda _: self.refresh(),
                success_message="Job cancellation requested.",
            )

    def disable_user(self):
        user = self.selected(self.users)
        if (
            user
            and QMessageBox.question(
                self, "Disable user",
                f"Disable {self._user_name(user)}? Their sign-ins and devices stop working at once. "
                "Their queued and running jobs are not cancelled. You can enable the account later.",
            )
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
                self, "Revoke device",
                "This is this window's own device. It stays signed out until you register again "
                "with your password. Revoke it?" if device == self.own_device
                else "Revoke this device key and its active tokens? This cannot be undone; "
                "the device has to register again.",
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
