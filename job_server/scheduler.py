import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from .jobs import PENDING_IN_ORDER, TERMINAL
from .history import ResourceHistory
from .maintenance import Maintenance
from .worker import atomic_json


class Scheduler:
    def __init__(self, db, jobs, settings):
        self.db, self.jobs, self.settings = db, jobs, settings
        self.stop_event = threading.Event()
        self.thread = None
        self.children = []
        self.maintenance = Maintenance(db, jobs, settings, self.supervisor_alive)
        self.history = ResourceHistory(self.resources, host_usage)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="job-dispatcher", daemon=True)
        self.thread.start()
        # Moves and backups can take minutes; they never hold up dispatching.
        self.maintenance.start()
        self.history.start()

    def stop(self):
        self.stop_event.set()
        self.maintenance.stop()
        self.history.stop()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logging.exception("Dispatcher tick failed")
            self.stop_event.wait(0.2)

    @staticmethod
    def supervisor_alive(job):
        """Whether this job's own supervisor process still exists; a reused PID does not count."""
        if not job["pid"]:
            return False
        try:
            process = psutil.Process(job["pid"])
            return (
                abs(process.create_time() - job["process_started"]) < 0.01
                and process.is_running()
                and process.status() != psutil.STATUS_ZOMBIE
            )
        except psutil.NoSuchProcess:
            return False
        except (psutil.AccessDenied, TypeError):
            # Unknown is not gone: never release a job that might still be computing.
            return True

    def reconcile(self):
        self.children[:] = [child for child in self.children if child.poll() is None]
        for job in self.db.rows("SELECT * FROM jobs WHERE state='RUNNING'"):
            control = self.settings.root / "control" / job["id"]
            if (control / "done.json").exists():
                result = json.loads((control / "done.json").read_text("utf-8"))
                if result["state"] not in TERMINAL:
                    continue
                if result["reason"] == "termination_needs_recheck":
                    self._mark_recheck(job, "NEEDS_RECHECK: termination could not be verified")
                    continue
                with self.db.transaction() as c:
                    c.execute(
                        "UPDATE jobs SET state=?,exit_code=?,ended_at=?,reason=?,relocate=? "
                        "WHERE id=? AND state='RUNNING'",
                        (
                            result["state"],
                            result["exit_code"],
                            result["ended_at"],
                            result["reason"],
                            self.settings.relocates,
                            job["id"],
                        ),
                    )
                    self.db.event(
                        c,
                        "job_finished",
                        job_id=job["id"],
                        state=result["state"],
                        reason=result["reason"],
                    )
                continue
            if not job["pid"] and (control / "worker.json").exists():
                identity = json.loads((control / "worker.json").read_text("utf-8"))
                with self.db.transaction() as c:
                    c.execute(
                        "UPDATE jobs SET pid=?,process_started=? WHERE id=?",
                        (identity["pid"], identity["process_started"], job["id"]),
                    )
                job.update(identity)
            if job["pid"]:
                try:
                    process = psutil.Process(job["pid"])
                    alive = (
                        abs(process.create_time() - job["process_started"]) < 0.01
                        and process.is_running()
                        and process.status() != psutil.STATUS_ZOMBIE
                    )
                except psutil.NoSuchProcess:
                    alive = False
                except psutil.AccessDenied:
                    # Unknown is not evidence that a running job has disappeared.
                    continue
            else:
                alive = time.time() - job["started_at"] < 10
            if not alive:
                # Keep uncertain allocations reserved. Never resubmit a claimed job.
                self._mark_recheck(job, "NEEDS_RECHECK: supervisor identity missing")

    def _mark_recheck(self, job, reason):
        if job["reason"] == reason:
            return
        with self.db.transaction() as c:
            c.execute(
                "UPDATE jobs SET reason=? WHERE id=? AND state='RUNNING' AND reason<>?",
                (reason, job["id"], reason),
            )

    def tick(self):
        self.reconcile()
        with self.db.transaction() as c:
            # A durable force request may wait for a dependency. Losing the
            # requesting administrator's authority must revoke the override
            # before priority ordering or resource checks are evaluated.
            for revoked in c.execute(
                """SELECT j.id,j.force_actor FROM jobs j LEFT JOIN users u ON u.id=j.force_actor
                   WHERE j.state='PENDING' AND j.force_run=1
                   AND (u.id IS NULL OR u.enabled<>1 OR u.role<>'admin')"""
            ).fetchall():
                c.execute("UPDATE jobs SET force_run=0,force_actor=NULL WHERE id=?", (revoked["id"],))
                self.db.event(c, "job_force_run_revoked", job_id=revoked["id"],
                              requested_by=revoked["force_actor"], reason="administrator_unavailable")
            paused = self.db.metadata("paused") == "true"
            running = self.db.rows("SELECT * FROM jobs WHERE state='RUNNING'")
            used_cpu = sum(json.loads(j["spec"])["resources"]["cpus_per_task"] for j in running)
            used_memory = sum(json.loads(j["spec"])["resources"]["memory_mb"] for j in running)
            for job in self.db.rows(PENDING_IN_ORDER):
                forced = bool(job["force_run"])
                if paused and not forced:
                    continue
                spec = json.loads(job["spec"])
                predecessor_id = spec["after_job"]
                if predecessor_id:
                    predecessor = self.db.one(
                        "SELECT state FROM jobs WHERE id=?", (predecessor_id,)
                    )
                    if predecessor["state"] not in TERMINAL or (
                        predecessor["state"] != "DONE" and not spec["chain_any"]
                    ):
                        continue
                active = sum(j["owner_id"] == job["owner_id"] for j in running)
                resources = spec["resources"]
                if not forced and active >= self.settings.max_active_per_user:
                    continue
                # A request for nothing always fits, even when a forced job has
                # already taken the budget past its limit.
                if not forced and (
                    (resources["cpus_per_task"] and used_cpu + resources["cpus_per_task"] > self.settings.cpus)
                    or (resources["memory_mb"] and used_memory + resources["memory_mb"] > self.settings.memory_mb)
                ):
                    continue
                control = self.settings.root / "control" / job["id"]
                control.mkdir(exist_ok=True, mode=0o700)
                self.settings.save(control / "settings.json")
                atomic_json(
                    control / "request.json", {"spec": spec, "work_root": job["work_root"]}
                )
                c.execute(
                    "UPDATE jobs SET state='RUNNING',started_at=?,force_run=0,force_actor=NULL WHERE id=? AND state='PENDING'",
                    (time.time(), job["id"]),
                )
                self.db.event(c, "dispatch_claimed", actor=job["force_actor"] if forced else None,
                              job_id=job["id"], forced=forced, priority=job["priority"])
                claimed = job
                break
            else:
                return
        # Commit the dispatch claim BEFORE launch: a crash in this gap requires
        # review, never another execution. One dispatcher is protected by OS lock.
        environment = os.environ.copy()
        environment["PYTHONPATH"] = (
            str(Path(__file__).resolve().parents[1])
            + os.pathsep
            + environment.get("PYTHONPATH", "")
        )
        with (control / "supervisor.log").open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "job_server.worker",
                    str(control / "settings.json"),
                    claimed["id"],
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=os.name != "nt",
                creationflags=0x08000000 | 0x00000008 if os.name == "nt" else 0,
            )
        self.children.append(process)
        try:
            identity = psutil.Process(process.pid).create_time()
        except psutil.NoSuchProcess:
            return
        with self.db.transaction() as c:
            c.execute(
                "UPDATE jobs SET pid=?,process_started=? WHERE id=?",
                (process.pid, identity, claimed["id"]),
            )

    def resources(self):
        running = self.db.rows("SELECT spec FROM jobs WHERE state='RUNNING'")
        cpu = sum(json.loads(j["spec"])["resources"]["cpus_per_task"] for j in running)
        memory = sum(json.loads(j["spec"])["resources"]["memory_mb"] for j in running)
        return {
            "cpus": self.settings.cpus,
            "memory_mb": self.settings.memory_mb,
            "reserved_cpus": cpu,
            "reserved_memory_mb": memory,
            "free_cpus": self.settings.cpus - cpu,
            "free_memory_mb": self.settings.memory_mb - memory,
            "running": len(running),
            "pending": self.db.one("SELECT count(*) AS n FROM jobs WHERE state='PENDING'")["n"],
            "paused": self.db.metadata("paused") == "true",
            "enforcement": "advisory",
        }


