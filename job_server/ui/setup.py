"""Local first-run setup; opening the guide never starts a listener."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from .units import GBSpinBox


class SetupDialog(QDialog):
    def __init__(self, dashboard):
        super().__init__(dashboard)
        self.dashboard = dashboard
        self.initial_admin = not dashboard.has_administrator()
        self.created = False
        self.processing = False
        self.setWindowTitle("Job Server setup")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(530, 520)
        layout = QVBoxLayout(self)

        def note(text):
            label = QLabel(text)
            label.setWordWrap(True)
            layout.addWidget(label)

        note("Set up this server PC. Jobs run as its OS account; allow trusted users only.")
        note("1. Connection — local access works without extra configuration.")
        form = QFormLayout()
        self.address = QLineEdit(dashboard.public_url.text())
        self.address.setPlaceholderText("Optional: https://your-pc.your-tailnet.ts.net")
        form.addRow("Remote Web address", self.address)
        layout.addLayout(form)
        note("For Tailscale, run the command below on this PC and paste its HTTPS URL above. "
             "Use private Tailscale Serve; never Funnel or public port forwarding.")
        command = QLineEdit(f"tailscale serve --bg http://127.0.0.1:{dashboard.settings.port}")
        command.setReadOnly(True)
        layout.addWidget(command)
        note("2. Resources — choose how much this server may reserve for jobs.")
        resources = QFormLayout()
        self.cpus = QSpinBox()
        self.cpus.setRange(1, 4096)
        self.cpus.setValue(dashboard.fields["cpus"].value())
        self.memory = GBSpinBox(2147483647)
        self.memory.set_mb(dashboard.fields["memory_mb"].mb())
        self.python = QLineEdit(dashboard.python_path.text())
        self.python.setPlaceholderText("Automatic; select a Python executable for portable builds")
        resources.addRow("CPU budget", self.cpus)
        resources.addRow("Memory budget", self.memory)
        resources.addRow("Daemon Python", self.python)
        layout.addLayout(resources)
        self.username, self.password, self.confirm = QLineEdit(), QLineEdit(), QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        if self.initial_admin:
            note("3. Administrator — create the account used to manage users and jobs.")
            admin = QFormLayout()
            admin.addRow("Username", self.username)
            admin.addRow("Password", self.password)
            admin.addRow("Confirm password", self.confirm)
            layout.addLayout(admin)
            note("The button below saves these settings, creates the administrator, starts the "
                 "server and signs in this window. Desktop credentials stay on this PC.")
        else:
            note("3. Administrator — already configured. Use Account > Sign in to access this "
                 "window. After saving changes, choose Server > Restart to apply them.")
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.addButton(
            "Create, start and sign in" if self.initial_admin else "Save configuration",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.finished.connect(self._finished)

    def _finished(self, _):
        self.password.clear()
        self.confirm.clear()
        self.dashboard.timer.start()

    def reject(self):
        if not self.processing:
            super().reject()

    def apply(self):
        dashboard = self.dashboard
        if self.processing or dashboard.busy:
            self.status.setText("Wait for the current operation, then try again.")
            return
        credentials = (self.username.text().strip(), self.password.text())
        if self.initial_admin and (
            not credentials[0] or not credentials[1] or credentials[1] != self.confirm.text()
        ):
            self.status.setText("Enter a username and a nonempty password; both passwords must match.")
            return
        dashboard.public_url.setText(self.address.text().strip())
        dashboard.fields["cpus"].setValue(self.cpus.value())
        dashboard.fields["memory_mb"].set_mb(self.memory.mb())
        dashboard.python_path.setText(self.python.text().strip())
        if not dashboard.save_settings():
            self.status.setText(dashboard.message.text())
            return
        if not self.initial_admin:
            self.accept()
            return
        self.processing = True
        self.buttons.setEnabled(False)
        self.status.setText("Creating administrator and signing in…")
        self.password.clear()
        self.confirm.clear()

        def work():
            if not self.created:
                dashboard.create_administrator(credentials)
                self.created = True
            dashboard._start_and_sign_in(credentials)

        def done(_):
            self.processing = False
            dashboard.message.setText("Server is running. This window is signed in.")
            self.accept()
            dashboard.refresh()

        def failed(message):
            self.processing = False
            self.buttons.setEnabled(True)
            self.status.setText(message + (" Administrator created; enter its password to retry sign-in." if self.created else ""))

        dashboard.task(work, done, interactive=False, on_error=failed)
