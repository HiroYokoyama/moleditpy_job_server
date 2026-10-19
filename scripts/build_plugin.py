"""Build the installable plugin ZIP from exactly the pinned execution source."""

import runpy
import sys
import tempfile
from pathlib import Path
from shutil import copy2, copytree
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from job_server import PLUGIN_VERSION  # noqa: E402


def main():
    build = runpy.run_path(str(ROOT / "setup.py"))
    destination = ROOT / "dist" / f"job_server_{PLUGIN_VERSION}.zip"
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        plugin = Path(temporary) / "job_server"
        copytree(
            ROOT / "job_server",
            plugin,
            ignore=lambda _p, n: [name for name in n if name == "__pycache__"],
        )
        build["copy_core"](plugin / "_execution")
        for name in ("LICENSE", "COPYING", "LICENSING.md"):
            copy2(ROOT / name, plugin / name)
        with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
            for path in sorted(plugin.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(temporary))
    print(destination)


if __name__ == "__main__":
    main()
