import os

import pytest
from fastapi.testclient import TestClient

from job_server.api import create_app
from job_server.core import build_wrapper
from job_server.settings import Settings

from .conftest import enroll, reserve
from .test_execution import wait_job


@pytest.mark.parametrize(
    "commands",
    ["conda activate orca", [5], ["one\ntwo"], ["one\rtwo"], ["one\0two"], ["x" * 65537]],
)
def test_bad_job_setup_commands_are_refused(tmp_path, commands):
    with pytest.raises(ValueError, match="(?i)job setup"):
        Settings(data_dir=str(tmp_path), job_setup_commands=commands).validate()


def test_earlier_settings_files_have_no_setup_commands(tmp_path):
    config = tmp_path / "settings.json"
    config.write_text("{}")
    assert Settings.load(config).job_setup_commands == []


def spec(command, name="example"):
    return {
        "name": name,
        "command": command,
        "files": [{"path": "input.txt"}],
        "resources": {"cpus_per_task": 1, "memory_mb": 1, "walltime_seconds": 0},
    }


@pytest.mark.parametrize("windows", [False, True])
def test_setup_lines_come_first_and_are_not_templates(tmp_path, windows):
    setup = ["echo {input}", "echo second"]
    script, payload = build_wrapper(spec("echo job"), tmp_path.as_posix(), windows, setup)
    text = payload if windows else script
    assert text.index("echo {input}") < text.index("echo second") < text.index("echo job")


@pytest.mark.parametrize("windows", [False, True])
def test_no_setup_lines_leave_the_script_unchanged(tmp_path, windows):
    default = build_wrapper(spec("echo job"), tmp_path.as_posix(), windows)
    assert build_wrapper(spec("echo job"), tmp_path.as_posix(), windows, []) == default


def test_setup_lines_reach_a_real_job_and_stay_out_of_its_public_view(tmp_path):
    windows = os.name == "nt"
    setup = "$env:JOB_SETUP = 'example'" if windows else "export JOB_SETUP=example"
    command = "$env:JOB_SETUP | Set-Content result.txt" if windows else 'echo "$JOB_SETUP" > result.txt'
    settings = Settings(
        data_dir=str(tmp_path / "data"), cpus=2, memory_mb=1024, job_setup_commands=[setup]
    )
    with TestClient(create_app(settings, dispatch=False)) as client:
        alice = enroll(client)
        job_id = reserve(client, alice, command=command).json()["id"]
        client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
        assert wait_job(client, job_id, {"DONE", "FAILED", "LOST"})["state"] == "DONE"
        result = settings.root / "jobs" / job_id / "work" / "result.txt"
        assert result.read_text().strip() == "example"
        public = client.get(f"/api/v1/jobs/{job_id}", headers=alice["headers"]).text
        assert "JOB_SETUP = " not in public and "JOB_SETUP=example" not in public
