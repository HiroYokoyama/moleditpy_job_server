import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from .jobs import TERMINAL
from .worker import atomic_json


class Scheduler:
    def __init__(self, db, jobs, settings):
        self.db, self.jobs, self.settings = db, jobs, settings
        self.stop_event = threading.Event()
        self.thread = None
        self.children = []

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="job-dispatcher", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logging.exception("Dispatcher tick failed")
            self.stop_event.wait(0.2)

    def reconcile(self):
        self.children[:] = [child for child in self.children if child.poll() is None]
        for job in self.db.rows("SELECT * FROM jobs WHERE state='RUNNING'"):
            control = self.settings.root / "control" / job["id"]
            if (control / "done.json").exists():
                result = json.loads((control / "done.json").read_text("utf-8"))
                if result["state"] not in TERMINAL:
                    continue
                if result["reason"] == "termination_needs_recheck":
                    with self.db.transaction() as c:
                        c.execute(
                            "UPDATE jobs SET reason='NEEDS_RECHECK: termination could not be verified' WHERE id=?",
                            (job["id"],),
                        )
                    continue
                with self.db.transaction() as c:
                    c.execute(
                        "UPDATE jobs SET state=?,exit_code=?,ended_at=?,reason=? WHERE id=? AND state='RUNNING'",
                        (
                            result["state"],
                            result["exit_code"],
                            result["ended_at"],
                            result["reason"],
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
                with self.db.transaction() as c:
                    c.execute(
                        "UPDATE jobs SET reason='NEEDS_RECHECK: supervisor identity missing' WHERE id=?",
                        (job["id"],),
                    )

    def tick(self):
        self.reconcile()
        with self.db.transaction() as c:
            if self.db.metadata("paused") == "true":
                return
            running = self.db.rows("SELECT * FROM jobs WHERE state='RUNNING'")
            used_cpu = sum(json.loads(j["spec"])["resources"]["cpus_per_task"] for j in running)
            used_memory = sum(json.loads(j["spec"])["resources"]["memory_mb"] for j in running)
            for job in self.db.rows(
                "SELECT * FROM jobs WHERE state='PENDING' ORDER BY created_at,id"
            ):
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
                if active >= self.settings.max_active_per_user:
                    continue
                if (
                    used_cpu + resources["cpus_per_task"] > self.settings.cpus
                    or used_memory + resources["memory_mb"] > self.settings.memory_mb
                ):
                    continue
                control = self.settings.root / "control" / job["id"]
                control.mkdir(exist_ok=True, mode=0o700)
                self.settings.save(control / "settings.json")
                atomic_json(control / "request.json", {"spec": spec})
                c.execute(
                    "UPDATE jobs SET state='RUNNING',started_at=? WHERE id=? AND state='PENDING'",
                    (time.time(), job["id"]),
                )
                self.db.event(c, "dispatch_claimed", job_id=job["id"])
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
