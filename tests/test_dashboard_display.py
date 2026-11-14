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


def test_multiple_addresses_and_http_opt_in_save_together(dashboard):
    from job_server.settings import Settings

    dashboard.public_urls.setPlainText(
        "https://compute.example.ts.net/\nhttp://192.168.1.2:8080/\nhttps://other.example.ts.net"
    )
    assert not dashboard.save_settings()
    dashboard.allow_lan_http.setChecked(True)
    assert dashboard.save_settings()
    saved = Settings.load(dashboard.config)
    assert saved.allow_lan_http
    assert len(saved.connection_urls) == 3


def test_copy_connection_addresses_uses_saved_origins(dashboard):
    from PySide6.QtWidgets import QApplication

    dashboard.public_urls.setPlainText("https://compute.example.ts.net/")
    assert dashboard.save_settings()
    dashboard.public_urls.setPlainText("https://unsaved.example.org")
    dashboard.copy_connection_addresses()
    assert QApplication.clipboard().text() == "https://compute.example.ts.net"


def test_lan_selection_saves_and_copies_the_direct_url(dashboard):
    from PySide6.QtWidgets import QApplication
    from job_server.settings import Settings

    assert dashboard.lan_addresses.isHidden()
    dashboard.allow_lan_http.setChecked(True)
    assert not dashboard.lan_addresses.isHidden()
    dashboard.lan_addresses.select(["192.168.1.20"])
    assert "http://192.168.1.20:8766" in dashboard.connect_urls.text()
    assert dashboard.save_settings()
    saved = Settings.load(dashboard.config)
    assert saved.lan_bind_address == "192.168.1.20"
    dashboard.copy_connection_addresses()
    assert "http://192.168.1.20:8766" in QApplication.clipboard().text()
    dashboard.allow_lan_http.setChecked(False)
    assert dashboard.save_settings()
    assert Settings.load(dashboard.config).lan_bind_address == ""


def test_legacy_primary_and_additional_urls_share_one_field(dashboard):
    from job_server.settings import Settings
    from job_server.ui.dashboard import Dashboard

    Settings(
        data_dir=str(dashboard.settings.root),
        public_url="https://primary.example.org",
        public_urls=["https://additional.example.org"],
    ).save(dashboard.config)
    reopened = Dashboard(config=dashboard.config)
    reopened.timer.stop()
    try:
        assert reopened.public_urls.toPlainText().splitlines() == [
            "https://primary.example.org", "https://additional.example.org",
        ]
        assert reopened.save_settings()
        saved = Settings.load(dashboard.config)
        assert saved.public_url == ""
        assert saved.public_urls == [
            "https://primary.example.org", "https://additional.example.org",
        ]
    finally:
        reopened.close()


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
    # Every button on the account tab is also in the menu and the right-click menu.
    for entry in (dashboard.add_user_action, dashboard.delete_user_action, dashboard.reset_password_action):
        assert entry in account.actions() and entry in dashboard._context_actions(dashboard.users)


def test_start_is_queued_during_refresh_and_runs_once(dashboard, monkeypatch):
    import threading
    import time
    from PySide6.QtWidgets import QApplication

    started, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(dashboard, "refresh", lambda: None)
    monkeypatch.setattr(dashboard, "_start_daemon", lambda: calls.append("start") or "Server is running.")

    def background():
        started.set()
        assert release.wait(5)

    dashboard.task(background, interactive=False)
    try:
        assert started.wait(2)
        dashboard.start_daemon()
        dashboard.start_daemon()
        assert not calls
        assert not dashboard.start_action.isEnabled()
        assert not dashboard.save_settings_button.isEnabled()
        assert "queued" in dashboard.connection_status.text()
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while dashboard.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert calls == ["start"]
    assert dashboard.start_action.isEnabled()
    assert dashboard.save_settings_button.isEnabled()
    assert dashboard.status_level == "online"


