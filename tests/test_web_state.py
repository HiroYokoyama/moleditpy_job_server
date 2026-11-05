"""Delayed HTTP responses must not restore stale browser accounts or jobs."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_browser_ignores_stale_responses():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for browser JavaScript regression")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/web_state_cases.cjs"),
            str(root / "job_server/web/app.js"),
            "all",
        ],
        capture_output=True,
        text=True,
        # One process avoids fifteen cold Node starts competing with native/Qt
        # tests on Windows runners. The JS harness bounds each scenario itself.
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
