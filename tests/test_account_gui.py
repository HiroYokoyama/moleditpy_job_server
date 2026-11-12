"""Account, context-menu, usage and job-folder behaviour of the administration window."""

import sqlite3
import uuid

import pytest


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from job_server.settings import Settings
    from job_server.ui.dashboard import Dashboard

    app = QApplication.instance() or QApplication([])
    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    dialog = Dashboard(config=config)
    dialog.timer.stop()
    monkeypatch.setattr(dialog, "require_admin_registration", lambda: True)
    yield dialog
    dialog.close()
    app.processEvents()


USERS = [
    {"id": "me", "username": "admin", "role": "admin", "enabled": 1, "created_at": 1},
    {"id": "a", "username": "alice", "role": "user", "enabled": 1, "created_at": 1},
    {"id": "b", "username": "bob", "role": "admin", "enabled": 0, "created_at": 1},
]


def show_users(dashboard, monkeypatch, jobs=(), resources=None):
    dashboard.client.key_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.client.key_path.write_text("synthetic placeholder")
    replies = {
        "/health": {"server_version": "fixture"},
        "/me": {"user_id": "me", "username": "admin", "role": "admin"},
        "/system/resources": {"running": 0, "pending": 0, "reserved_cpus": 0, "cpus": 8,
                              "reserved_memory_mb": 0, "memory_mb": 4096, "paused": False,
                              **(resources or {})},
        "/admin/jobs": list(jobs),
        "/admin/users": USERS,
        "/admin/devices": [
            {"id": "d1", "user_id": "a", "revoked_at": None, "last_access": None, "created_at": 1},
            {"id": "d2", "user_id": "a", "revoked_at": 5, "last_access": None, "created_at": 1},
        ],
    }
    monkeypatch.setattr(dashboard.client, "request", lambda method, path, **kwargs: replies[path])
    monkeypatch.setattr(dashboard, "task", lambda call, on_ok=None, **kwargs: on_ok and on_ok(call()))
    dashboard.refresh()


def select_user(dashboard, username):
    for row in range(dashboard.users.rowCount()):
        if dashboard.users.item(row, 0).text().startswith(username):
            dashboard.users.setCurrentCell(row, 0)
            return
    pytest.fail(username)


def record_requests(dashboard, monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard.client, "request", lambda *args: calls.append(args))
    monkeypatch.setattr(dashboard, "refresh", lambda: None)
    return calls


def test_users_table_marks_you_and_counts_only_active_devices(dashboard, monkeypatch):
    show_users(dashboard, monkeypatch)
    assert dashboard.users.item(0, 0).text() == "admin (you)"
    assert dashboard.users.item(0, 1).text() == "administrator"
    assert dashboard.users.item(1, 3).text() == "1"
    assert "admin (admin)" in dashboard.account_label.text()


def test_own_account_actions_are_blocked_and_labels_follow_the_row(dashboard, monkeypatch):
    show_users(dashboard, monkeypatch)
    select_user(dashboard, "admin")
    dashboard._update_account_actions()
    for item in (dashboard.toggle_user_action, dashboard.toggle_role_action,
                 dashboard.reset_password_action, dashboard.delete_user_action):
        assert not item.isEnabled()
    assert dashboard.change_password_action.isEnabled()
    select_user(dashboard, "alice")
    dashboard._update_account_actions()
    assert dashboard.toggle_user_action.text().startswith("Disable")
    assert "administrator" in dashboard.toggle_role_action.text()
    assert dashboard.toggle_user_action.isEnabled()
    select_user(dashboard, "bob")
    dashboard._update_account_actions()
    assert dashboard.toggle_user_action.text().startswith("Enable")
    assert "regular user" in dashboard.toggle_role_action.text()


def test_context_menus_offer_the_row_actions(dashboard):
    users = dashboard._context_actions(dashboard.users)
    assert dashboard.toggle_user_action in users and dashboard.toggle_role_action in users
    assert dashboard.reset_password_action in users and dashboard.delete_user_action in users
    assert dashboard.revoke_device_action in dashboard._context_actions(dashboard.devices)
    jobs = dashboard._context_actions(dashboard.jobs)
    assert dashboard.folder_action in jobs and dashboard.cancel_action in jobs
    assert dashboard.force_action in jobs and dashboard.priority_action in jobs