def test_start_failure_restores_controls_and_reports_error(dashboard, monkeypatch):
    import time
    from PySide6.QtWidgets import QApplication, QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    def failed_start():
        raise RuntimeError("Server exited during startup")

    monkeypatch.setattr(dashboard, "_start_daemon", failed_start)
    dashboard.start_daemon()
    assert "Starting" in dashboard.connection_status.text()
    assert not dashboard.start_action.isEnabled()
    deadline = time.monotonic() + 5
    while dashboard.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert dashboard.start_action.isEnabled()
    assert "failed" in dashboard.connection_status.text()
    assert warnings and "exited during startup" in dashboard.message.text()


def test_offline_refresh_is_a_normal_off_state(dashboard, monkeypatch):
    from urllib.error import URLError

    def offline(*args, **kwargs):
        raise URLError(ConnectionRefusedError())

    monkeypatch.setattr(dashboard.client, "request", offline)
    monkeypatch.setattr(dashboard, "task", lambda work, display, **kwargs: display(work()))
    dashboard.refresh()
    assert dashboard.connection_status.text() == "Server is OFF"
    assert dashboard.message.text() == "Server is OFF. Choose Server > Start."


def test_restart_waits_for_stop_before_starting(dashboard, monkeypatch):
    import time
    from PySide6.QtWidgets import QApplication

    calls = []
    monkeypatch.setattr(dashboard, "require_admin_registration", lambda: True)
    monkeypatch.setattr("job_server.lifecycle.stop_and_wait", lambda *args: calls.append("stop"))
    monkeypatch.setattr(dashboard, "_start_daemon", lambda: calls.append("start") or "Server is running.")
    monkeypatch.setattr(dashboard, "refresh", lambda: None)
    dashboard.restart_daemon()
    assert "Restarting" in dashboard.connection_status.text()
    deadline = time.monotonic() + 5
    while dashboard.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert calls == ["stop", "start"]
    assert dashboard.restart_action.isEnabled()


