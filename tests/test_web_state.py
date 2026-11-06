"""Delayed HTTP responses must not restore stale browser accounts or jobs."""

from pathlib import Path
import shutil
import subprocess

import pytest


def output_text(output):
    return output.decode("utf-8", "replace") if isinstance(output, bytes) else output or ""


def run_browser_cases(command):
    """Run the scenarios, allowing one retry only if Node never began running them.

    Windows runners have stalled a cold Node start for over a minute while every
    pytest worker was busy. That says nothing about the code under test, so a second
    try is fair. A timeout after the first scenario began, or any failing result,
    is never retried. Both attempts together stay inside pytest's 120 s limit.
    """
    for attempt in (1, 2):
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired as error:
            output = output_text(error.stdout) + output_text(error.stderr)
            if attempt == 2 or "RUN " in output:
                pytest.fail(f"Browser scenarios timed out on attempt {attempt}:\n{output}")


def test_browser_ignores_stale_responses():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for browser JavaScript regression")
    root = Path(__file__).resolve().parents[1]
    # One process avoids fifteen cold Node starts competing with native/Qt tests on
    # Windows runners. The JS harness bounds each scenario itself.
    result = run_browser_cases(
        [
            node,
            str(root / "tests/web_state_cases.cjs"),
            str(root / "job_server/web/app.js"),
            "all",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr


def stalls(monkeypatch, *outcomes):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, subprocess.TimeoutExpired):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_a_node_start_that_stalls_is_tried_once_more(monkeypatch):
    finished = subprocess.CompletedProcess("node", 0, "PASS", "")
    calls = stalls(monkeypatch, subprocess.TimeoutExpired("node", 45), finished)
    assert run_browser_cases("node") is finished
    assert len(calls) == 2


def test_node_that_never_starts_still_fails(monkeypatch):
    stall = subprocess.TimeoutExpired("node", 45)
    calls = stalls(monkeypatch, stall, stall)
    with pytest.raises(pytest.fail.Exception, match="attempt 2"):
        run_browser_cases("node")
    assert len(calls) == 2


@pytest.mark.parametrize("output", ["RUN refresh\n", b"RUN refresh\n"])
def test_a_scenario_that_hangs_is_not_retried(monkeypatch, output):
    calls = stalls(monkeypatch, subprocess.TimeoutExpired("node", 45, output=output))
    with pytest.raises(pytest.fail.Exception, match="RUN refresh"):
        run_browser_cases("node")
    assert len(calls) == 1


def test_a_failing_result_is_not_retried(monkeypatch):
    failed = subprocess.CompletedProcess("node", 1, "RUN refresh", "assertion")
    calls = stalls(monkeypatch, failed)
    assert run_browser_cases("node") is failed
    assert len(calls) == 1


def test_browser_hash_bundle_matches_pinned_provenance():
    import hashlib
    import json
    import re

    directory = Path(__file__).resolve().parents[1] / "job_server/web"
    manifest = json.loads((directory / "sha256.PROVENANCE.json").read_text("utf-8"))
    assert manifest["license"] == "MIT"
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["commit"])
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((directory / name).read_text("utf-8").encode()).hexdigest() == expected