@pytest.mark.parametrize("name, method, path, body", [
    ("alice", "toggle_user", "/admin/users/a/disable", None),
    ("bob", "toggle_user", "/admin/users/b/enable", None),
    ("alice", "toggle_role", "/admin/users/a/role", {"role": "admin"}),
    ("bob", "toggle_role", "/admin/users/b/role", {"role": "user"}),
])
def test_toggles_send_the_opposite_of_the_current_state(dashboard, monkeypatch, name, method, path, body):
    from PySide6.QtWidgets import QMessageBox

    show_users(dashboard, monkeypatch)
    select_user(dashboard, name)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    calls = record_requests(dashboard, monkeypatch)
    getattr(dashboard, method)()
    assert calls == [("POST", path, body) if body else ("POST", path)]


def test_declined_confirmations_make_no_request(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    show_users(dashboard, monkeypatch)
    select_user(dashboard, "alice")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    calls = record_requests(dashboard, monkeypatch)
    dashboard.toggle_user()
    dashboard.toggle_role()
    assert calls == []


@pytest.mark.parametrize("typed, accepted, requests", [
    ("alice", True, 1), ("Alice", True, 0), ("", True, 0), ("alice", False, 0),
])
def test_delete_needs_the_username_typed_exactly(dashboard, monkeypatch, typed, accepted, requests):
    from PySide6.QtWidgets import QInputDialog

    show_users(dashboard, monkeypatch)
    select_user(dashboard, "alice")
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: (typed, accepted))
    calls = record_requests(dashboard, monkeypatch)
    dashboard.delete_user()
    assert calls == [("DELETE", "/admin/users/a")] * requests


def test_change_password_sends_old_and_new_and_nothing_when_cancelled(dashboard, monkeypatch):
    import job_server.ui.account as account

    calls = record_requests(dashboard, monkeypatch)
    monkeypatch.setattr(dashboard, "task", lambda call, *args, **kwargs: call())
    monkeypatch.setattr(account, "ask_password", lambda *args, **kwargs: None)
    dashboard.change_password()
    assert calls == []
    monkeypatch.setattr(account, "ask_password", lambda *args, **kwargs: ("old", "new", True))
    dashboard.change_password()
    assert calls == [("POST", "/me/password", {
        "current_password": "old", "new_password": "new", "revoke_devices": True,
    })]


def test_password_dialog_needs_a_match_and_a_real_change(dashboard):
    from PySide6.QtWidgets import QDialogButtonBox
    from job_server.ui.account import PasswordDialog

    dialog = PasswordDialog(dashboard, "t", "i", ask_current=True, revoke_label="r")
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    dialog.current.setText("old")
    dialog.new.setText("new")
    dialog.repeat.setText("other")
    assert not ok.isEnabled() and "differ" in dialog.hint.text()
    dialog.repeat.setText("new")
    assert ok.isEnabled()
    dialog.new.setText("old")
    dialog.repeat.setText("old")
    assert not ok.isEnabled()
    dialog.clear_fields()
    assert not dialog.new.text() and not dialog.current.text()
    dialog.deleteLater()


def test_add_user_can_create_an_administrator(dashboard, monkeypatch):
    import job_server.ui.account as account

    calls = record_requests(dashboard, monkeypatch)
    monkeypatch.setattr(dashboard, "task", lambda call, *args, **kwargs: call())
    monkeypatch.setattr(account, "ask_new_user", lambda parent: ("second", "x", "admin"))
    dashboard.add_user()
    assert calls == [("POST", "/admin/users", {"username": "second", "password": "x", "role": "admin"})]


def test_new_user_dialog_defaults_to_an_ordinary_user(dashboard):
    from PySide6.QtWidgets import QDialogButtonBox
    from job_server.ui.account import NewUserDialog

    dialog = NewUserDialog(dashboard)
    assert dialog.role.currentData() == "user"
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    dialog.username.setText("alice")
    dialog.password.setText("x")
    assert not ok.isEnabled()
    dialog.repeat.setText("x")
    assert ok.isEnabled()
    dialog.role.setCurrentIndex(1)
    assert "manage every" in dialog.note.text()
    dialog.deleteLater()


