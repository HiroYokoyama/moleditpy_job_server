import runpy
import sys
from pathlib import Path


def test_plugin_core_packaging_needs_only_standard_library(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "setuptools", None)
    source = Path(__file__).resolve().parents[1]
    setup = runpy.run_path(str(source / "setup.py"))
    target = tmp_path / "_execution"
    setup["copy_core"](target)
    assert (target / "models.py").is_file()
    assert (target / "schedulers/windows.py").is_file()

    for name in ("LICENSE", "COPYING", "LICENSING.md"):
        assert (target / name).read_bytes() == (source / name).read_bytes()
    assert (target / "UPSTREAM_LICENSE").read_bytes() == (source / "upstream/job_manager/LICENSE").read_bytes()
