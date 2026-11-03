"""Readable GUI values must keep stable IDs for administrative operations."""

from datetime import datetime

import pytest


@pytest.fixture
def dashboard(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from job_server.settings import Settings
    from job_server.ui.dashboard import Dashboard

    app = QApplication.instance() or QApplication([])
    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    dialog = Dashboard(config=config)
    dialog.timer.stop()
    yield dialog
    dialog.close()
    app.processEvents()


def test_account_menu_is_available_on_every_tab(dashboard, monkeypatch):
    assert dashboard.windowTitle() == "Job Server"
    assert [action.text() for action in dashboard.menu_bar.actions()] == [
        "File",
        "Server",
        "Account",
        "Queue",
    ]
    account = dashboard.menu_bar.actions()[2].menu()
    assert account.actions()[0] is dashboard.sign_in_action
    assert dashboard.sign_in_action.text() == "Sign in / register device..."
    assert dashboard.menu_bar.parent() is dashboard
    for index in range(dashboard.tabs.count()):
        dashboard.tabs.setCurrentIndex(index)
        assert dashboard.sign_in_action.isEnabled()
    from PySide6.QtWidgets import QPushButton

    for index in [0, 1]:
        assert not dashboard.tabs.widget(index).findChildren(QPushButton)


def test_refresh_displays_names_status_and_local_access_time(dashboard, monkeypatch):
    dashboard.client.key_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.client.key_path.write_text("synthetic placeholder")
    timestamp = 1700000000
    replies = {
        "/health": {"server_version": "fixture"},
        "/system/resources": {},
        "/admin/jobs": [
            {
                "id": "job-id",
                "owner_id": "user-id",
                "spec": {"name": "test"},
                "display_state": "RUNNING",
            }
        ],
        "/admin/users": [{"id": "user-id", "username": "alice", "role": "user", "enabled": 1}],
        "/admin/devices": [
            {"id": "device-id", "user_id": "user-id", "revoked_at": None, "last_access": timestamp}
        ],
    }
    monkeypatch.setattr(dashboard.client, "request", lambda method, path, **kwargs: replies[path])
    monkeypatch.setattr(dashboard, "task", lambda call, display, **kwargs: display(call()))
    dashboard.refresh()
    assert dashboard.jobs.item(0, 1).text() == "alice"
    assert dashboard.users.item(0, 0).text() == "alice"
    assert dashboard.users.item(0, 2).text() == "Enabled"
    assert dashboard.devices.item(0, 1).text() == "alice"
    assert dashboard.devices.item(0, 2).text() == "Active"
    assert dashboard.devices.item(0, 3).text() == datetime.fromtimestamp(
        timestamp
    ).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    assert dashboard.timestamp(None) == "Never"
    replies["/admin/users"][0]["enabled"] = 0
    replies["/admin/devices"][0]["revoked_at"] = timestamp
    dashboard.refresh()
    assert dashboard.users.item(0, 2).text() == "Disabled"
    assert dashboard.devices.item(0, 2).text() == "Revoked"


def test_refresh_preserves_selected_identity_after_row_reordering(dashboard):
    dashboard.fill(
        dashboard.users,
        [("alice", "user", "Enabled"), ("bob", "user", "Enabled")],
        ["id-a", "id-b"],
    )
    dashboard.users.setCurrentCell(1, 0)
    dashboard.fill(
        dashboard.users,
        [("bob", "user", "Enabled"), ("alice", "user", "Enabled")],
        ["id-b", "id-a"],
    )
    assert dashboard.selected(dashboard.users) == "id-b"
    assert dashboard.users.currentRow() == 0
    dashboard.fill(dashboard.users, [("alice", "user", "Enabled")], ["id-a"])
    assert dashboard.selected(dashboard.users) is None


def test_disable_uses_hidden_id_instead_of_displayed_username(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dashboard.fill(dashboard.users, [("alice", "user", "Enabled")], ["private-user-id"])
    dashboard.users.setCurrentCell(0, 0)
    calls = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        dashboard.client, "request", lambda method, path: calls.append((method, path))
    )
    monkeypatch.setattr(dashboard, "task", lambda call, *args, **kwargs: call())
    dashboard.disable_user()
    assert calls == [("POST", "/admin/users/private-user-id/disable")]


def test_remote_https_url_can_be_configured_from_gui(dashboard):
    from job_server.settings import Settings

    dashboard.public_url.setText("https://compute.example.ts.net/")
    dashboard.save_settings()
    assert Settings.load(dashboard.config).public_url == "https://compute.example.ts.net"
    assert dashboard.settings.public_url == "https://compute.example.ts.net"
    assert dashboard.public_url.text() == "https://compute.example.ts.net"


def test_invalid_settings_do_not_change_saved_or_active_configuration(dashboard):
    previous = dashboard.config.read_bytes()
    original = dashboard.settings
    dashboard.public_url.setText("http://invalid.example")
    dashboard.fields["port"].setValue(8888)
    dashboard.save_settings()
    assert dashboard.config.read_bytes() == previous
    assert dashboard.settings is original
    assert dashboard.settings.port != 8888
    assert dashboard.settings.public_url == ""
    assert "HTTPS" in dashboard.message.text()