def test_dashboard_shows_live_usage_apart_from_the_budget(dashboard, monkeypatch):
    show_users(dashboard, monkeypatch, resources={
        "cpu_percent": 37.4, "memory_percent": 50.0, "memory_used_mb": 8192, "memory_total_mb": 16384,
    })
    assert dashboard.metric_values["cpu_usage"].text() == "37%"
    assert dashboard.metric_values["memory_usage"].text() == "8.0 / 16.0 GiB"
    assert dashboard.metric_bars["cpu_usage"].value() == 37
    assert dashboard.metric_values["cpu"].text() == "0 / 8"
    show_users(dashboard, monkeypatch, resources={"cpu_percent": None, "memory_percent": None})
    assert dashboard.metric_values["cpu_usage"].text() == "—"
    assert dashboard.metric_values["memory_usage"].text() == "—"


def test_budget_can_be_set_to_this_pc_and_a_new_server_starts_there(dashboard, tmp_path, monkeypatch):
    from job_server import settings as settings_module
    from job_server.ui import dashboard as dashboard_module
    from job_server.ui.dashboard import Dashboard

    monkeypatch.setattr(dashboard_module, "pc_resources", lambda: (12, 16384))
    fresh = Dashboard(config=tmp_path / "new" / "settings.json")
    fresh.timer.stop()
    try:
        assert fresh.settings.cpus == 12 and fresh.settings.memory_mb == 16384
        assert fresh.fields["cpus"].value() == 12
    finally:
        fresh.close()
    # A saved budget is the administrator's choice until they ask to replace it.
    assert dashboard.settings.cpus == settings_module.Settings().cpus
    cpus, memory_mb = settings_module.pc_resources()
    assert cpus >= 1 and memory_mb >= 1
    dashboard.fields["cpus"].setValue(1)
    dashboard.match_pc_button.click()
    assert dashboard.fields["cpus"].value() == cpus
    assert dashboard.fields["memory_mb"].mb() == pytest.approx(memory_mb, abs=64)


def test_near_simultaneous_usage_readings_agree(monkeypatch):
    from job_server import scheduler

    monkeypatch.setattr(scheduler, "_usage", (0.0, None))
    first = scheduler.host_usage()
    assert scheduler.host_usage() is first


def test_stuck_job_is_flagged_and_can_be_marked_lost(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    job = {"id": "j", "owner_id": "a", "spec": {"name": "n"}, "state": "RUNNING",
           "display_state": "RUNNING", "reason": "NEEDS_RECHECK: supervisor identity missing"}
    show_users(dashboard, monkeypatch, jobs=[job])
    assert dashboard.jobs.item(0, 2).text() == "RUNNING · needs recheck"
    dashboard.jobs.setCurrentCell(0, 0)
    dashboard._update_job_actions()
    assert dashboard.lost_action.isEnabled() and dashboard.lost_action in dashboard._context_actions(dashboard.jobs)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    calls = record_requests(dashboard, monkeypatch)
    dashboard.mark_lost()
    assert calls == [("POST", "/admin/jobs/j/mark-lost")]
    dashboard.job_records["j"]["reason"] = ""
    dashboard._update_job_actions()
    assert not dashboard.lost_action.isEnabled()


def test_cpu_reading_does_not_depend_on_the_request_thread(monkeypatch):
    import threading
    from collections import namedtuple
    from job_server import scheduler

    Times = namedtuple("Times", "user system idle")
    samples = iter([Times(10, 10, 80), Times(40, 20, 90)])
    monkeypatch.setattr(scheduler.psutil, "cpu_times", lambda: next(samples))
    monkeypatch.setattr(scheduler, "_cpu_times", None)
    assert scheduler._cpu_percent() == 0.0
    result = []
    thread = threading.Thread(target=lambda: result.append(scheduler._cpu_percent()))
    thread.start()
    thread.join()
    assert result == [80.0]  # 40 of the 50 ticks since the previous sample were busy.


def test_job_filter_hides_rows_by_text_and_state(dashboard):
    dashboard.fill(dashboard.jobs, [
        ("opt", "alice", "RUNNING", 0, "id-1"), ("freq", "bob", "FAILED", 0, "id-2"),
    ], ["id-1", "id-2"])
    dashboard.job_search.setText("BOB")
    assert dashboard.jobs.isRowHidden(0) and not dashboard.jobs.isRowHidden(1)
    dashboard.job_search.clear()
    dashboard.job_filter.setCurrentText("Running")
    assert not dashboard.jobs.isRowHidden(0) and dashboard.jobs.isRowHidden(1)


def jobs_database(dashboard, job_id, work_root=""):
    connection = sqlite3.connect(dashboard.settings.root / "server.db")
    connection.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT, work_root TEXT)")
    connection.execute("INSERT INTO jobs VALUES (?,?)", (job_id, work_root))
    connection.commit()
    connection.close()


