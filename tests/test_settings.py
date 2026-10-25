from job_server.settings import Settings
import pytest


@pytest.mark.parametrize("name", ["trusted_users_only", "allow_privileged", "web_ui"])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_security_flags_require_actual_json_booleans(name, value):
    with pytest.raises(ValueError, match="must be a boolean"):
        Settings(**{name: value}).validate()


def test_default_cli_reads_saved_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = Settings().root / "settings.json"
    settings = Settings(port=8890, cpus=3)
    settings.save(path)
    assert Settings.load().port == 8890
    assert Settings.load().cpus == 3
