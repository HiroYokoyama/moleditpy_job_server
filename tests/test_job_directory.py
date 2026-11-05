import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from job_server.api import create_app
from job_server.settings import Settings

from .conftest import enroll, reserve
from .test_execution import wait_job


def app_client(settings):
    return TestClient(create_app(settings, dispatch=False))


@pytest.mark.parametrize("value", ["relative/jobs", "jobs", 5])
def test_job_directory_must_be_an_absolute_path(tmp_path, value):
    with pytest.raises(ValueError, match="Job working directory"):
        Settings(data_dir=str(tmp_path), job_dir=value).validate()


def test_job_directory_rejects_parent_links_files_and_tls(tmp_path):
    data = tmp_path / "data"
    with pytest.raises(ValueError, match="absolute"):
        Settings(data_dir=str(data), job_dir=str(tmp_path / ".." / "jobs")).validate()
    file = tmp_path / "file"
    file.write_text("x")
    with pytest.raises(ValueError, match="real directory"):
        Settings(data_dir=str(data), job_dir=str(file)).validate()
    with pytest.raises(ValueError, match="TLS"):
        Settings(data_dir=str(data), job_dir=str(data / "tls" / "jobs")).validate()
    with pytest.raises(ValueError, match="invalid characters"):
        Settings(data_dir=str(data), job_dir=str(tmp_path / "a\nb")).validate()


@pytest.mark.skipif(os.name == "nt", reason="Symlinks need privileges on Windows")
def test_job_directory_rejects_a_link(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="real directory"):
        Settings(data_dir=str(tmp_path / "data"), job_dir=str(link)).validate()


def test_default_keeps_jobs_in_the_data_directory(client, alice):
    job_id = reserve(client, alice).json()["id"]
    settings = client.app.state.settings
    assert settings.job_root == settings.root
    job = client.app.state.db.one("SELECT work_root FROM jobs WHERE id=?", (job_id,))
    assert job["work_root"] == ""
    directory = client.app.state.jobs.directory(job_id, True)
    assert directory == settings.root / "staging" / job_id / "work"
    assert directory.is_dir()


def test_custom_directory_holds_job_files_and_runs_the_job(tmp_path):
    custom = tmp_path / "custom"
    settings = Settings(
        data_dir=str(tmp_path / "data"), job_dir=str(custom), cpus=2, memory_mb=1024
    )
    with app_client(settings) as client:
        alice = enroll(client)
        job_id = reserve(client, alice, command="echo example > result.txt").json()["id"]
        assert (custom.resolve() / "staging" / job_id / "work").is_dir()
        public = client.get(f"/api/v1/jobs/{job_id}", headers=alice["headers"]).json()
        assert "work_root" not in public
        client.post(f"/api/v1/jobs/{job_id}/commit", headers=alice["headers"])
        job = wait_job(client, job_id, {"DONE", "FAILED", "LOST"})
        assert job["state"] == "DONE"
        assert (custom.resolve() / "jobs" / job_id / "work" / "result.txt").is_file()
        assert not (settings.root / "jobs" / job_id).exists()
        assert client.app.state.jobs.directory(job_id).parent.parent == custom.resolve() / "jobs"
    assert (settings.root / "server.db").is_file()
    assert not (custom / "server.db").exists()


def test_changing_the_setting_keeps_earlier_jobs_where_they_are(tmp_path):
    first = Settings(data_dir=str(tmp_path / "data"), cpus=2, memory_mb=1024)
    with app_client(first) as client:
        old_id = reserve(client, enroll(client)).json()["id"]
    second = replace(first, job_dir=str(tmp_path / "custom"))
    with app_client(second) as client:
        jobs = client.app.state.jobs
        assert jobs.directory(old_id, True) == first.root / "staging" / old_id / "work"
        assert jobs.directory(old_id, True).is_dir()
        new_id = reserve(client, enroll(client, "bob")).json()["id"]
        assert jobs.directory(new_id, True) == second.job_root / "staging" / new_id / "work"


def test_existing_database_gains_the_column_and_old_jobs_stay_put(tmp_path):
    from job_server.db import Database

    settings = Settings(data_dir=str(tmp_path / "data"), cpus=2, memory_mb=1024)
    with app_client(settings) as client:
        job_id = reserve(client, enroll(client)).json()["id"]
    database = Database(settings.root)
    database.connection.execute("ALTER TABLE jobs DROP COLUMN work_root")
    database.connection.commit()
    database.close()
    with app_client(settings) as client:
        directory = client.app.state.jobs.directory(job_id, True)
        assert directory == settings.root / "staging" / job_id / "work"
