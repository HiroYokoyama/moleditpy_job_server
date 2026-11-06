"""Account dialogs. Passwords stay in these fields only until the dialog closes."""

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
)


class PasswordDialog(QDialog):
    """A new password entered twice, with the current one when changing your own."""

    def __init__(self, parent, title, intro, *, ask_current=False, revoke_label=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        layout = QFormLayout(self)
        explanation = QLabel(intro)
        explanation.setWordWrap(True)
        layout.addRow(explanation)
        self.current = self._password_field("current_password") if ask_current else None
        if self.current is not None:
            layout.addRow("Current password", self.current)
        self.new = self._password_field("new_password")
        self.repeat = self._password_field("repeat_password")
        layout.addRow("New password", self.new)
        layout.addRow("Repeat new password", self.repeat)
        self.revoke = QCheckBox(revoke_label)
        layout.addRow(self.revoke)
        self.hint = QLabel()
        layout.addRow(self.hint)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        for field in (self.current, self.new, self.repeat):
            if field is not None:
                field.textChanged.connect(self._validate)
        self._validate()

    def _password_field(self, name):
        field = QLineEdit(self)
        field.setObjectName(name)
        field.setEchoMode(QLineEdit.EchoMode.Password)
        return field

    def _validate(self):
        new, repeat = self.new.text(), self.repeat.text()
        missing = self.current is not None and not self.current.text()
        differs = bool(repeat) and new != repeat
        same = self.current is not None and bool(new) and new == self.current.text()
        self.hint.setText(
            "The passwords differ." if differs
            else "Choose a password different from the current one." if same else ""
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(new) and new == repeat and not missing and not same
        )

    def clear_fields(self):
        for field in (self.current, self.new, self.repeat):
            if field is not None:
                field.clear()


def ask_password(parent, title, intro, *, ask_current=False, revoke_label=""):
    """Return (current, new, revoke_devices), or None when cancelled."""
    dialog = PasswordDialog(
        parent, title, intro, ask_current=ask_current, revoke_label=revoke_label,
    )
    result = None
    if dialog.exec() == QDialog.DialogCode.Accepted:
        result = (
            dialog.current.text() if dialog.current is not None else None,
            dialog.new.text(),
            dialog.revoke.isChecked(),
        )
    dialog.clear_fields()
    dialog.deleteLater()
    return result


class NewUserDialog(QDialog):
    """Username, password entered twice, and whether this is a user or an administrator."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add user")
        self.setMinimumWidth(420)
        layout = QFormLayout(self)
        self.username = QLineEdit(self)
        self.username.setObjectName("username")
        self.username.setMaxLength(64)
        self.role = QComboBox(self)
        self.role.addItem("User", "user")
        self.role.addItem("Administrator", "admin")
        self.password = QLineEdit(self)
        self.password.setObjectName("password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.repeat = QLineEdit(self)
        self.repeat.setObjectName("repeat_password")
        self.repeat.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Username", self.username)
        layout.addRow("Account type", self.role)
        layout.addRow("Password", self.password)
        layout.addRow("Repeat password", self.repeat)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addRow(self.note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        for field in (self.username, self.password, self.repeat):
            field.textChanged.connect(self._validate)
        self.role.currentIndexChanged.connect(self._validate)
        self._validate()

    def _validate(self):
        differs = bool(self.repeat.text()) and self.password.text() != self.repeat.text()
        admin = self.role.currentData() == "admin"
        self.note.setText(
            "The passwords differ." if differs
            else "Administrators can manage every user and job on this server." if admin else ""
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.username.text().strip()) and bool(self.password.text())
            and self.password.text() == self.repeat.text()
        )


def ask_new_user(parent):
    """Return (username, password, role), or None when cancelled."""
    dialog = NewUserDialog(parent)
    result = None
    if dialog.exec() == QDialog.DialogCode.Accepted:
        result = (dialog.username.text().strip(), dialog.password.text(), dialog.role.currentData())
    dialog.password.clear()
    dialog.repeat.clear()
    dialog.deleteLater()
    return result
