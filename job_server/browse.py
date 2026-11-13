"""Owner-authorized virtual job folders; never accept an OS path from a client."""

import json
import os
import stat
import tempfile
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

from .errors import ServerError, reject
from .files import contained, is_link, open_regular, sha256, validate_path


def split_path(path):
    if not path:
        return None, ""
    head, _, relative = path.partition("/")
    try:
        identifier = str(uuid.UUID(head))
    except (ValueError, AttributeError):
        reject(400, "invalid_path", "Use a job UUID followed by a relative path")
    if head != identifier or ("/" in path and not relative):
        reject(400, "invalid_path", "Use a canonical job path without trailing slashes")
    if relative:
        validate_path(relative, readable=True)
    return identifier, relative


def target_for(jobs, path, principal):
    identifier, relative = split_path(path)
    if identifier is None:
        return None
    job = jobs.authorize(identifier, principal)
    if job["state"] == "UPLOADING":
        reject(409, "upload_open", "Commit this job before browsing its folder")
    root = jobs.directory(identifier)
    if is_link(root):
        reject(400, "unsafe_path", "Linked job roots are forbidden")
    return contained(root, relative, readable=True) if relative else root


def listing(jobs, path, principal, offset=0, limit=1000):
    target = target_for(jobs, path, principal)
    if target is None:
        rows = jobs.db.rows(
            "SELECT id,spec,state FROM jobs WHERE state<>'UPLOADING' AND (?='admin' OR owner_id=?) "
            "ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
            (principal["role"], principal["user_id"], limit + 1, offset),
        )
        entries = [
            {"name": row["id"], "path": row["id"], "type": "directory",
             "job_name": json.loads(row["spec"])["name"], "state": row["state"]}
            for row in rows
        ]
    else:
        if not target.is_dir():
            reject(404, "directory_missing", "Job folder not found")
        entries = []
        # Bound enumeration as well as response size. No recursive walk or hashing here.
        with os.scandir(target) as iterator:
            for count, entry in enumerate(iterator):
                if count >= 50000:
                    reject(413, "directory_too_large", "Folder contains too many entries")
                try:
                    child = contained(target, entry.name, readable=True)
                    info = child.lstat()
                    if stat.S_ISDIR(info.st_mode):
                        kind = "directory"
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        kind = "file"
                    else:
                        continue
                    entries.append({"name": entry.name, "path": path + "/" + entry.name,
                                    "type": kind, "size": info.st_size if kind == "file" else None})
                except (ServerError, OSError):
                    continue
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"]))
        entries = entries[offset:offset + limit + 1]
    more = len(entries) > limit
    return {"path": path, "entries": entries[:limit],
            "next_offset": offset + limit if more else None}


def metadata(jobs, path, principal, digest=False):
    target = target_for(jobs, path, principal)
    if target is None:
        return {"path": path, "exists": True, "type": "directory"}
    if target.is_dir():
        return {"path": path, "exists": True, "type": "directory"}
    with open_regular(target) as stream:
        size = os.fstat(stream.fileno()).st_size
    result = {"path": path, "exists": True, "type": "file", "size": size}
    if digest:
        result["sha256"] = sha256(target)
    return result


def archive(jobs, path, principal):
    target = target_for(jobs, path, principal)
    if target is None or not target.is_dir():
        reject(404, "directory_missing", "Select a job folder")
    stream = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=jobs.settings.root)
    cap = jobs.settings.storage_quota_mb * 1024 * 1024
    total, count = 0, 0
    try:
        with ZipFile(stream, "w", compression=ZIP_DEFLATED, allowZip64=True) as bundle:
            pending = [(target, "")]
            while pending:
                directory, prefix = pending.pop()
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        count += 1
                        if count > 50000:
                            reject(413, "archive_too_large", "Folder contains too many entries")
                        relative = prefix + entry.name
                        try:
                            child = contained(target, relative, readable=True)
                            info = child.lstat()
                            if stat.S_ISDIR(info.st_mode):
                                bundle.writestr(relative + "/", b"")
                                pending.append((child, relative + "/"))
                                continue
                            source = open_regular(child)
                        except (ServerError, OSError):
                            continue
                        with source, bundle.open(relative, "w", force_zip64=True) as output:
                            while data := source.read(1024 * 1024):
                                total += len(data)
                                if total > cap:
                                    reject(413, "archive_too_large", "Folder exceeds archive size limit")
                                output.write(data)
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise
