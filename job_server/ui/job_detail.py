"""Read-only job details with bounded log reads and stale-response rejection."""

from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHeaderView,
    QLabel, QPlainTextEdit, QTableWidget, QTabWidget, QVBoxLayout,
)

from ._qt_callbacks import connect_queued
from .dashboard import Task
from .units import gb

LOG_LIMIT = 65536
FILE_LIMIT = 1000
FOLLOW_MS = 5000
FINISHED = {"DONE", "FAILED", "CANCELLED", "LOST"}


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
        self.shown_job = None
        self.finished_job = False
        # An unfinished job is followed until it ends, without the user pressing Refresh.
        self.follow = QTimer(self)
        self.follow.setInterval(FOLLOW_MS)
        self.follow.timeout.connect(self._follow)
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
        self.folder_button = buttons.addButton("Open job folder", QDialogButtonBox.ButtonRole.ActionRole)
        self.folder_button.clicked.connect(
            lambda: self.job_id and self.dashboard.open_job_folder(self.job_id)
        )
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def show_job(self, identifier, owner):
        if identifier != self.job_id:
            self.invalidate("Loading job details…")
        self.job_id, self.owner = identifier, owner
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def _follow(self):
        if self.isVisible() and not self.finished_job and self.refresh_button.isEnabled():
            self.refresh()

    def invalidate(self, message):
        self.generation += 1
        self.shown_job = None
        self.finished_job = False
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
        # Refreshing the job on screen keeps it there until the answer arrives:
        # blanking every field first made each refresh flash.
        if self.shown_job == self.job_id:
            self.generation += 1
            self.status.setText("Refreshing…")
        else:
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
                "resources": f"CPU {resources.get('cpus_per_task', '—')} · Memory {gb(resources.get('memory_mb'))} GB · Wall time {resources.get('walltime_seconds', '—')} s",
                "dependency": (str(dependency) + (" · any completion" if spec.get("chain_any") else " · must succeed")) if dependency else "None",
                "exit": job.get("exit_code") if job.get("exit_code") is not None else "—",
            }
            for key, value in values.items():
                if self.values[key].text() != str(value):
                    self.values[key].setText(str(value))
            if self.command.toPlainText() != spec.get("command", ""):
                self.command.setPlainText(spec.get("command", ""))
            self._show_log(log.get("text", ""))
            self.dashboard.fill(
                self.files,
                [tuple(str(entry.get(key, "—")) for key in ("path", "size", "sha256"))
                 for entry in files[:FILE_LIMIT]],
            )
            self.shown_job = identifier
            self.finished_job = job.get("state") in FINISHED
            if self.finished_job:
                self.follow.stop()
            elif not self.follow.isActive():
                self.follow.start()
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

    def _show_log(self, text):
        """Replace the log, following its end only if the reader was already there."""
        if self.log.toPlainText() == text:
            return
        bar = self.log.verticalScrollBar()
        first = self.log.document().isEmpty()
        following = first or bar.value() >= bar.maximum() - 2
        position = bar.value()
        self.log.setPlainText(text)
        bar.setValue(bar.maximum() if following else min(position, bar.maximum()))

    def closeEvent(self, event):
        self.follow.stop()
        self.invalidate("Closed")
        super().closeEvent(event)
