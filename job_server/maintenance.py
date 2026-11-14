"""Background upkeep: move ended jobs out of the working directory, back up the database,
and move activity log rows older than a few months into monthly archive files.

Runs on its own thread next to the dispatcher, so a long copy or backup never
delays starting the next job. Only the process that dispatches runs it.
"""

import calendar
import errno
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from .auth import DELETED_ROLE
from .files import DEVICES, is_link
from .jobs import TERMINAL, work_directory

#: Seconds between maintenance passes.
INTERVAL = 5
#: Seconds to wait before retrying a move that failed, such as a file held open.
RETRY_SECONDS = 60
#: Failed moves after which a job is left where it is.
MAX_ATTEMPTS = 10
BACKUP_PREFIX = "server-"
BACKUP_SUFFIX = ".db"
EVENT_ARCHIVE_PREFIX = "events-"
#: Most activity log rows moved in one pass, so the database lock is held only briefly.
EVENT_BATCH = 5000
EVENT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, job_id TEXT, actor TEXT, "
    "timestamp REAL NOT NULL, event TEXT NOT NULL, details TEXT NOT NULL)"
)
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
# Windows: rename across volumes. POSIX reports EXDEV.
_CROSS_DEVICE_WINERROR = 17


def safe_component(text, fallback):
    """One folder name that is valid on Windows and POSIX and cannot climb or hide."""
    name = _UNSAFE.sub("_", str(text)).strip().strip(".").rstrip(" ")
    name = name[:100].rstrip(" .")
    if not name:
        return fallback
    if DEVICES.match(name):
        name = "_" + name
    return name


def unique_folder(parent, name):
    candidate, number = parent / name, 2
    while candidate.exists() or is_link(candidate):
        candidate = parent / f"{name}_{number}"
        number += 1
    return candidate


def _cross_device(error):
    return error.errno == errno.EXDEV or getattr(error, "winerror", None) == _CROSS_DEVICE_WINERROR


