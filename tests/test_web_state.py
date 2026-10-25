"""Delayed HTTP responses must not restore stale browser accounts or jobs."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("scenario", ["authentication", "refresh", "details"])
def test_browser_ignores_stale_responses(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for browser JavaScript regression")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/web_state_cases.cjs"),
            str(root / "job_server/web/app.js"),
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
