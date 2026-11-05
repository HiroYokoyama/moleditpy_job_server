"""First-run setup is local, explicit and does not bootstrap twice on retry."""

import pytest

from job_server.db import Database
from job_server.settings import Settings


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
    yield dialog
    dialog.close()
    app.processEvents()


def test_first_open_offers_setup_without_creating_admin_or_starting_server(dashboard, monkeypatch):
    monkeypatch.setattr(dashboard, "isVisible", lambda: True)
    monkeypatch.setattr(dashboard, "_start_daemon", lambda: pytest.fail("No automatic listener"))
    dashboard._first_setup()
    assert dashboard.setup_dialog.isVisible()
    assert not dashboard.timer.isActive()
    assert not (dashboard.settings.root / "server.db").exists()
    dashboard.setup_dialog.reject()
    assert not dashboard.setup_dialog.isVisible()


def test_configured_server_skips_automatic_setup_but_guide_remains_available(dashboard, monkeypatch):
    dashboard.create_administrator(("admin", "x"))
    monkeypatch.setattr(dashboard, "isVisible", lambda: True)
    dashboard._first_setup()
    assert dashboard.setup_dialog is None
    dashboard.open_setup()
    assert not dashboard.setup_dialog.initial_admin
    assert dashboard.setup_dialog.apply_button.text() == "Save configuration"


def test_setup_saves_an_explicit_lan_listener(dashboard):
    dashboard.create_administrator(("fixture-admin", "synthetic-password"))
    dashboard.open_setup()
    guide = dashboard.setup_dialog
    guide.allow_lan_http.setChecked(True)
    guide.lan_addresses.select(["192.168.1.20"])
    guide.apply()
    assert not guide.isVisible()
    saved = Settings.load(dashboard.config)
    assert saved.allow_lan_http and saved.lan_bind_address == "192.168.1.20"


def test_setup_validates_then_creates_and_signs_in_without_second_password_prompt(dashboard, monkeypatch):
    dashboard.open_setup()
    guide = dashboard.setup_dialog
    guide.username.setText("admin")
    guide.password.setText("x")
    guide.confirm.setText("wrong")
    calls = []
    monkeypatch.setattr(dashboard, "_start_and_sign_in", lambda credentials: calls.append(credentials))
    monkeypatch.setattr(dashboard, "refresh", lambda: None)
    monkeypatch.setattr(dashboard, "task", lambda work, done, **kwargs: done(work()))
    guide.apply()
    assert not calls
    assert not dashboard.has_administrator()
    guide.confirm.setText("x")
    guide.addresses.setPlainText("https://compute.example.ts.net/")
    guide.apply()
    assert calls == [("admin", "x")]
    assert dashboard.has_administrator()
    assert Settings.load(dashboard.config).connection_urls == ["https://compute.example.ts.net"]
    assert guide.password.text() == guide.confirm.text() == ""
    assert not guide.isVisible()


def test_setup_failed_start_retries_sign_in_without_creating_another_admin(dashboard, monkeypatch):
    dashboard.open_setup()
    guide = dashboard.setup_dialog
    guide.username.setText("admin")
    starts = []

    def start(credentials):
        starts.append(credentials)
        if len(starts) == 1:
            raise OSError("synthetic runtime failure")

    def task(work, done, on_error, **kwargs):
        try:
            value = work()
        except Exception as exc:
            on_error(str(exc))
        else:
            done(value)

    monkeypatch.setattr(dashboard, "_start_and_sign_in", start)
    monkeypatch.setattr(dashboard, "refresh", lambda: None)
    monkeypatch.setattr(dashboard, "task", task)
    for _ in range(2):
        guide.password.setText("x")
        guide.confirm.setText("x")
        guide.apply()
    assert len(starts) == 2
    db = Database(dashboard.settings.root)
    try:
        assert len(db.rows("SELECT id FROM users WHERE role='admin'")) == 1
    finally:
        db.close()
    assert not guide.isVisible()


def test_account_bootstrap_signs_in_in_same_operation(dashboard, monkeypatch):
    monkeypatch.setattr(dashboard, "credentials", lambda title: ("admin", "x"))
    called = []
    monkeypatch.setattr(dashboard, "_start_and_sign_in", lambda credentials: called.append(credentials))
    monkeypatch.setattr(dashboard, "refresh", lambda: None)
    monkeypatch.setattr(dashboard, "task", lambda work, done, **kwargs: done(work()))
    dashboard.bootstrap()
    assert dashboard.has_administrator()
    assert called == [("admin", "x")]
