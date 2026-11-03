"""Read-only job details with bounded log reads and stale-response rejection."""

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHeaderView,
    QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
)

from ._qt_callbacks import connect_queued
from .dashboard import Task

LOG_LIMIT = 65536
FILE_LIMIT = 1000


class JobDetailDialog(QDialog):
    def __init__(self, dashboard):
        super().__init__(dashboard)
        self.dashboard = dashboard
        self.setWindowTitle("Job details")
        self.resize(800, 610)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._tasks = set()
        self.generation = 0
        self.job_id = None
        self.owner = "—"
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.values = {}
        for key, title in (
            ("name", "Name"), ("state", "State"), ("owner", "Owner"), ("id", "Job ID"),
            ("priority", "Priority"), ("force", "Force run"), ("resources", "Resources"),
            ("dependency", "After job"), ("exit", "Exit code"),
        ):
            value = QLabel("—")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self.values[key] = value
            form.addRow(title, value)
        layout.addLayout(form)
        self.tabs = QTabWidget()
        self.command = QPlainTextEdit()
        self.command.setReadOnly(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("No output yet. Refresh to read the latest 64 KiB.")
        self.files = QTableWidget(0, 3)
        self.files.setHorizontalHeaderLabels(["Result file", "Bytes", "SHA-256"])
        self.files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files.setWordWrap(False)
        self.files.verticalHeader().setVisible(False)
        self.files.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.files.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.files.setColumnWidth(2, 280)
        self.tabs.addTab(self.command, "Command")
        self.tabs.addTab(self.log, "Latest output (64 KiB)")
        self.tabs.addTab(self.files, "Result files")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.refresh_button = buttons.addButton("Refresh", QDialogButtonBox.ButtonRole.ActionRole)
        self.refresh_button.clicked.connect(self.refresh)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def show_job(self, identifier, owner):
        self.job_id, self.owner = identifier, owner
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def invalidate(self, message):
        self.generation += 1
        for value in self.values.values():
            value.setText("—")
        self.command.clear()
        self.log.clear()
        self.files.setRowCount(0)
        self.status.setText(message)
        self.refresh_button.setEnabled(True)

    def refresh(self):
        if self.job_id is None:
            return
        self.invalidate("Loading job details…")
        self.refresh_button.setEnabled(False)
        generation, identifier, owner = self.generation, self.job_id, self.owner
        client = self.dashboard.client

        def work():
            job = client.request("GET", f"/jobs/{identifier}")
            metadata = client.request("GET", f"/jobs/{identifier}/log?limit=1")
            offset = max(0, metadata.get("size", 0) - LOG_LIMIT)
            log = client.request("GET", f"/jobs/{identifier}/log?offset={offset}&limit={LOG_LIMIT}")
            files = client.request("GET", f"/jobs/{identifier}/files")
            return job, log, files

        task = Task(work)
        self._tasks.add(task)

        def current():
            return generation == self.generation and client is self.dashboard.client and self.isVisible()

        def finished(data):
            self._tasks.discard(task)
            if not current():
                return
            job, log, files = data
            spec = job["spec"]
            resources = spec.get("resources", {})
            dependency = spec.get("after_job")
            values = {
                "name": spec.get("name", "—"), "state": job.get("display_state", job["state"]),
                "owner": owner, "id": job["id"], "priority": job.get("priority", 0),
                "force": "Requested; dependencies still apply" if job.get("force_run") else "Not requested",
                "resources": f"CPU {resources.get('cpus_per_task', '—')} · Memory {resources.get('memory_mb', '—')} MB · Wall time {resources.get('walltime_seconds', '—')} s",
                "dependency": (str(dependency) + (" · any completion" if spec.get("chain_any") else " · must succeed")) if dependency else "None",
                "exit": job.get("exit_code") if job.get("exit_code") is not None else "—",
            }
            for key, value in values.items():
                self.values[key].setText(str(value))
            self.command.setPlainText(spec.get("command", ""))
            self.log.setPlainText(log.get("text", ""))
            self.files.setRowCount(min(len(files), FILE_LIMIT))
            for row, entry in enumerate(files[:FILE_LIMIT]):
                for column, key in enumerate(("path", "size", "sha256")):
                    self.files.setItem(row, column, QTableWidgetItem(str(entry.get(key, "—"))))
            self.status.setText(
                f"Latest output from byte {log.get('offset', 0)}. "
                + (f"Showing the first {FILE_LIMIT} of {len(files)} files." if len(files) > FILE_LIMIT else f"{len(files)} result files.")
            )
            self.refresh_button.setEnabled(True)

        def failed(message):
            self._tasks.discard(task)
            if current():
                self.invalidate(message)

        cleanup = (task.signals.finished, task.signals.failed)
        connect_queued(task.signals.finished, finished, self, (object,), cleanup=cleanup)
        connect_queued(task.signals.failed, failed, self, (str,), cleanup=cleanup)
        self.pool.start(task)

    def closeEvent(self, event):
        self.invalidate("Closed")
        super().closeEvent(event)