class Maintenance:
    def __init__(self, db, jobs, settings, supervisor_alive=lambda job: False):
        self.db, self.jobs, self.settings = db, jobs, settings
        self.supervisor_alive = supervisor_alive
        self.stop_event = threading.Event()
        self.thread = None
        self.failures = {}  # job id -> (attempts, next attempt monotonic time)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="job-maintenance", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=30)

    def _loop(self):
        while not self.stop_event.is_set():
            self.run_once()
            self.stop_event.wait(INTERVAL)

    def run_once(self):
        for step in (self.relocate_finished, self.backup_if_due, self.archive_events):
            try:
                step()
            except Exception:
                logging.exception("Maintenance step failed")

    # Finished jobs -------------------------------------------------------

    def relocate_finished(self):
        if not self.settings.finished_dir:
            return
        rows = self.db.rows(
            "SELECT j.*,u.username,u.role AS owner_role FROM jobs j LEFT JOIN users u ON u.id=j.owner_id "
            "WHERE j.relocate=1 ORDER BY j.ended_at,j.id LIMIT 100"
        )
        for job in rows:
            if self.stop_event.is_set():
                return
            if job["state"] not in TERMINAL:
                continue
            attempts, due = self.failures.get(job["id"], (0, 0))
            if time.monotonic() < due or self.supervisor_alive(job):
                continue
            try:
                self.relocate(job)
                self.failures.pop(job["id"], None)
            except Exception as exc:
                attempts += 1
                logging.warning("Moving job %s failed: %s", job["id"], exc)
                if attempts >= MAX_ATTEMPTS:
                    self.failures.pop(job["id"], None)
                    self._finish(job, "", "job_move_failed", error=f"{type(exc).__name__}: {exc}")
                else:
                    self.failures[job["id"]] = (attempts, time.monotonic() + RETRY_SECONDS)

    def _finish(self, job, archive_path, event, **details):
        with self.db.transaction() as c:
            c.execute(
                "UPDATE jobs SET relocate=0,archive_path=? WHERE id=? AND relocate=1",
                (archive_path, job["id"]),
            )
            self.db.event(c, event, job_id=job["id"], **details)

    def relocate(self, job):
        """Move one ended job's folder to <finished>/<user>/<job name>."""
        source = work_directory(self.settings, job["work_root"], job["id"])
        if job["archive_path"] or not source.is_dir():
            # Cancelled during upload, or already moved: nothing to move.
            with self.db.transaction() as c:
                c.execute("UPDATE jobs SET relocate=0 WHERE id=?", (job["id"],))
            return
        if is_link(source) or is_link(source.parent):
            self._finish(job, "", "job_move_skipped", reason="linked job folder")
            return
        base = self.settings.finished_root
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if job["owner_role"] == DELETED_ROLE:
            owner = "deleted-users"  # The tombstone name is only an ID.
        else:
            owner = safe_component(job["username"] or "", "unknown-user")
        owner_dir = base / owner
        owner_dir.mkdir(exist_ok=True, mode=0o700)
        if is_link(owner_dir) or not owner_dir.resolve().is_relative_to(base):
            raise OSError(f"{owner_dir} is a link")
        name = safe_component(json.loads(job["spec"])["name"], "job")
        target = unique_folder(owner_dir, name)
        try:
            os.rename(source, target)
        except OSError as error:
            if not _cross_device(error):
                raise  # Such as a file still open: try again later.
            partial = owner_dir / f".{name}.partial-{job['id']}"
            shutil.rmtree(partial, ignore_errors=True)
            try:
                shutil.copytree(source, partial, symlinks=True)
                os.rename(partial, target)
            except BaseException:
                shutil.rmtree(partial, ignore_errors=True)
                raise
            # The copy is complete and in place; record it before removing the original.
            self._finish(job, str(target), "job_files_moved", destination=str(target))
            shutil.rmtree(source, ignore_errors=True)
        else:
            self._finish(job, str(target), "job_files_moved", destination=str(target))
        try:
            source.parent.rmdir()  # jobs/<id>, now empty
        except OSError:
            pass

    # Database backups ----------------------------------------------------

    def backups(self):
        folder = self.settings.backup_root
        if not folder.is_dir():
            return []
        found = [
            path for path in folder.iterdir()
            if path.name.startswith(BACKUP_PREFIX) and path.name.endswith(BACKUP_SUFFIX)
            and path.is_file() and not is_link(path)
        ]
        # Oldest first by time written: names of copies made in one second do not sort so.
        return sorted(found, key=lambda path: (path.stat().st_mtime, path.name))

    def backup_if_due(self):
        if not self.settings.db_backup:
            return None
        existing = self.backups()
        if existing and time.time() - existing[-1].stat().st_mtime < self.settings.db_backup_hours * 3600:
            return None
        return self.backup_now()

    def backup_now(self):
        """Write a consistent copy of server.db, then keep only the newest few."""
        folder = self.settings.backup_root
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if is_link(folder):
            raise OSError(f"{folder} is a link")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = folder / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
        number = 2
        while target.exists():
            target = folder / f"{BACKUP_PREFIX}{stamp}-{number}{BACKUP_SUFFIX}"
            number += 1
        partial = folder / f".{target.name}.partial"
        partial.unlink(missing_ok=True)
        try:
            destination = sqlite3.connect(partial)
            try:
                # The backup API copies a consistent snapshot even while jobs update it.
                with self.db.lock:
                    self.db.connection.backup(destination)
            finally:
                destination.close()
            if os.name != "nt":
                os.chmod(partial, 0o600)  # Holds password hashes and sessions.
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        for old in self.backups()[: -self.settings.db_backup_keep]:
            old.unlink(missing_ok=True)
        return Path(target)

    # Activity log archive -----------------------------------------------

    def event_archives(self):
        folder = self.settings.event_archive_root
        if not folder.is_dir():
            return []
        return sorted(
            path for path in folder.iterdir()
            if path.name.startswith(EVENT_ARCHIVE_PREFIX) and path.name.endswith(BACKUP_SUFFIX)
            and path.is_file() and not is_link(path)
        )

    def event_cutoff(self, now=None):
        """The local time ``event_keep_months`` calendar months before ``now``."""
        moment = time.localtime(time.time() if now is None else now)
        months = moment.tm_year * 12 + moment.tm_mon - 1 - self.settings.event_keep_months
        year, month = divmod(months, 12)
        month += 1
        day = min(moment.tm_mday, calendar.monthrange(year, month)[1])
        return time.mktime(
            (year, month, day, moment.tm_hour, moment.tm_min, moment.tm_sec, 0, 0, -1)
        )

    def archive_events(self, now=None):
        """Move rows older than ``event_keep_months`` into events-YYYY-MM.db files.

        Rows are copied and committed to the archive before they are deleted here,
        and copying is keyed on ``seq``, so an interrupted pass only repeats work.
        Returns the number of rows moved.
        """
        cutoff = self.event_cutoff(now)
        moved = 0
        while not self.stop_event.is_set():
            # Oldest first by seq, which uses the primary key rather than a scan.
            rows = self.db.rows("SELECT * FROM events ORDER BY seq LIMIT ?", (EVENT_BATCH,))
            old = []
            for row in rows:
                if row["timestamp"] >= cutoff:
                    break
                old.append(row)
            if not old:
                break
            by_month = {}
            for row in old:
                month = time.strftime("%Y-%m", time.localtime(row["timestamp"]))
                by_month.setdefault(month, []).append(row)
            for month, group in by_month.items():
                self._write_event_archive(month, group)
            with self.db.transaction() as c:
                c.execute(
                    "DELETE FROM events WHERE seq BETWEEN ? AND ?",
                    (old[0]["seq"], old[-1]["seq"]),
                )
            moved += len(old)
            if len(old) < len(rows):
                break
        return moved

    def _write_event_archive(self, month, rows):
        folder = self.settings.event_archive_root
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if is_link(folder):
            raise OSError(f"{folder} is a link")
        target = folder / f"{EVENT_ARCHIVE_PREFIX}{month}{BACKUP_SUFFIX}"
        if is_link(target):
            raise OSError(f"{target} is a link")
        connection = sqlite3.connect(target)
        try:
            connection.execute(EVENT_SCHEMA)
            connection.executemany(
                "INSERT OR IGNORE INTO events(seq,job_id,actor,timestamp,event,details) "
                "VALUES (:seq,:job_id,:actor,:timestamp,:event,:details)",
                rows,
            )
            connection.commit()
        finally:
            connection.close()
        if os.name != "nt":
            os.chmod(target, 0o600)
