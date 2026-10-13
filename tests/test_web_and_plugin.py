from job_server import initialize


def test_web_assets_and_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Submit a job" in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/app.css").status_code == 200
    assert (
        client.get("/api/v1/health", headers={"Origin": "http://127.0.0.1:8766"}).status_code == 200
    )
    assert (
        client.get("/api/v1/health", headers={"Origin": "https://evil.example"}).status_code == 403
    )


def test_plugin_registration_is_lazy_and_uses_supported_context():
    class Context:
        def __init__(self):
            self.actions = []

        def add_plugin_menu(self, path, callback, **kwargs):
            assert kwargs["icon"].endswith("icon.png")
            self.actions.append((path, callback))

    context = Context()
    initialize(context)
    assert len(context.actions) == 4
    assert all(path.startswith("Job Server/") for path, _ in context.actions)


def test_gui_construction_has_no_listener(tmp_path):
    import pytest

    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    from job_server.settings import Settings
    from job_server.ui.dashboard import Dashboard

    app = QApplication.instance() or QApplication([])
    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    dialog = Dashboard(config=config)
    assert dialog.tabs.count() == 3
    assert "OFF" in dialog.message.text()
    dialog.close()
    app.processEvents()
    assert not dialog.timer.isActive()
    dialog.show()
    assert dialog.timer.isActive()
    dialog.close()


def test_gui_start_adopts_saved_port(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("PyQt6")
    import time
    from PyQt6.QtWidgets import QApplication
    from job_server.settings import Settings
    from job_server.ui.dashboard import Dashboard

    app = QApplication.instance() or QApplication([])
    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    dialog = Dashboard(config=config)
    dialog.timer.stop()
    dialog.settings.port = 8879

    def offline(*args, **kwargs):
        raise OSError("test daemon is offline")

    monkeypatch.setattr(dialog.client, "request", offline)
    commands = []
    monkeypatch.setattr(
        "job_server.ui.dashboard.subprocess.Popen", lambda args, **kwargs: commands.append(args)
    )
    dialog.start_daemon()
    deadline = time.monotonic() + 5
    while dialog.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not dialog.busy
    assert commands
    assert dialog.client.url == "http://127.0.0.1:8879"
    assert Settings.load(config).port == 8879
    dialog.close()


def test_gui_refuses_listener_that_does_not_hold_daemon_lock(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("PyQt6")
    import time
    from PyQt6.QtWidgets import QApplication
    from job_server.instance import InstanceLock
    from job_server.settings import Settings
    from job_server.ui.dashboard import Dashboard

    app = QApplication.instance() or QApplication([])
    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    dialog = Dashboard(config=config)
    dialog.timer.stop()
    sent = []

    def answering(method, path, body=None, **kwargs):
        sent.append((method, path, body))
        return {"status": "ok", "server_version": "impostor"}

    monkeypatch.setattr(dialog.client, "request", answering)
    monkeypatch.setattr(
        "job_server.ui.dashboard.subprocess.Popen", lambda *args, **kwargs: None
    )

    def run(call):
        call()
        deadline = time.monotonic() + 5
        while dialog.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not dialog.busy

    # Something answers /health, but no daemon holds this data directory's lock.
    run(dialog.start_daemon)
    assert "not this Job Server" in dialog.message.text()
    monkeypatch.setattr(dialog, "credentials", lambda title: ("admin", "example-password-123"))
    run(dialog.sign_in)
    assert "not this Job Server" in dialog.message.text()
    assert not any(path == "/auth/login" for _, path, _ in sent)

    # The genuine daemon holds the lock and is reused.
    lock = InstanceLock(tmp_path).acquire()
    try:
        run(dialog.start_daemon)
        assert "reusing it" in dialog.message.text()
    finally:
        lock.release()
    dialog.close()
