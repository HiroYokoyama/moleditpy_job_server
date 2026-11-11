"""Every signed-in user sees the shared queue and how many jobs wait ahead of theirs."""

from .conftest import enroll
from .test_queue_controls import pending


def queue(client, identity):
    response = client.get("/api/v1/queue", headers=identity["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def test_each_waiting_job_reports_the_jobs_ahead_of_it(client, alice):
    bob = enroll(client, "bob")
    first = pending(client, alice, name="first")
    second = pending(client, bob, name="second")
    third = pending(client, alice, name="third")
    ahead = {
        job["id"]: job["queue_ahead"]
        for job in client.get("/api/v1/jobs", headers=alice["headers"]).json()
    }
    assert ahead == {first: 0, third: 2}
    assert client.get(f"/api/v1/jobs/{second}", headers=bob["headers"]).json()["queue_ahead"] == 1


def test_priority_moves_a_job_forward_in_the_count(client, alice):
    admin = enroll(client, "queue-admin", "admin")
    pending(client, alice, name="first")
    late = pending(client, alice, name="late")
    response = client.post(
        f"/api/v1/admin/jobs/{late}/priority", json={"priority": 5}, headers=admin["headers"]
    )
    assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/jobs/{late}", headers=alice["headers"]).json()["queue_ahead"] == 0


def test_the_queue_names_owners_and_jobs_but_keeps_details_private(client, alice):
    bob = enroll(client, "bob")
    mine = pending(client, alice, name="my calculation")
    pending(client, bob, name="bobs calculation", command="secret-program --flag")
    listed = queue(client, alice)
    assert listed["running"] == [] and listed["paused"] is False
    first, second = listed["waiting"]
    assert (first["position"], first["owner"], first["name"], first["mine"]) == (
        1, "alice", "my calculation", True
    )
    assert first["job_id"] == mine
    assert (second["position"], second["owner"], second["name"], second["mine"]) == (
        2, "bob", "bobs calculation", False
    )
    # Another user's job ID, command and files are not part of the listing.
    assert second["job_id"] is None
    assert "secret-program" not in str(listed)
    assert set(second) == {
        "job_id", "name", "owner", "mine", "cpus", "memory_mb", "priority",
        "position", "display_state", "force_run",
    }


def test_an_administrator_gets_every_job_id(client, alice):
    admin = enroll(client, "queue-admin", "admin")
    job = pending(client, alice, name="example")
    assert queue(client, admin)["waiting"][0]["job_id"] == job


def test_the_queue_needs_a_sign_in(client):
    assert client.get("/api/v1/queue").status_code == 401