_usage_lock = threading.Lock()
_usage = (0.0, None)
_cpu_times = None


def _cpu_percent():
    """Busy share since the previous reading, whichever request thread takes it.

    psutil.cpu_percent keeps its previous sample per thread, so on a worker pool
    it reads 0.0 whenever a request lands on a thread it has not seen.
    """
    global _cpu_times
    times = psutil.cpu_times()
    # Linux already counts guest time inside user time.
    total = sum(times) - getattr(times, "guest", 0) - getattr(times, "guest_nice", 0)
    idle = times.idle + getattr(times, "iowait", 0)
    previous, _cpu_times = _cpu_times, (total, idle)
    if previous is None or total <= previous[0]:
        return 0.0
    busy = 1 - (idle - previous[1]) / (total - previous[0])
    return round(max(0.0, min(1.0, busy)) * 100, 1)


def host_usage():
    """Whole-PC CPU and memory use, as distinct from the advisory job reservations."""
    global _usage
    with _usage_lock:
        # Readings moments apart, such as from a second window, would measure nothing.
        taken, reading = _usage
        if reading is not None and time.monotonic() - taken < 1:
            return reading
        try:
            memory = psutil.virtual_memory()
            reading = {
                "cpu_percent": _cpu_percent(),
                "memory_percent": memory.percent,
                "memory_used_mb": (memory.total - memory.available) // (1024 * 1024),
                "memory_total_mb": memory.total // (1024 * 1024),
                # CPU use is a share of every thread; the cores mark where hyper-threads start.
                "cpu_threads": psutil.cpu_count(),
                "cpu_cores": psutil.cpu_count(logical=False),
            }
        except (OSError, psutil.Error):
            reading = {
                "cpu_percent": None, "memory_percent": None,
                "memory_used_mb": None, "memory_total_mb": None,
                "cpu_threads": None, "cpu_cores": None,
            }
        _usage = (time.monotonic(), reading)
        return reading
