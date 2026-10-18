"""Validate release metadata and assets without importing executable plugin code."""

import argparse
import ast
import hashlib
import re
import tarfile
import tomllib
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def source_version(root=ROOT):
    package = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    tree = ast.parse((root / "job_server/__init__.py").read_text("utf-8"))
    plugin = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PLUGIN_VERSION"
            for target in node.targets
        )
    )
    if package != plugin or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?", package):
        raise ValueError("Python package and plugin versions must match and use X.Y.Z with an optional a/b/rc prerelease")
    return package


def validate_version(version, root=ROOT):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?", version) or len(version) > 32:
        raise ValueError("Invalid version: use X.Y.Z with an optional a/b/rc prerelease without leading v")
    if version != source_version(root):
        raise ValueError("Release version does not match package/plugin source")
    return version


def safe_members(names):
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError("Archive contains an unsafe path")
        if any(part in {"__pycache__", ".git", "test-results"} for part in path.parts):
            raise ValueError("Archive contains development artifacts")
        if path.name in {"server.db", "settings.json"} or path.suffix in {".key", ".pem", ".env"}:
            raise ValueError("Archive contains runtime credentials or configuration")


def verify_artifacts(version, root=ROOT):
    dist = root / "dist"
    server = dist / f"job_server_{version}.zip"
    wheel = dist / f"moleditpy_job_server-{version}-py3-none-any.whl"
    source = dist / f"moleditpy_job_server-{version}.tar.gz"
    with ZipFile(server) as archive:
        safe_members(archive.namelist())
        for name in ("__init__.py", "_execution/schedulers/windows.py", "web/icon.png", "LICENSE"):
            archive.getinfo("job_server/" + name)
    with ZipFile(wheel) as archive:
        safe_members(archive.namelist())
        archive.getinfo("job_server/_execution/schedulers/base.py")
        archive.getinfo("job_server/web/app.js")
    with tarfile.open(source) as archive:
        safe_members(archive.getnames())
        if any(member.issym() or member.islnk() for member in archive.getmembers()):
            raise ValueError("Source archive contains linked files")
    assets = [server, wheel, source]
    checksums = []
    for path in assets:
        with path.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
    (dist / "SHA256SUMS").write_text(
        "".join(checksums),
        "utf-8",
    )
    return assets


def verify_companion(root=ROOT):
    """Validate the test-only companion without adding it to release checksums."""
    with ZipFile(root / "dist" / "job_manager_job_server.zip") as archive:
        safe_members(archive.namelist())
        archive.getinfo("job_manager/server_backend.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?")
    parser.add_argument("--artifacts", action="store_true")
    parser.add_argument("--companion", action="store_true", help="Validate the test-only companion")
    args = parser.parse_args()
    version = validate_version(args.version if args.version is not None else source_version())
    if args.artifacts:
        verify_artifacts(version)
    if args.companion:
        verify_companion()
    print(
        f"Verified release {version}"
        + (" and all three release assets + SHA256SUMS" if args.artifacts else "")
        + ("; test-only companion verified" if args.companion else "")
    )


if __name__ == "__main__":
    main()
