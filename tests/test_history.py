"""Server load is recorded on the server, so trend lines exist before anyone looks."""

from job_server.history import FIELDS, INTERVAL, KEEP_SECONDS, ResourceHistory, share


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def history(clock, **usage):
    resources = {"running": 2, "pending": 1, "reserved_cpus": 3, "cpus": 4,
                 "reserved_memory_mb": 512, "memory_mb": 2048}
    reading = {"cpu_percent": 41.5, "memory_percent": 63.0, **usage}
    return ResourceHistory(lambda: resources, lambda: reading, clock=clock)


def test_a_sample_records_load_and_reserved_shares():
    clock = Clock()
    recorder = history(clock)
    row = recorder.sample()
    assert dict(zip(FIELDS, row)) == {
        "time": clock.now, "running": 2, "pending": 1, "cpu_percent": 41.5,
        "memory_percent": 63.0, "reserved_cpu_percent": 75.0, "reserved_memory_percent": 25.0,
    }


def test_since_returns_only_newer_samples_within_the_kept_window():
    clock = Clock()
    recorder = history(clock)
    for _ in range(5):
        recorder.sample()
        clock.now += INTERVAL
    first = recorder.since()["samples"]
    assert len(first) == 5
    assert recorder.since(first[2][0])["samples"] == first[3:]
    clock.now += KEEP_SECONDS
    assert recorder.since()["samples"] == [], "Nothing older than the kept window is returned"


def test_unknown_usage_is_recorded_as_none():
    clock = Clock()
    row = history(clock, cpu_percent=None, memory_percent=None).sample()
    assert row[3] is None and row[4] is None
    assert share(1, 0) is None and share(None, 4) is None and share(9, 4) == 100.0


def test_the_endpoint_needs_a_session_and_returns_the_scheduler_history(client, alice):
    scheduler = client.app.state.scheduler
    scheduler.history.sample()
    assert client.get("/api/v1/system/resources/history").status_code == 401
    data = client.get("/api/v1/system/resources/history", headers=alice["headers"]).json()
    assert data["interval"] == INTERVAL and data["fields"] == list(FIELDS)
    assert isinstance(data["now"], float), "The reply carries the server's time for the browser's plots"
    assert len(data["samples"]) >= 1
    later = client.get(
        f"/api/v1/system/resources/history?since={data['samples'][-1][0]}", headers=alice["headers"]
    ).json()
    assert later["samples"] == []
