"""Uncertain running jobs retain allocations without repeated UPDATE transactions."""

import json
import time

import pytest

from .conftest import reserve


@pytest.mark.parametrize("termination", [False, True])
def test_reconcile_marks_uncertain_job_once_without_repeated_transactions(
    client, alice, termination
):
    job_id = reserve(client, alice).json()["id"]
    db, scheduler = client.app.state.db, client.app.state.scheduler
    with db.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state='RUNNING',started_at=? WHERE id=?", (time.time() - 20, job_id)
        )
    if termination:
        control = client.app.state.settings.root / "control" / job_id
        control.mkdir()
        (control / "done.json").write_text(
            json.dumps({"state": "LOST", "reason": "termination_needs_recheck"})
        )
    scheduler.reconcile()
    changes = db.connection.total_changes
    traces = []
    db.connection.set_trace_callback(traces.append)
    try:
        for _ in range(5):
            scheduler.reconcile()
    finally:
        db.connection.set_trace_callback(None)
    assert db.connection.total_changes == changes
    assert not any("BEGIN" in statement or "UPDATE" in statement for statement in traces)
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["state"] == "RUNNING" and "NEEDS_RECHECK" in job["reason"]
    assert scheduler.resources()["reserved_cpus"] == 1
    with db.transaction() as connection:
        connection.execute("UPDATE jobs SET state='DONE',reason='finished' WHERE id=?", (job_id,))
    scheduler._mark_recheck({**job, "reason": "old snapshot"}, "NEEDS_RECHECK: stale")
    assert db.one("SELECT reason FROM jobs WHERE id=?", (job_id,))["reason"] == "finished"
