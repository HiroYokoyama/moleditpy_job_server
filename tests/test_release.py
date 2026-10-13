import ast
import re
from pathlib import Path

import pytest
import yaml

from scripts.verify_release import safe_members, source_version, validate_version

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "version", ["", "v0.1.0", "0.1.0\n", "0.1.1", "0.1.0;echo injected", "$(id)"]
)
def test_release_rejects_mismatch_or_injected_input(version):
    with pytest.raises(ValueError):
        validate_version(version)


def test_release_accepts_matching_source_version():
    assert validate_version(source_version()) == "0.1.0"


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "x\\file", "x/.git/config", "x/device.key"]
)
def test_artifact_paths_reject_escape_and_credentials(path):
    with pytest.raises(ValueError):
        safe_members([path])


def test_workflows_validate_before_publication_and_do_not_execute_event_inputs():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.load(path.read_text("utf-8"), Loader=yaml.BaseLoader)
        assert "pull_request_target" not in workflow["on"]
        assert workflow["permissions"]["contents"] == "read"
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(r"[^@]+@[a-f0-9]{40}", step["uses"])
                assert "${{" not in step.get("run", "")
    release = yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text("utf-8"), Loader=yaml.BaseLoader
    )
    assert set(release["jobs"]["release"]["needs"]) == {"validate", "tests", "security"}
    assert "REGISTRY_PAT" not in str(release["jobs"]["tests"])
    assert release["jobs"]["release"]["permissions"]["contents"] == "write"
    # Syntax-check the validation script independently of imports.
    ast.parse((ROOT / "scripts/verify_release.py").read_text("utf-8"))
