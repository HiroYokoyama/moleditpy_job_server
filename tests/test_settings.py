from job_server.settings import Settings


def test_default_cli_reads_saved_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = Settings().root / "settings.json"
    settings = Settings(port=8890, cpus=3)
    settings.save(path)
    assert Settings.load().port == 8890
    assert Settings.load().cpus == 3
