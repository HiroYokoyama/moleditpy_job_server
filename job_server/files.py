import hashlib
import os
import re
import stat
from pathlib import Path

from .errors import reject


RESERVED = {
    ".moleditpy_rc",
    ".moleditpy_rc.tmp",
    "moleditpy_run.sh",
    "moleditpy_run.ps1",
    "job.log",
    "payload.ps1",
    ".server_process",
}
DEVICES = re.compile(r"^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)", re.I)


def validate_path(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value:
        reject(400, "invalid_path", "Use a relative slash-separated path")
    parts = value.split("/")
    for part in parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or len(part) > 255
            or any(ord(c) < 32 or c in ':<>"|?*' for c in part)
            or DEVICES.match(part)
            or part.casefold() in RESERVED
        ):
            reject(400, "invalid_path", "Unsafe or reserved file path")
    return value


def contained(root, relative, create=False):
    root = Path(root)
    validate_path(relative)
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        reject(400, "unsafe_path", "Linked job root is forbidden")
    target = root / relative
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            reject(400, "unsafe_path", "Links and junctions are forbidden")
    if not target.resolve().is_relative_to(root.resolve()):
        reject(400, "unsafe_path", "File escapes job directory")
    if create:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def open_regular(path, mode="rb"):
    flags = os.O_RDONLY if mode == "rb" else os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileNotFoundError:
        reject(404, "file_missing", "File not found")
    except OSError:
        reject(400, "unsafe_file", "Cannot open file safely")
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(descriptor)
        reject(400, "unsafe_file", "Only ordinary unlinked files can be transferred")
    return os.fdopen(descriptor, mode)


def sha256(path):
    digest = hashlib.sha256()
    with open_regular(path) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(root):
    result = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not (Path(directory) / name).is_symlink()
            and not (
                hasattr(Path(directory) / name, "is_junction")
                and (Path(directory) / name).is_junction()
            )
        ]
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if any(part.casefold() in RESERVED for part in relative.split("/")):
                continue
            from .errors import ServerError

            try:
                path = contained(root, relative)
                digest = sha256(path)
                result.append({"path": relative, "size": path.stat().st_size, "sha256": digest})
            except ServerError:
                continue
    return sorted(result, key=lambda item: item["path"])