def test_refresh_displays_names_status_and_local_access_time(dashboard, monkeypatch):
    dashboard.client.key_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.client.key_path.write_text("synthetic placeholder")
    timestamp = 1700000000
    replies = {
        "/health": {"server_version": "fixture"},
        "/me": {"user_id": "other", "username": "admin", "role": "admin"},
        "/system/resources": {"running": 1, "pending": 2, "reserved_cpus": 2, "cpus": 8,
                              "reserved_memory_mb": 1024, "memory_mb": 4096, "paused": False},
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
    assert dashboard.metric_values["cpu"].text() == "2 / 8"
    assert dashboard.metric_values["memory"].text() == "1.0 / 4.0 GiB"
    assert dashboard.metric_values["running"].text() == "1"
    dashboard.jobs.setCurrentCell(0, 0)
    assert dashboard.selected(dashboard.jobs) == "job-id"
    assert dashboard.jobs.item(0, 0).text() == "test"
    assert dashboard.jobs.item(0, 4).text() == "—"  # This reply carries no resources.
    assert dashboard.jobs.item(0, 6).text() == "job-id"
    assert dashboard.users.item(0, 0).text() == "alice"
    assert dashboard.users.item(0, 2).text() == "Enabled"
    assert dashboard.devices.item(0, 1).text() == "alice"
    assert dashboard.devices.item(0, 2).text() == "Active"
    assert dashboard.devices.item(0, 3).text() == datetime.fromtimestamp(
        timestamp
    ).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    assert dashboard.timestamp(None) == "Never"
    replies["/admin/users"][0]["enabled"] = 0
    replies["/admin/devices"][0]["revoked_at"] = timestamp
    dashboard.refresh()
    assert dashboard.users.item(0, 2).text() == "Disabled"
    assert dashboard.devices.item(0, 2).text() == "Revoked"
    dashboard.client.key_path.unlink()
    dashboard.refresh()
    assert dashboard.metric_values["cpu"].text() == "—"
    assert dashboard.jobs.rowCount() == dashboard.users.rowCount() == dashboard.devices.rowCount() == 0


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

    dashboard.public_urls.setPlainText("https://compute.example.ts.net/")
    dashboard.save_settings()
    assert Settings.load(dashboard.config).connection_urls == ["https://compute.example.ts.net"]
    assert dashboard.settings.connection_urls == ["https://compute.example.ts.net"]
    assert dashboard.public_urls.toPlainText() == "https://compute.example.ts.net"


def test_invalid_settings_do_not_change_saved_or_active_configuration(dashboard):
    previous = dashboard.config.read_bytes()
    original = dashboard.settings
    dashboard.public_urls.setPlainText("http://invalid.example")
    dashboard.fields["port"].setValue(8888)
    dashboard.save_settings()
    assert dashboard.config.read_bytes() == previous
    assert dashboard.settings is original
    assert dashboard.settings.port != 8888
    assert dashboard.settings.public_url == ""
    assert "HTTPS" in dashboard.message.text()


def test_queue_actions_use_stable_job_id_and_explicit_force_confirmation(dashboard, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    dashboard.client.key_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.client.key_path.write_text("synthetic placeholder")
    dashboard.fill(dashboard.jobs, [("friendly name", "alice", "PENDING", 0, "job-id")], ["job-id"])
    dashboard.jobs.setCurrentCell(0, 0)
    dashboard.job_records = {"job-id": {"state": "PENDING", "priority": 0, "force_run": False}}
    dashboard._update_job_actions()
    assert dashboard.priority_action.isEnabled() and dashboard.force_action.isEnabled()
    assert not dashboard.unforce_action.isEnabled()
    calls, confirmations = [], []
    monkeypatch.setattr(QInputDialog, "getInt", lambda *args: (7, True))
    monkeypatch.setattr(dashboard.client, "request", lambda *args: calls.append(args))
    monkeypatch.setattr(dashboard, "task", lambda call, *args, **kwargs: call())

    def confirm(*args):
        confirmations.append(args[2])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    dashboard.set_priority()
    dashboard.force_run()
    dashboard.force_run(False)
    assert calls == [
        ("POST", "/admin/jobs/job-id/priority", {"priority": 7}),
        ("POST", "/admin/jobs/job-id/force-run", {"force_run": True}),
        ("POST", "/admin/jobs/job-id/force-run", {"force_run": False}),
    ]
    assert len(confirmations) == 1
    assert all(word in confirmations[0] for word in ("paused", "CPU", "memory", "Dependencies"))
    dashboard.job_records["job-id"]["state"] = "RUNNING"
    dashboard._update_job_actions()
    assert not dashboard.priority_action.isEnabled() and not dashboard.force_action.isEnabled()
    assert "ignoring budget" in dashboard.force_action.text()
    assert dashboard.force_action in dashboard._context_actions(dashboard.jobs)


def test_force_run_says_when_the_job_still_waits_for_another(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    dashboard.client.key_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.client.key_path.write_text("synthetic placeholder")
    dashboard.fill(dashboard.jobs, [("friendly name", "alice", "BLOCKED", 0, "job-id")], ["job-id"])
    dashboard.jobs.setCurrentCell(0, 0)
    messages = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(dashboard, "task", lambda call, *args, **kwargs: messages.append(kwargs["success_message"]))
    dashboard.job_records = {"job-id": {"state": "PENDING", "spec": {"after_job": "earlier-id"}}}
    dashboard.force_run()
    dashboard.job_records = {"job-id": {"state": "PENDING", "spec": {"after_job": None}}}
    dashboard.force_run()
    assert "waits for job earlier-id" in messages[0]
    assert "starts within a few seconds" in messages[1]


def test_queue_force_cancel_and_priority_dialog_cancel_make_no_request(dashboard, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    monkeypatch.setattr(dashboard, "require_admin_registration", lambda: True)
    dashboard.fill(dashboard.jobs, [("name", "alice", "PENDING", 0, "job-id")], ["job-id"])
    dashboard.jobs.setCurrentCell(0, 0)
    monkeypatch.setattr(QInputDialog, "getInt", lambda *args: (7, False))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    monkeypatch.setattr(dashboard.client, "request", lambda *args: pytest.fail("Cancelled action made a request"))
    dashboard.set_priority()
    dashboard.force_run()


def test_refresh_preserves_user_column_widths(dashboard):
    dashboard.jobs.setColumnWidth(1, 175)
    rows = [("long name" * 20, "alice", "PENDING", 0, "job-id")]
    dashboard.fill(dashboard.jobs, rows, ["job-id"])
    dashboard.fill(dashboard.jobs, rows, ["job-id"])
    assert dashboard.jobs.columnWidth(1) == 175
    assert not dashboard.jobs.wordWrap()


def test_status_banner_reports_each_server_state(dashboard, monkeypatch):
    assert dashboard.status_level == "unknown"
    monkeypatch.setattr(dashboard, "task", lambda call, display, **kwargs: (
        kwargs["on_error"]("refused") if fail else display(call())))
    fail = True
    dashboard.refresh()
    assert dashboard.status_level == "offline"
    assert dashboard.connection_status.text() == "Server offline"
    assert "Server > Start" in dashboard.resources.text()
    assert dashboard.client.url in dashboard.status_detail.text()

    fail = False
    replies = {"/health": {"server_version": "9.9.9"}}
    monkeypatch.setattr(dashboard.client, "request", lambda method, path, **kwargs: replies[path])
    dashboard.refresh()
    assert dashboard.status_level == "warning"
    assert "not signed in" in dashboard.connection_status.text()
    assert "v9.9.9" in dashboard.status_detail.text()

    dashboard.client.key_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.client.key_path.write_text("synthetic placeholder")
    dashboard.refresh()  # /system/resources is missing: the signed-in calls fail.
    assert dashboard.status_level == "warning"
    assert "sign-in failed" in dashboard.connection_status.text()

    replies.update({
        "/system/resources": {"running": 1, "pending": 0, "reserved_cpus": 6, "cpus": 8,
                              "reserved_memory_mb": 1000, "memory_mb": 1000, "paused": False},
        "/admin/jobs": [{"id": "j", "owner_id": "u", "spec": {"name": "opt"},
                         "display_state": "RUNNING"}],
        "/admin/users": [], "/admin/devices": [],
        "/me": {"user_id": "u", "username": "admin", "role": "admin"},
    })
    dashboard.refresh()
    assert dashboard.status_level == "online"
    assert "accepting jobs" in dashboard.connection_status.text()
    assert dashboard.metric_bars["cpu"].value() == 75
    assert dashboard.metric_bars["memory"].value() == 100
    assert dashboard.jobs.item(0, 2).font().bold()
    replies["/system/resources"]["paused"] = True
    dashboard.refresh()
    assert dashboard.status_level == "warning"
    assert "queue paused" in dashboard.connection_status.text()


def test_memory_and_storage_are_edited_in_gb_but_saved_in_mb(dashboard):
    from job_server.settings import Settings
    from job_server.ui.units import gb

    assert gb(16384) == "16.0" and gb(512) == "0.5" and gb(1000) == "1.0" and gb(1300) == "1.3"
    assert gb(None) == "—"
    assert dashboard.fields["memory_mb"].decimals() == 1
    assert dashboard.fields["memory_mb"].suffix() == " GiB"
    dashboard.fields["memory_mb"].setValue(16)
    dashboard.fields["storage_quota_mb"].setValue(2.5)
    assert dashboard.save_settings()
    saved = Settings.load(dashboard.config)
    assert saved.memory_mb == 16384
    assert saved.storage_quota_mb == 2560


def test_finished_folder_time_prefix_is_on_and_can_be_saved_off(dashboard):
    from job_server.settings import Settings

    assert dashboard.finished_date_prefix.isChecked()
    dashboard.finished_date_prefix.setChecked(False)
    assert dashboard.save_settings()
    assert Settings.load(dashboard.config).finished_date_prefix is False


def test_job_working_directory_is_saved_and_default_stays_empty(dashboard, tmp_path):
    from job_server.settings import Settings

    assert dashboard.job_dir.text() == ""
    assert str(dashboard.settings.root) in dashboard.job_dir.placeholderText()
    dashboard.job_dir.setText(str(tmp_path / "work"))
    assert dashboard.save_settings()
    assert Settings.load(dashboard.config).job_dir == str(tmp_path / "work")
    dashboard.job_dir.setText("relative")
    assert not dashboard.save_settings()
    assert Settings.load(dashboard.config).job_dir == str(tmp_path / "work")


def test_network_rows_appear_only_when_they_apply(dashboard):
    assert dashboard.fields["tls_port"].isHidden()
    assert dashboard.ca_button.isHidden()
    assert dashboard.lan_addresses.isHidden()
    dashboard.allow_lan_http.setChecked(True)
    assert not dashboard.lan_addresses.isHidden()
    assert dashboard.fields["tls_port"].isHidden()
    dashboard.lan_tls.setChecked(True)
    assert not dashboard.fields["tls_port"].isHidden()
    assert not dashboard.ca_button.isHidden()


def test_several_addresses_and_a_local_name_are_saved(dashboard):
    from job_server.settings import Settings

    dashboard.lan_tls.setChecked(True)
    dashboard.lan_addresses.select(["192.168.1.20", "10.0.0.5"], "your-pc.local")
    assert "https://your-pc.local:8767" in dashboard.connect_urls.text()
    assert dashboard.save_settings()
    saved = Settings.load(dashboard.config)
    assert set(saved.lan_addresses) == {"192.168.1.20", "10.0.0.5"}
    assert saved.lan_hostname == "your-pc.local"
    assert dashboard.public_urls.toPlainText() == ""


def test_only_one_local_name_can_be_ticked(dashboard):
    from PySide6.QtCore import Qt

    dashboard.lan_tls.setChecked(True)
    dashboard.lan_addresses.select(["192.168.1.20"], "first.local")
    second = dashboard.lan_addresses.add_entry("second.local")
    second.setCheckState(Qt.CheckState.Checked)
    assert dashboard.lan_addresses.hostname() == "second.local"


def test_local_name_follows_the_enabled_schemes(dashboard):
    dashboard.allow_lan_http.setChecked(True)
    dashboard.lan_tls.setChecked(True)
    dashboard.lan_addresses.select(["192.168.1.20"], "your-pc.local")
    assert "https://your-pc.local:8767" in dashboard.connect_urls.text()
    assert "http://your-pc.local:8766" in dashboard.connect_urls.text()
    dashboard.lan_tls.setChecked(False)
    assert "https://your-pc.local" not in dashboard.connect_urls.text()
    assert "http://your-pc.local:8766" in dashboard.connect_urls.text()
    assert dashboard.save_settings()


def test_job_setup_commands_are_saved_without_blank_lines(dashboard):
    from job_server.settings import Settings

    dashboard.job_setup.setPlainText("  conda activate orca  \n\nmodule load orca\n")
    assert dashboard.save_settings()
    assert Settings.load(dashboard.config).job_setup_commands == [
        "conda activate orca",
        "module load orca",
    ]


@pytest.mark.parametrize("accept", [False, True])
def test_a_local_name_replaces_the_ca_only_when_accepted(dashboard, monkeypatch, accept):
    from PySide6.QtWidgets import QMessageBox
    from job_server.settings import Settings
    from job_server.tls import ca_fingerprint, ensure_certificates

    dashboard.lan_tls.setChecked(True)
    dashboard.lan_addresses.select(["192.168.1.20"])
    assert dashboard.save_settings()
    ensure_certificates(dashboard.settings)
    before = ca_fingerprint(dashboard.settings)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No,
    )
    dashboard.lan_addresses.select(["192.168.1.20"], "your-pc.local")
    assert dashboard.save_settings() is accept
    assert Settings.load(dashboard.config).lan_hostname == ("your-pc.local" if accept else "")
    if accept:
        ensure_certificates(dashboard.settings)
        assert ca_fingerprint(dashboard.settings) != before
        assert list((dashboard.settings.root / "tls").glob("retired-*/ca.pem"))
    else:
        assert ca_fingerprint(dashboard.settings) == before


@pytest.mark.parametrize("accept", [False, True])
def test_regenerate_ca_replaces_it_only_when_accepted(dashboard, monkeypatch, accept):
    from PySide6.QtWidgets import QMessageBox
    from job_server.tls import ca_fingerprint, ensure_certificates

    shown = []
    monkeypatch.setattr(dashboard, "show_local_ca", lambda: shown.append(True))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No,
    )
    assert dashboard.regenerate_ca_button.isHidden()
    dashboard.lan_tls.setChecked(True)
    assert not dashboard.regenerate_ca_button.isHidden()
    dashboard.lan_addresses.select(["192.168.1.20"])
    assert dashboard.save_settings()
    ensure_certificates(dashboard.settings)
    before = ca_fingerprint(dashboard.settings)
    dashboard.regenerate_ca()
    assert bool(shown) is accept
    if accept:
        ensure_certificates(dashboard.settings)
        assert ca_fingerprint(dashboard.settings) != before
        assert list((dashboard.settings.root / "tls").glob("retired-*/ca.pem"))
    else:
        assert ca_fingerprint(dashboard.settings) == before


def test_regenerate_without_a_ca_creates_one_without_asking(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(dashboard, "show_local_ca", lambda: shown.append(True))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("asked"))
    dashboard.lan_tls.setChecked(True)
    dashboard.lan_addresses.select(["192.168.1.20"])
    dashboard.regenerate_ca()
    assert shown


def _answer(monkeypatch, answer):
    from PySide6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: asked.append(args[1]) or answer
    )
    return asked


def test_save_offers_a_restart_only_for_a_change_under_a_running_server(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    asked = _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    restarts = []
    monkeypatch.setattr(dashboard, "restart_daemon", lambda: restarts.append(1))
    running = [False]
    monkeypatch.setattr("job_server.lifecycle.directory_is_idle", lambda root: not running[0])

    # A stopped server reads the file at its next start: nothing to ask.
    dashboard.fields["cpus"].setValue(dashboard.fields["cpus"].value() + 1)
    dashboard.save_settings_and_offer_restart()
    assert not asked and "when it starts" in dashboard.message.text()

    # Saving unchanged settings does not interrupt a running server.
    running[0] = True
    dashboard.save_settings_and_offer_restart()
    assert not asked and "No setting changed" in dashboard.message.text()

    dashboard.fields["cpus"].setValue(dashboard.fields["cpus"].value() + 1)
    dashboard.save_settings_and_offer_restart()
    assert asked == ["Restart the server?"] and restarts == [1]


def test_declined_restart_is_offered_again_at_the_next_save(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    asked = _answer(monkeypatch, QMessageBox.StandardButton.No)
    monkeypatch.setattr(dashboard, "restart_daemon", lambda: pytest.fail("Declined"))
    monkeypatch.setattr("job_server.lifecycle.directory_is_idle", lambda root: False)
    dashboard.fields["cpus"].setValue(dashboard.fields["cpus"].value() + 1)
    dashboard.save_settings_and_offer_restart()
    dashboard.save_settings_and_offer_restart()
    assert len(asked) == 2 and "Restart to apply" in dashboard.message.text()


def test_start_replaces_a_daemon_this_window_cannot_reach(dashboard, monkeypatch):
    import time
    from PySide6.QtWidgets import QApplication

    calls = []
    monkeypatch.setattr(dashboard, "refresh", lambda: None)
    monkeypatch.setattr(dashboard, "_offer_process_stop", lambda: lambda: calls.append("stop"))
    monkeypatch.setattr(dashboard, "_start_daemon", lambda: calls.append("start") or "Server is running.")
    dashboard.start_daemon()
    deadline = time.monotonic() + 5
    while dashboard.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert calls == ["stop", "start"]

    monkeypatch.setattr(dashboard, "_offer_process_stop", lambda: False)
    dashboard.start_daemon()
    assert calls == ["stop", "start"] and not dashboard.busy


def _many_jobs(count, state="PENDING"):
    rows = [(f"job {n}", "alice", state, 0, f"id-{n}", "—") for n in range(count)]
    return rows, [f"id-{n}" for n in range(count)]


def test_an_unchanged_refresh_leaves_the_table_and_its_scroll_alone(dashboard):
    table = dashboard.jobs
    dashboard.show()
    table.resize(500, 200)
    rows, ids = _many_jobs(200)
    dashboard.fill(table, rows, ids)
    table.setCurrentCell(3, 1)
    item = table.item(3, 1)
    table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
    scrolled = table.verticalScrollBar().value()
    assert scrolled > 0
    dashboard.fill(table, rows, ids)
    assert table.item(3, 1) is item
    assert table.verticalScrollBar().value() == scrolled


def test_a_changed_refresh_keeps_the_selection_column_and_scroll(dashboard):
    table = dashboard.jobs
    dashboard.show()
    table.resize(500, 200)
    rows, ids = _many_jobs(200)
    dashboard.fill(table, rows, ids)
    table.setCurrentCell(3, 2)
    table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
    scrolled = table.verticalScrollBar().value()
    rows, ids = _many_jobs(200, state="RUNNING")
    dashboard.fill(table, rows, ids)
    assert dashboard.selected(table) == "id-3"
    assert table.currentColumn() == 2
    assert table.verticalScrollBar().value() == scrolled, "Refresh scrolled back to the selected row"


def test_a_sorted_table_is_left_alone_when_only_the_order_differs(dashboard):
    table = dashboard.users
    table.setSortingEnabled(True)
    dashboard.fill(table, [("alice", "user", "Enabled"), ("bob", "user", "Enabled")], ["a", "b"])
    first = table.item(0, 0)
    dashboard.fill(table, [("bob", "user", "Enabled"), ("alice", "user", "Enabled")], ["b", "a"])
    assert table.item(0, 0) is first


def test_right_click_holds_releases_and_changes_a_waiting_job_reservation(dashboard, monkeypatch):
    from PySide6.QtWidgets import QDialog

    dashboard.client.key_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.client.key_path.write_text("synthetic placeholder")
    dashboard.settings.cpus, dashboard.settings.memory_mb = 8, 16384
    dashboard.fill(dashboard.jobs, [("friendly name", "alice", "QUEUED", 0, "job-id")], ["job-id"])
    dashboard.jobs.setCurrentCell(0, 0)
    dashboard.job_records = {"job-id": {"state": "PENDING", "held": False,
                                        "spec": {"resources": {"cpus_per_task": 4, "memory_mb": 4096}}}}
    dashboard._update_job_actions()
    menu = dashboard._context_actions(dashboard.jobs)
    for name in ("priority_action", "resources_action", "hold_action", "force_action"):
        assert getattr(dashboard, name) in menu and getattr(dashboard, name).isEnabled()
    assert not dashboard.release_action.isEnabled()
    calls = []
    monkeypatch.setattr(dashboard, "task", lambda call, *args, **kwargs: call())
    monkeypatch.setattr(dashboard.client, "request", lambda *args: calls.append(args))

    def accept(dialog):
        from job_server.ui.units import FocusSpinBox, GBSpinBox
        dialog.findChild(FocusSpinBox).setValue(0)
        dialog.findChild(GBSpinBox).setValue(0)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    dashboard.change_resources()
    dashboard.hold()
    dashboard.job_records["job-id"]["held"] = True
    dashboard._update_job_actions()
    assert dashboard.release_action.isEnabled() and not dashboard.hold_action.isEnabled()
    dashboard.hold(False)
    assert calls == [
        ("POST", "/admin/jobs/job-id/resources", {"cpus_per_task": 0, "memory_mb": 0}),
        ("POST", "/jobs/job-id/hold", {"held": True}),
        ("POST", "/jobs/job-id/hold", {"held": False}),
    ]


def test_reserved_and_elapsed_columns_read_plainly():
    from job_server.ui.dashboard import elapsed_text, reserved_text

    spec = {"spec": {"resources": {"cpus_per_task": 4, "memory_mb": 8192}}}
    assert reserved_text(spec) == "4 cores · 8.0 GiB"
    assert reserved_text({"spec": {"resources": {"cpus_per_task": 1, "memory_mb": 0}}}) == "1 core · 0.0 GiB"
    assert reserved_text({}) == "—"
    now = 1_000_000.0
    assert elapsed_text({"started_at": None}, now) == "—"
    assert elapsed_text({"started_at": now - 30}, now) == "< 1 min"
    assert elapsed_text({"started_at": now - 12 * 60 - 59}, now) == "12 min"
    assert elapsed_text({"started_at": now - 3 * 3600 - 5 * 60}, now) == "3 h 05 min"
    assert elapsed_text({"started_at": now - 50 * 3600}, now) == "2 d 2 h"
    # A finished job keeps its run time instead of growing.
    assert elapsed_text({"started_at": now - 7200, "ended_at": now - 3600}, now) == "1 h 00 min"


def test_times_carry_no_utc_offset():
    from job_server.ui.dashboard import Dashboard

    assert "+" not in Dashboard.timestamp(1_800_000_000) and len(Dashboard.timestamp(1_800_000_000)) == 19

