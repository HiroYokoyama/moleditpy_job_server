import errno
import os
import sqlite3
import time
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from job_server.api import create_app
from job_server.maintenance import Maintenance, safe_component
from job_server.settings import Settings

from .conftest import enroll, reserve
from .test_execution import wait_job


def make_client(tmp_path, **extra):
    settings = Settings(
        data_dir=str(tmp_path / "data"), cpus=2, memory_mb=1024,
        finished_dir=str(tmp_path / "finished"), **extra,
    )
    return TestClient(create_app(settings, dispatch=False))


def maintenance(client):
    return client.app.state.scheduler.maintenance


def run_job(client, identity, name="water opt", command="echo example > result.txt"):
    job_id = reserve(client, identity, command=command, name=name).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/commit", headers=identity["headers"])
    job = wait_job(client, job_id, {"DONE", "FAILED", "LOST"})
    # A move waits for the supervisor to exit; let it, so each test is deterministic.
    deadline = time.monotonic() + 20
    while client.app.state.scheduler.supervisor_alive(job) and time.monotonic() < deadline:
        time.sleep(0.05)
    return job_id


def test_finished_job_moves_to_user_and_job_name_and_stays_downloadable(tmp_path):
    with make_client(tmp_path) as client:
        alice = enroll(client)
        job_id = run_job(client, alice)
        settings = client.app.state.settings
        original = settings.root / "jobs" / job_id / "work"
        assert (original / "result.txt").is_file()
        maintenance(client).relocate_finished()
        target = settings.finished_root / "alice" / "water opt"
        assert (target / "result.txt").is_file()
        assert not (settings.root / "jobs" / job_id).exists()
        job = client.app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        assert job["archive_path"] == str(target) and job["relocate"] == 0
        public = client.get(f"/api/v1/jobs/{job_id}", headers=alice["headers"]).json()
        assert "archive_path" not in public and "relocate" not in public
        download = client.get(
            f"/api/v1/jobs/{job_id}/files/result.txt", headers=alice["headers"]
        )
        assert download.status_code == 200 and b"example" in download.content
        listing = client.get(f"/api/v1/browse?path={job_id}", headers=alice["headers"])
        assert "result.txt" in [entry["name"] for entry in listing.json()["entries"]]
        events = [e["event"] for e in client.app.state.db.rows("SELECT event FROM events")]
        assert "job_files_moved" in events


def test_same_job_name_gets_a_number_and_users_are_kept_apart(tmp_path):
    with make_client(tmp_path) as client:
        alice, bob = enroll(client), enroll(client, "bob")
        for identity in (alice, alice, bob):
            run_job(client, identity, name="scan")
        maintenance(client).relocate_finished()
        root = client.app.state.settings.finished_root
        assert sorted(p.name for p in (root / "alice").iterdir()) == ["scan", "scan_2"]
        assert [p.name for p in (root / "bob").iterdir()] == ["scan"]


def test_failed_jobs_move_too_and_pending_jobs_do_not(tmp_path):
    with make_client(tmp_path) as client:
        alice = enroll(client)
        failed = run_job(client, alice, name="bad", command="exit 3")
        waiting = reserve(client, alice, name="later").json()["id"]
        client.post(f"/api/v1/jobs/{waiting}/commit", headers=alice["headers"])
        maintenance(client).relocate_finished()
        root = client.app.state.settings.finished_root / "alice"
        assert (root / "bad").is_dir()
        assert not (root / "later").exists()
        db = client.app.state.db
        assert db.one("SELECT state FROM jobs WHERE id=?", (failed,))["state"] == "FAILED"


def test_cancelled_pending_job_moves_its_inputs(tmp_path):
    with make_client(tmp_path) as client:
        alice = enroll(client)
        job_id = reserve(client, alice, name="never").json()["id"]
        client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
        client.post(f"/api/v1/jobs/{job_id}/cancel", headers=alice["headers"])
        maintenance(client).relocate_finished()
        assert (client.app.state.settings.finished_root / "alice" / "never").is_dir()


def test_cancelled_during_upload_has_nothing_to_move(tmp_path):
    with make_client(tmp_path) as client:
        alice = enroll(client)
        job_id = reserve(client, alice, name="partial").json()["id"]
        client.post(f"/api/v1/jobs/{job_id}/cancel", headers=alice["headers"])
        maintenance(client).relocate_finished()
        job = client.app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        assert job["relocate"] == 0 and job["archive_path"] == ""


def test_without_the_setting_jobs_stay_put(client, alice):
    job_id = run_job(client, alice)
    maintenance(client).relocate_finished()
    job = client.app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["relocate"] == 0 and job["archive_path"] == ""
    assert (client.app.state.settings.root / "jobs" / job_id / "work").is_dir()


def test_cross_volume_move_copies_then_removes_the_original(tmp_path):
    with make_client(tmp_path) as client:
        alice = enroll(client)
        job_id = run_job(client, alice, name="far")
        real_rename = os.rename
        calls = []

        def rename(source, target):
            if not calls:
                calls.append(source)
                raise OSError(errno.EXDEV, "cross-device")
            return real_rename(source, target)

        with mock.patch("job_server.maintenance.os.rename", rename):
            maintenance(client).relocate_finished()
        settings = client.app.state.settings
        assert (settings.finished_root / "alice" / "far" / "result.txt").is_file()
        assert not (settings.root / "jobs" / job_id).exists()
        assert not list((settings.finished_root / "alice").glob(".*partial*"))


