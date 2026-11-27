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


@pytest.mark.parametrize("command", ["run {basename}", "run [input]", "run [stem]"])
def test_retired_placeholder_spellings_are_refused(tmp_path, command):
    spec = {
        "name": "job",
        "command": command,
        "files": [{"path": "input.gjf"}],
        "resources": {"cpus_per_task": 1, "memory_mb": 512, "walltime_seconds": 30},
    }
    with pytest.raises(ValueError, match="no longer supported"):
        build_wrapper(spec, tmp_path.as_posix())


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


@pytest.mark.parametrize(
    ("windows", "values", "expected"),
    [
        (True, {"input": "input.inp", "stem": "input"}, "orca input.inp > input.out"),
        (False, {"input": "input.inp", "stem": "input"}, "orca input.inp > input.out"),
        (True, {"input": "my mol.inp", "stem": "my mol"}, "orca 'my mol.inp' > 'my mol.out'"),
        (False, {"input": "my mol.inp", "stem": "my mol"}, "orca 'my mol.inp' > 'my mol.out'"),
        (True, {"input": "o'k.inp", "stem": "o'k"}, "orca 'o''k.inp' > 'o''k.out'"),
    ],
)
def test_placeholder_and_its_suffix_are_quoted_as_one_word(windows, values, expected):
    # PowerShell reads 'input'.out as a member access ($null), so the redirect
    # discarded ORCA's output and no .out file was written.
    assert render_command("orca {input} > {stem}.out", values, windows) == expected


def test_adjacent_placeholders_join_one_word():
    values = {"stem": "a b", "name": "job"}
    assert render_command("cp x {stem}_{name}.log", values, True) == "cp x 'a b_job.log'"
    assert render_command("cp x {stem}_{name}.log", values, False) == "cp x 'a b_job.log'"


@pytest.mark.parametrize("value", ["-x.inp", "@a.inp", "$a.inp", "a,b.inp", "(a).inp", ".a"])
def test_windows_quotes_words_powershell_would_interpret(value):
    assert render_command("run {input}", {"input": value}, True) == "run '" + value + "'"


def test_windows_comma_after_placeholder_stays_outside_the_word():
    assert render_command("run {stem},x", {"stem": "a b"}, True) == "run 'a b',x"


@pytest.mark.skipif(os.name != "nt", reason="Native PowerShell redirect")
@pytest.mark.parametrize("stem", ["input", "my mol", "o'k"])
def test_powershell_redirect_to_stem_out_writes_the_file(tmp_path, stem):
    command = render_command("Write-Output done > {stem}.out", {"stem": stem}, windows=True)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=tmp_path,
        check=True,
        timeout=60,
    )
    assert (tmp_path / f"{stem}.out").read_text(encoding="utf-16").strip() == "done"
