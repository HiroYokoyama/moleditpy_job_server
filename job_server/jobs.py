import hashlib
import json
import os
import re
import time
import uuid

from .errors import reject
from .files import contained, manifest, open_regular, sha256

TERMINAL = {"DONE", "FAILED", "CANCELLED", "LOST"}


class Jobs:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        for name in ("staging", "jobs", "control", "logs"):
            (settings.root / name).mkdir(exist_ok=True, mode=0o700)
        if settings.execution_os == "wsl":
            from .executor.wsl import native_root

            for name in ("staging", "jobs"):
                (native_root(settings) / name).mkdir(parents=True, exist_ok=True)

    def directory(self, job_id, staging=False):
        root = self.settings.root
        if self.settings.execution_os == "wsl":
            from .executor.wsl import native_root

            root = native_root(self.settings)
        return root / ("staging" if staging else "jobs") / job_id / "work"

    def authorize(self, job_id, principal):
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job or (job["owner_id"] != principal["user_id"] and principal["role"] != "admin"):
            reject(404, "job_missing", "Job not found")
        return job

    def public(self, job):
        result = {k: v for k, v in job.items() if k not in {"pid", "process_started"}}
        result["spec"] = json.loads(result["spec"])
        if result["state"] == "PENDING" and result["spec"]["after_job"]:
            predecessor = self.db.one(
                "SELECT state FROM jobs WHERE id=?", (result["spec"]["after_job"],)
            )
            result["display_state"] = (
                "BLOCKED"
                if predecessor["state"] in TERMINAL
                and predecessor["state"] != "DONE"
                and not result["spec"]["chain_any"]
                else "QUEUED"
            )
        else:
            result["display_state"] = result["state"]
        return result

    def missing(self, job_id):
        return [
            dict(row)
            for row in self.db.rows(
                "SELECT path,size,sha256 FROM job_files WHERE job_id=? AND complete=0", (job_id,)
            )
        ]

    def create(self, spec, principal, key):
        try:
            key = str(uuid.UUID(key))
        except (ValueError, TypeError, AttributeError):
            reject(400, "idempotency_required", "Idempotency-Key must be a UUID")
        specification = spec.model_dump()
        canonical = json.dumps(specification, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        owner = principal["user_id"]
        with self.db.transaction() as c:
            old = c.execute(
                "SELECT * FROM idempotency_keys WHERE user_id=? AND key=?", (owner, key)
            ).fetchone()
            if old:
                if old["digest"] != digest:
                    reject(409, "idempotency_conflict", "Key already used for a different request")
                return {"id": old["job_id"], "missing_files": self.missing(old["job_id"])}
            if self.db.metadata("paused") == "true":
                reject(503, "queue_paused", "Queue is paused")
            if (
                spec.executor not in {"auto", self.settings.execution_os}
                or spec.resources.cpus_per_task > self.settings.cpus
                or spec.resources.memory_mb > self.settings.memory_mb
            ):
                reject(
                    422,
                    "unsupported_resources",
                    "Executor or requested resources exceed server capacity",
                )
            queued = c.execute(
                "SELECT count(*) FROM jobs WHERE owner_id=? AND state IN ('UPLOADING','PENDING')",
                (owner,),
            ).fetchone()[0]
            if queued >= self.settings.max_queued_per_user:
                reject(429, "queue_quota", "User queue quota exceeded")
            reserved = c.execute(
                "SELECT coalesce(sum(f.size),0) FROM job_files f JOIN jobs j ON j.id=f.job_id WHERE j.owner_id=?",
                (owner,),
            ).fetchone()[0]
            if (
                reserved + sum(file.size for file in spec.files)
                > self.settings.storage_quota_mb * 1024 * 1024
            ):
                reject(413, "storage_quota", "User input storage quota exceeded")
            if spec.after_job:
                predecessor = c.execute(
                    "SELECT owner_id FROM jobs WHERE id=?", (spec.after_job,)
                ).fetchone()
                if not predecessor or predecessor[0] != owner:
                    reject(404, "dependency_missing", "Dependency not found")
            job_id = str(uuid.uuid4())
            c.execute(
                "INSERT INTO jobs(id,owner_id,spec,state,created_at) VALUES (?,?,?,'UPLOADING',?)",
                (job_id, owner, canonical, time.time()),
            )
            for file in spec.files:
                c.execute(
                    "INSERT INTO job_files VALUES (?,?,?,?,0)",
                    (job_id, file.path, file.size, file.sha256),
                )
            c.execute("INSERT INTO idempotency_keys VALUES (?,?,?,?)", (owner, key, job_id, digest))
            self.directory(job_id, True).mkdir(parents=True, mode=0o700)
            self.db.event(c, "job_created", owner, job_id, spec=specification)
        return {"id": job_id, "missing_files": self.missing(job_id)}

    def upload(self, job_id, relative, data, content_range, principal):
        with self.db.transaction() as c:
            job = self.authorize(job_id, principal)
            if job["state"] != "UPLOADING":
                reject(409, "upload_closed", "Job no longer accepts uploads")
            file = c.execute(
                "SELECT * FROM job_files WHERE job_id=? AND path=?", (job_id, relative)
            ).fetchone()
            if not file:
                reject(404, "file_missing", "File is not in the manifest")
            start, end, total = 0, len(data) - 1, file["size"]
            if content_range is not None:
                match = re.fullmatch(
                    r"bytes ([0-9]{1,19})-([0-9]{1,19})/([0-9]{1,19})", content_range
                )
                if not match:
                    reject(400, "invalid_range", "Invalid Content-Range")
                start, end, total = map(int, match.groups())
            if total != file["size"] or end - start + 1 != len(data) or start < 0 or end >= total:
                if not (total == 0 and not data and not content_range):
                    reject(400, "invalid_range", "Chunk length or total differs from manifest")
            path = contained(self.directory(job_id, True), relative, create=True)
            with open_regular(path, "r+b") as stream:
                stream.seek(0, os.SEEK_END)
                current = stream.tell()
                if start > current or (start < current and end >= current):
                    reject(409, "upload_offset", f"Resume at offset {current}")
                if start < current:
                    stream.seek(start)
                    if stream.read(len(data)) != data:
                        reject(409, "chunk_conflict", "Retry differs from stored chunk")
                else:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            complete = path.stat().st_size == total
            if complete and sha256(path) != file["sha256"]:
                # A corrupt complete input can be retransmitted from zero.
                with open_regular(path, "r+b") as stream:
                    stream.truncate(0)
                c.execute(
                    "UPDATE job_files SET complete=0 WHERE job_id=? AND path=?", (job_id, relative)
                )
                result = {"checksum_error": True}
            else:
                c.execute(
                    "UPDATE job_files SET complete=? WHERE job_id=? AND path=?",
                    (int(complete), job_id, relative),
                )
                result = {"offset": path.stat().st_size, "complete": complete}
        if result.get("checksum_error"):
            reject(409, "checksum_mismatch", "SHA-256 mismatch; resume at offset 0")
        return result

    def upload_status(self, job_id, principal):
        job = self.authorize(job_id, principal)
        result = []
        for file in self.db.rows("SELECT * FROM job_files WHERE job_id=?", (job_id,)):
            path = contained(self.directory(job_id, job["state"] == "UPLOADING"), file["path"])
            result.append({**file, "offset": path.stat().st_size if path.exists() else 0})
        return result

    def commit(self, job_id, principal):
        with self.db.transaction() as c:
            job = self.authorize(job_id, principal)
            if job["state"] != "UPLOADING":
                if job["state"] in {"PENDING", "RUNNING"} | TERMINAL:
                    return self.public(job)
                reject(409, "invalid_state", "Job cannot be committed")
            staging = self.directory(job_id, True)
            work = self.directory(job_id)
            source = staging if staging.exists() else work
            for file in self.db.rows("SELECT * FROM job_files WHERE job_id=?", (job_id,)):
                path = contained(source, file["path"])
                if (
                    not path.exists()
                    or path.stat().st_size != file["size"]
                    or sha256(path) != file["sha256"]
                ):
                    reject(409, "incomplete_upload", "All manifest files must pass verification")
            work.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if staging.exists():
                staging.replace(work)
            c.execute("UPDATE jobs SET state='PENDING' WHERE id=?", (job_id,))
            self.db.event(c, "job_committed", principal["user_id"], job_id)
        return self.public(self.authorize(job_id, principal))

    def cancel(self, job_id, principal):
        with self.db.transaction() as c:
            job = self.authorize(job_id, principal)
            if job["state"] in TERMINAL:
                return self.public(job)
            if job["state"] != "RUNNING":
                c.execute(
                    "UPDATE jobs SET state='CANCELLED',ended_at=?,cancel_requested=1 WHERE id=?",
                    (time.time(), job_id),
                )
            else:
                c.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
                control = self.settings.root / "control" / job_id
                control.mkdir(exist_ok=True)
                (control / "cancel").touch()
            self.db.event(c, "cancel_requested", principal["user_id"], job_id)
        return self.public(self.authorize(job_id, principal))

    def outputs(self, job_id, principal):
        self.authorize(job_id, principal)
        return manifest(self.directory(job_id))
