"""The regression copy backports upstream fixes without touching the submodule."""

from pathlib import Path
import importlib.util
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "upstream/job_manager/job_manager/remote_runner_ps.py"


def regression_script(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("run_regression", ROOT / "scripts/run_regression.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not RUNNER.exists(), reason="upstream submodule not initialized")
def test_reaper_backport_looks_again_before_moving_a_job_with_no_pid(tmp_path, monkeypatch):
    copy = tmp_path / "remote_runner_ps.py"
    shutil.copy2(RUNNER, copy)
    patch = regression_script(monkeypatch).patch_powershell_reaper
    patch(tmp_path)
    patched = copy.read_text("utf-8")
    compile(patched, str(copy), "exec")
    assert patched.count("Start-Sleep -Milliseconds 500") == 1
    reaper = patched[patched.index("function Invoke-Reap"):]
    assert reaper.index("Start-Sleep -Milliseconds 500") < reaper.index("-Destination ('done")
    # Applying it again, as to an upstream that already has the fix, changes nothing.
    patch(tmp_path)
    assert copy.read_text("utf-8") == patched


def test_reaper_backport_refuses_code_it_does_not_recognise(tmp_path, monkeypatch):
    (tmp_path / "remote_runner_ps.py").write_text("changed = True\n", "utf-8")
    with pytest.raises(RuntimeError, match="review the backport"):
        regression_script(monkeypatch).patch_powershell_reaper(tmp_path)
