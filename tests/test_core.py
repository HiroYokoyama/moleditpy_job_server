import pytest
from job_server.core import render_command


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
