"""Job details remain bounded, plain text and safe against delayed responses."""

import pytest


@pytest.fixture
def details(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from job_server.settings import Settings
    from job_server.ui.dashboard import Dashboard
    from job_server.ui.job_detail import JobDetailDialog

    app = QApplication.instance() or QApplication([])
    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    dashboard = Dashboard(config=config)
    dashboard.timer.stop()
    dialog = JobDetailDialog(dashboard)
    queued = []
    monkeypatch.setattr(dialog.pool, "start", queued.append)
    yield dashboard, dialog, queued, app
    for task in tuple(dialog._tasks):
        task.run()
    dialog.close()
    dashboard.close()
    app.processEvents()


def record(identifier):
    return {
        "id": identifier, "state": "PENDING", "display_state": "PENDING", "priority": 5,
        "force_run": True, "exit_code": None,
        "spec": {"name": "<b>" + identifier + "</b>", "command": "echo synthetic", "after_job": "previous",
                 "chain_any": False, "resources": {"cpus_per_task": 2, "memory_mb": 1024, "walltime_seconds": 3600}},
    }


def responses(calls):
    def request(method, path):
        calls.append(path)
        if path.endswith("/log?limit=1"):
            return {"size": 200000, "text": "x"}
        if "/log?offset=" in path:
            return {"offset": 134464, "text": "synthetic log"}
        if path.endswith("/files"):
            return [{"path": "<a>result.txt</a>", "size": 4, "sha256": "0" * 64}]
        return record(path.rsplit("/", 1)[-1])
    return request


def test_job_detail_reads_bounded_log_and_plain_file_metadata(details, monkeypatch):
    from PySide6.QtCore import Qt

    dashboard, dialog, queued, app = details
    calls = []
    monkeypatch.setattr(dashboard.client, "request", responses(calls))
    dialog.show_job("job-a", "alice")
    assert dialog.values["name"].text() == "—"
    queued.pop(0).run()
    app.processEvents()
    assert dialog.values["name"].text() == "<b>job-a</b>"
    assert dialog.values["name"].textFormat() == Qt.TextFormat.PlainText
    assert dialog.values["owner"].text() == "alice"
    assert "must succeed" in dialog.values["dependency"].text()
    assert dialog.command.toPlainText() == "echo synthetic"
    assert dialog.log.toPlainText() == "synthetic log"
    assert dialog.files.item(0, 0).text() == "<a>result.txt</a>"
    assert "/jobs/job-a/log?offset=134464&limit=65536" in calls


def test_job_detail_ignores_previous_job_and_closed_dialog_responses(details, monkeypatch):
    dashboard, dialog, queued, app = details
    monkeypatch.setattr(dashboard.client, "request", responses([]))
    dialog.show_job("job-a", "alice")
    dialog.show_job("job-b", "bob")
    queued.pop(0).run()
    app.processEvents()
    assert dialog.values["id"].text() == "—"
    queued.pop(0).run()
    app.processEvents()
    assert dialog.values["id"].text() == "job-b"
    assert dialog.values["owner"].text() == "bob"
    dialog.refresh()
    dialog.close()
    queued.pop(0).run()
    app.processEvents()
    assert dialog.values["id"].text() == "—"
    assert dialog.log.toPlainText() == ""


def test_job_detail_authentication_error_clears_previous_contents(details, monkeypatch):
    from job_server.client import ClientError

    dashboard, dialog, queued, app = details
    monkeypatch.setattr(dashboard.client, "request", responses([]))
    dialog.show_job("job-a", "alice")
    queued.pop(0).run()
    app.processEvents()

    def denied(*args):
        raise ClientError(401, {"message": "Credential expired or revoked"})

    monkeypatch.setattr(dashboard.client, "request", denied)
    dialog.refresh()
    queued.pop(0).run()
    app.processEvents()
    assert dialog.command.toPlainText() == dialog.log.toPlainText() == ""
    assert dialog.files.rowCount() == 0
    assert "expired" in dialog.status.text()
    assert dialog.refresh_button.isEnabled()


def test_refreshing_the_shown_job_keeps_it_on_screen_and_follows_until_it_ends(details, monkeypatch):
    dashboard, dialog, queued, app = details
    state = {"value": "RUNNING"}

    def request(method, path):
        if path.endswith("/files"):
            return [{"path": "result.txt", "size": 4, "sha256": "0" * 64}]
        if "/log" in path:
            return {"size": 3, "offset": 0, "text": "line"}
        return {**record("job-a"), "state": state["value"], "display_state": state["value"]}

    monkeypatch.setattr(dashboard.client, "request", request)
    dialog.show_job("job-a", "alice")
    queued.pop(0).run()
    app.processEvents()
    assert dialog.follow.isActive(), "An unfinished job is followed"
    item = dialog.files.item(0, 0)
    dialog._follow()
    assert dialog.values["id"].text() == "job-a", "A refresh does not blank the job first"
    assert dialog.log.toPlainText() == "line"
    state["value"] = "DONE"
    queued.pop(0).run()
    app.processEvents()
    assert dialog.values["state"].text() == "DONE"
    assert dialog.files.item(0, 0) is item, "Unchanged result files are not redrawn"
    assert not dialog.follow.isActive(), "A finished job is no longer followed"
    dialog._follow()
    assert queued == []


def test_the_log_stays_where_the_reader_scrolled(details):
    _dashboard, dialog, _queued, _app = details
    dialog.show()
    dialog.tabs.setCurrentWidget(dialog.log)
    dialog._show_log("\n".join(f"line {n}" for n in range(500)))
    bar = dialog.log.verticalScrollBar()
    assert bar.value() == bar.maximum(), "The first output opens at its end"
    bar.setValue(10)
    dialog._show_log("\n".join(f"line {n}" for n in range(501)))
    assert bar.value() == 10
    bar.setValue(bar.maximum())
    dialog._show_log("\n".join(f"line {n}" for n in range(502)))
    assert bar.value() == bar.maximum()