def test_job_folder_opens_the_real_folder_only(dashboard, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    job = str(uuid.uuid4())
    jobs_database(dashboard, job)
    with pytest.raises(ValueError, match="no folder"):
        dashboard._job_folder(job)
    folder = dashboard.settings.root / "jobs" / job / "work"
    folder.mkdir(parents=True)
    assert dashboard._job_folder(job) == folder
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    monkeypatch.setattr(dashboard, "task", lambda call, on_ok, **kwargs: on_ok(call()))
    dashboard.open_job_folder(job)
    assert opened == [folder.as_posix()]


@pytest.mark.parametrize("identifier", ["../../etc", "not-a-uuid", "4C743D16-7F22-4E63-AFDA-341684E59F56"])
def test_job_folder_rejects_anything_but_a_job_id(dashboard, identifier):
    jobs_database(dashboard, str(uuid.uuid4()))
    with pytest.raises(ValueError):
        dashboard._job_folder(identifier)


def test_job_folder_rejects_an_unknown_job(dashboard):
    jobs_database(dashboard, str(uuid.uuid4()))
    with pytest.raises(ValueError, match="not on this server"):
        dashboard._job_folder(str(uuid.uuid4()))


def test_revoked_key_is_replaced_only_after_a_new_device_is_accepted(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from job_server.client import ClientError, JobServerClient

    key = dashboard.client.key_path
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("old key")
    monkeypatch.setattr(dashboard, "task", lambda call, on_ok=None, **kwargs: on_ok and on_ok(call()))
    monkeypatch.setattr(dashboard, "require_own_daemon", lambda: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(dashboard, "credentials", lambda title: ("admin", "x"))
    monkeypatch.setattr(dashboard, "refresh", lambda: None)

    def revoked(self=None):
        raise ClientError(401, {"code": "invalid_device", "message": "Device unavailable"})

    monkeypatch.setattr(dashboard.client, "authenticate", revoked)

    def refused(self, username, password):
        raise ClientError(401, {"code": "invalid_credentials", "message": "Invalid credentials"})

    monkeypatch.setattr(JobServerClient, "enroll", refused)
    with pytest.raises(ClientError):
        dashboard.sign_in()
    assert key.read_text() == "old key"
    assert not key.with_name(key.name + ".new").exists()

    def accepted(self, username, password):
        self.key_path.write_text("new key")

    monkeypatch.setattr(JobServerClient, "enroll", accepted)
    dashboard.sign_in()
    assert key.read_text() == "new key"


def test_cpu_dial_is_red_at_every_core_and_purple_past_it(dashboard, monkeypatch):
    usage = {"memory_percent": 50.0, "memory_used_mb": 8192, "memory_total_mb": 16384,
             "cpu_cores": 8, "cpu_threads": 12}

    def color(cpu, **extra):
        show_users(dashboard, monkeypatch, resources={**usage, "cpu_percent": cpu, **extra})
        return dashboard.metric_bars["cpu_usage"]._color.name()

    # 8 of 12 threads (66.7%) is every core busy: the colours follow the cores.
    assert color(40.0) == "#2e7d32"
    assert dashboard.metric_trends["cpu_usage"].marker == (pytest.approx(100 * 8 / 12), "8 cores")
    assert color(50.0) == "#e08600"
    assert color(62.0) == "#c62828"
    assert color(66.7) == "#c62828"
    assert color(70.0) == "#7b1fa2"
    # Without hyper-threading there is no core line and nothing past red.
    assert color(68.0, cpu_threads=8) == "#2e7d32"
    assert dashboard.metric_trends["cpu_usage"].marker is None
    assert color(100.0, cpu_threads=8) == "#c62828"
