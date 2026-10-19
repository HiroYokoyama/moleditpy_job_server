import ast
import re
from pathlib import Path

import pytest
import yaml

from scripts.verify_release import safe_members, source_version, validate_version

ROOT = Path(__file__).resolve().parents[1]


def test_release_checksums_cover_only_distributed_assets(tmp_path):
    import hashlib
    import tarfile
    from zipfile import ZipFile
    from scripts.verify_release import verify_artifacts, verify_companion

    dist = tmp_path / "dist"
    dist.mkdir()
    version = source_version()
    server = dist / f"job_server_{version}.zip"
    wheel = dist / f"moleditpy_job_server-{version}-py3-none-any.whl"
    source = dist / f"moleditpy_job_server-{version}.tar.gz"
    with ZipFile(server, "w") as archive:
        for name in ("__init__.py", "_execution/schedulers/windows.py", "web/icon.png", "LICENSE", "COPYING", "LICENSING.md", "_execution/UPSTREAM_LICENSE"):
            archive.writestr("job_server/" + name, "fixture")
    with ZipFile(wheel, "w") as archive:
        archive.writestr("job_server/_execution/schedulers/base.py", "fixture")
        archive.writestr("job_server/web/app.js", "fixture")
    with tarfile.open(source, "w:gz"):
        pass
    assets = [server, wheel, source]
    assert verify_artifacts(version, tmp_path) == assets
    expected = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in assets
    )
    assert (dist / "SHA256SUMS").read_text() == expected
    with ZipFile(dist / "job_manager_job_server.zip", "w") as archive:
        archive.writestr("job_manager/server_backend.py", "fixture")
    verify_companion(tmp_path)
    assert verify_artifacts(version, tmp_path) == assets
    assert (dist / "SHA256SUMS").read_text() == expected


@pytest.mark.parametrize(
    "version", ["", "v0.1.4", "0.1.4\n", "0.0.0", "0.1.4;echo injected", "$(id)"]
)
def test_release_rejects_mismatch_or_injected_input(version):
    with pytest.raises(ValueError):
        validate_version(version)


def test_release_accepts_matching_source_version():
    version = source_version()
    assert validate_version(version) == version


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
    # Only a manual release may skip registration; tag pushes always register.
    assert release["on"]["workflow_dispatch"]["inputs"]["register"]["default"] == "true"
    assert release["jobs"]["registry"]["if"] == "github.event_name == 'push' || inputs.register"
    # Syntax-check the validation script independently of imports.
    ast.parse((ROOT / "scripts/verify_release.py").read_text("utf-8"))