def test_a_held_file_is_retried_later_not_copied(tmp_path):
    with make_client(tmp_path) as client:
        alice = enroll(client)
        job_id = run_job(client, alice, name="busy")
        upkeep = maintenance(client)
        with mock.patch(
            "job_server.maintenance.os.rename", side_effect=PermissionError("in use")
        ):
            upkeep.relocate_finished()
        job = client.app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        assert job["relocate"] == 1 and job["archive_path"] == ""
        assert upkeep.failures[job_id][0] == 1
        upkeep.failures[job_id] = (1, 0)  # Retry now.
        upkeep.relocate_finished()
        assert (client.app.state.settings.finished_root / "alice" / "busy").is_dir()


def test_dashboard_folder_lookup_follows_the_move(tmp_path):
    from job_server.jobs import work_directory

    with make_client(tmp_path) as client:
        job_id = run_job(client, enroll(client), name="opened")
        maintenance(client).relocate_finished()
        row = client.app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        folder = work_directory(
            client.app.state.settings, row["work_root"], job_id, archive_path=row["archive_path"]
        )
        assert folder == client.app.state.settings.finished_root / "alice" / "opened"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("water opt", "water opt"),
        ("../../etc", "_.._etc"),
        ("a/b\\c:d", "a_b_c_d"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("...", "job"),
        ("", "job"),
        ("trailing. ", "trailing"),
        ("x" * 300, "x" * 100),
    ],
)
def test_safe_component(text, expected):
    assert safe_component(text, "job") == expected


@pytest.mark.parametrize("sub", ["jobs", "control", "tls", "backups"])
def test_finished_folder_must_not_be_a_server_folder(tmp_path, sub):
    data = tmp_path / "data"
    with pytest.raises(ValueError, match="Finished jobs folder"):
        Settings(data_dir=str(data), finished_dir=str(data / sub / "x")).validate()


def test_finished_folder_must_not_be_or_contain_the_data_folder(tmp_path):
    data = tmp_path / "data"
    for value in (data, tmp_path):
        with pytest.raises(ValueError, match="Finished jobs folder"):
            Settings(data_dir=str(data), finished_dir=str(value)).validate()
    with pytest.raises(ValueError, match="absolute"):
        Settings(data_dir=str(data), finished_dir="relative").validate()
    Settings(data_dir=str(data), finished_dir=str(tmp_path / "finished")).validate()


def test_backup_folder_validation(tmp_path):
    data = tmp_path / "data"
    with pytest.raises(ValueError, match="backup folder"):
        Settings(data_dir=str(data), db_backup_dir=str(data / "jobs")).validate()
    with pytest.raises(ValueError, match="backup folder"):
        Settings(
            data_dir=str(data), finished_dir=str(tmp_path / "f"),
            db_backup_dir=str(tmp_path / "f" / "b"),
        ).validate()
    with pytest.raises(ValueError, match="db_backup_hours"):
        Settings(data_dir=str(data), db_backup_hours=0).validate()
    with pytest.raises(ValueError, match="db_backup"):
        Settings(data_dir=str(data), db_backup="yes").validate()


# Database backups --------------------------------------------------------


def test_backup_is_a_readable_copy_of_the_database(client, alice):
    upkeep = maintenance(client)
    path = upkeep.backup_if_due()
    assert path.parent == client.app.state.settings.root / "backups"
    connection = sqlite3.connect(path)
    try:
        names = [row[0] for row in connection.execute("SELECT username FROM users")]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
    assert names == ["alice"]
    # Not due again until the interval has passed.
    assert upkeep.backup_if_due() is None
    assert len(upkeep.backups()) == 1


def test_backup_runs_again_after_the_interval_and_prunes_old_copies(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path / "data"), db_backup_keep=3,
        db_backup_dir=str(tmp_path / "elsewhere"),
    ).validate()
    with TestClient(create_app(settings, dispatch=False)) as client:
        upkeep = maintenance(client)
        for _ in range(5):
            path = upkeep.backup_if_due()
            assert path is not None and path.parent == (tmp_path / "elsewhere").resolve()
            old = path.stat().st_mtime - 25 * 3600
            os.utime(path, (old, old))
        assert len(upkeep.backups()) == 3
        assert not list((tmp_path / "elsewhere").glob(".*partial"))


def test_backups_can_be_turned_off(tmp_path):
    settings = Settings(data_dir=str(tmp_path / "data"), db_backup=False)
    with TestClient(create_app(settings, dispatch=False)) as client:
        assert maintenance(client).backup_if_due() is None
        assert not (settings.root / "backups").exists()


def test_a_failing_step_does_not_stop_the_other(client):
    upkeep = maintenance(client)
    with mock.patch.object(Maintenance, "relocate_finished", side_effect=RuntimeError("x")):
        upkeep.run_once()
    assert len(upkeep.backups()) == 1
