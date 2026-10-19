import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from job_server.auth import Auth
from job_server.client import JobServerClient
from job_server.db import Database
from job_server.errors import ServerError
from job_server.settings import Settings


def test_initial_admin_can_be_created_while_daemon_owns_lock(client):
    settings = client.app.state.settings
    db = Database(settings.root)
    try:
        Auth(db, settings).create_initial_admin("admin", "synthetic-password")
        with pytest.raises(ServerError, match="administrator already exists"):
            Auth(db, settings).create_initial_admin("second", "synthetic-password")
    finally:
        db.close()
    assert client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "synthetic-password"}
    ).status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def test_initial_admin_is_atomic_across_connections(client):
    settings = client.app.state.settings
    databases = [Database(settings.root), Database(settings.root)]
    auths = [Auth(db, settings) for db in databases]
    barrier = threading.Barrier(2)

    def create(index):
        barrier.wait(timeout=10)
        try:
            auths[index].create_initial_admin(f"admin{index}", "synthetic-password")
            return "created"
        except ServerError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(2) as pool:
            assert sorted(pool.map(create, range(2))) == ["admin_exists", "created"]
        assert len(client.app.state.db.rows("SELECT id FROM users WHERE role='admin'")) == 1
    finally:
        for db in databases:
            db.close()


def test_cli_bootstrap_works_while_daemon_is_running(client, monkeypatch):
    import sys
    from job_server.__main__ import main

    monkeypatch.setattr(sys, "argv", ["job-server", "create-admin"])
    monkeypatch.setattr("job_server.__main__.Settings.load", lambda path: client.app.state.settings)
    monkeypatch.setattr("builtins.input", lambda prompt: "admin")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "synthetic-password")
    assert main() == 0
    assert client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "synthetic-password"}
    ).status_code == 200


def test_missing_device_file_has_actionable_message(tmp_path):
    client = JobServerClient("http://127.0.0.1:8766", tmp_path / "absent.key")
    with pytest.raises(ValueError, match="Sign in / register device"):
        client.authenticate()


@pytest.fixture
def dashboard(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from job_server.ui.dashboard import Dashboard

    app = QApplication.instance() or QApplication([])
    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    dialog = Dashboard(config=config)
    dialog.timer.stop()
    yield dialog, app
    dialog.close()
    app.processEvents()


def test_stop_and_add_user_without_key_show_registration_help(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dialog, app = dashboard
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(dialog.client, "request", lambda *args: pytest.fail("No request expected"))
    monkeypatch.setattr(dialog, "credentials", lambda *args: pytest.fail("No new-user prompt"))
    dialog.stop_daemon()
    dialog.add_user()
    assert len(warnings) == 2
    assert all("Sign in / register device" in warning[2] for warning in warnings)


@pytest.mark.parametrize("accepted", [True, False])
def test_username_and_password_share_one_dialog(dashboard, monkeypatch, accepted):
    from PySide6.QtWidgets import QDialog, QLineEdit

    dialog, app = dashboard
    calls = []

    def enter(form):
        calls.append(form)
        username = form.findChild(QLineEdit, "username")
        password = form.findChild(QLineEdit, "password")
        assert password.echoMode() == QLineEdit.EchoMode.Password
        username.setText(" alice ")
        password.setText("synthetic-password")
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", enter)
    assert dialog.credentials("Sign in") == (("alice", "synthetic-password") if accepted else None)
    assert len(calls) == 1
    assert calls[0].findChild(QLineEdit, "password").text() == ""


def test_interactive_error_remains_in_dialog_and_success_is_explicit(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dialog, app = dashboard
    warnings, successes = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: successes.append(args))

    def finish():
        deadline = time.monotonic() + 5
        while dialog.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not dialog.busy

    def failed():
        raise ValueError("synthetic failure")

    dialog.task(failed)
    finish()
    assert warnings[-1][2] == "synthetic failure"
    dialog.task(failed, interactive=False)
    finish()
    assert len(warnings) == 1
    dialog.task(lambda: None, success_message="User created.")
    finish()
    assert successes[-1][1:] == ("Success", "User created.")
