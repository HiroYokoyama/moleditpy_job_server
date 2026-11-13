"""A few hours of server load, recorded whether or not anyone is watching.

The dashboard and the Web UI draw their trend lines from this, so the lines
are already there when a window opens or a user signs in. It lives in memory
only: a restart begins it again, and the gap shows on the lines.
"""

import logging
import threading
import time
from collections import deque

INTERVAL = 10
KEEP_SECONDS = 6 * 60 * 60
# Each sample: time, running, waiting, CPU in use %, memory in use %,
# CPU reserved %, memory reserved %. Arrays rather than objects keep six hours small.
FIELDS = ("time", "running", "pending", "cpu_percent", "memory_percent",
          "reserved_cpu_percent", "reserved_memory_percent")


def share(used, total):
    try:
        return round(max(0.0, min(100.0, 100.0 * used / total)), 1)
    except (TypeError, ZeroDivisionError):
        return None


class ResourceHistory:
    def __init__(self, resources, usage, clock=time.time):
        self.resources, self.usage, self.clock = resources, usage, clock
        self.samples = deque(maxlen=KEEP_SECONDS // INTERVAL + 1)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None

    def sample(self):
        reading = {**self.resources(), **self.usage()}
        row = (
            round(self.clock(), 1), reading.get("running"), reading.get("pending"),
            reading.get("cpu_percent"), reading.get("memory_percent"),
            share(reading.get("reserved_cpus"), reading.get("cpus")),
            share(reading.get("reserved_memory_mb"), reading.get("memory_mb")),
        )
        with self.lock:
            self.samples.append(row)
        return row

    def since(self, after=0.0):
        """Samples newer than ``after``, oldest first."""
        now = self.clock()
        horizon = now - KEEP_SECONDS
        with self.lock:
            rows = [row for row in self.samples if row[0] > after and row[0] >= horizon]
        # The server's own time, so a browser on a PC whose clock differs places the samples right.
        return {"interval": INTERVAL, "keep_seconds": KEEP_SECONDS, "fields": FIELDS,
                "now": round(now, 1), "samples": rows}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="resource-history", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.sample()
            except Exception:
                logging.exception("Recording server load failed")
            self.stop_event.wait(INTERVAL)
