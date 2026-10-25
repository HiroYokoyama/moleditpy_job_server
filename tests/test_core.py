import os
import subprocess
import pytest
from job_server.core import build_wrapper, render_command
from .conftest import reserve
from .test_execution import wait_job


@pytest.mark.parametrize(
    "command", ['echo "prefix {input} suffix"', "echo 'prefix [input] suffix'", r"echo \{input}"]
)
def test_placeholder_quoting_context_is_rejected(command):
    with pytest.raises(ValueError):
        render_command(command, {"input": "input'; touch injected"})


def test_placeholder_escaping_and_literal_language_braces():
    result = render_command("awk '{print $1}' {input}", {"input": "input's data"})
    assert "awk '{print $1}'" in result
    assert "'\"'\"'" in result


def test_same_job_manager_icon_used():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert (root / "job_server/web/icon.png").read_bytes() == (
        root / "upstream/job_manager/img/icon.png"
    ).read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Native PowerShell expression execution")
@pytest.mark.parametrize("command", ["$null", "0", "exit 0"])
def test_native_control_expressions_do_not_replace_supervisor_code(client, alice, command):
    job_id = reserve(client, alice, command=command).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
    job = wait_job(client, job_id, {"DONE", "FAILED", "LOST"})
    assert job["state"] == "DONE"
    assert job["exit_code"] == 0


def test_payload_marker_in_job_name_does_not_execute_the_payload_twice(tmp_path):
    shell = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else "/bin/bash"
    if not os.path.exists(shell):
        pytest.skip("Bash unavailable")
    spec = {
        "name": "__MOLEDITPY_SERVER_PAYLOAD__",
        "command": "echo first >> count.out\necho second >> count.out",
        "files": [],
        "resources": {"cpus_per_task": 1, "memory_mb": 512, "walltime_seconds": 30},
    }
    script, _ = build_wrapper(spec, tmp_path.as_posix())
    path = tmp_path / "run.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    result = subprocess.run([shell, path.as_posix()], cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "count.out").read_text().splitlines() == ["first", "second"]
