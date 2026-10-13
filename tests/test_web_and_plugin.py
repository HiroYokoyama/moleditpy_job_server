from job_server import initialize


def test_web_assets_and_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Keep your calculations moving" in response.text
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
