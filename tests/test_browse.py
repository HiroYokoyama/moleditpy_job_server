import io
import os
from zipfile import ZipFile

import pytest

from .conftest import enroll, reserve


def populated(client, alice):
    identifier = reserve(client, alice).json()["id"]
    client.post(f"/api/v1/jobs/{identifier}/commit", headers=alice["headers"])
    root = client.app.state.jobs.directory(identifier)
    (root / "nested" / "empty").mkdir(parents=True)
    (root / "nested" / "result.txt").write_bytes(b"synthetic result")
    (root / "job.log").write_bytes(b"private wrapper log")
    return identifier, root


def test_browse_is_owner_scoped_and_paginated(client, alice):
    identifier, _ = populated(client, alice)
    bob = enroll(client, "bob")
    other, _ = populated(client, bob)
    assert client.get("/api/v1/browse").status_code == 401
    page = client.get("/api/v1/browse", headers=alice["headers"]).json()
    assert [e["path"] for e in page["entries"]] == [identifier]
    assert page["next_offset"] is None
    admin = enroll(client, "admin", "admin")
    first = client.get("/api/v1/browse?limit=1", headers=admin["headers"]).json()
    second = client.get("/api/v1/browse?limit=1&offset=1", headers=admin["headers"]).json()
    assert {first["entries"][0]["path"], second["entries"][0]["path"]} == {identifier, other}
    assert first["next_offset"] == 1
    for route in ("browse", "browse/stat"):
        assert client.get(f"/api/v1/{route}", params={"path": other}, headers=alice["headers"]).status_code == 404
    assert client.get(f"/api/v1/jobs/{other}/archive", headers=alice["headers"]).status_code == 404


def test_lazy_listing_and_stat_do_not_hash_unless_requested(client, alice, monkeypatch):
    identifier, _ = populated(client, alice)
    def no_hash(*args):
        pytest.fail("Listing and size-only stat must not hash results")
    monkeypatch.setattr("job_server.browse.sha256", no_hash)
    listing = client.get("/api/v1/browse", params={"path": identifier}, headers=alice["headers"]).json()
    assert [entry["name"] for entry in listing["entries"]] == ["nested"]
    info = client.get("/api/v1/browse/stat", params={"path": identifier + "/nested/result.txt"}, headers=alice["headers"]).json()
    assert info["type"] == "file" and info["size"] == 16
    assert "sha256" not in info


@pytest.mark.parametrize("suffix", ["/../server.db", "/nested/../../server.db", "/C:/data", "/job.log", "/nested\\escape", "/nested/", "/nested/a:stream"])
def test_browse_rejects_unsafe_paths(client, alice, suffix):
    identifier, _ = populated(client, alice)
    for route in ("browse", "browse/stat"):
        assert client.get(f"/api/v1/{route}", params={"path": identifier + suffix}, headers=alice["headers"]).status_code == 400
    assert client.get(f"/api/v1/jobs/{identifier}/archive", params={"path": suffix[1:]}, headers=alice["headers"]).status_code == 400


def test_archive_preserves_subfolders_and_empty_folders_but_excludes_private_and_linked_files(client, alice, tmp_path):
    identifier, root = populated(client, alice)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"never transfer")
    os.link(outside, root / "hardlink.txt")
    response = client.get(f"/api/v1/jobs/{identifier}/archive", headers=alice["headers"])
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"nested/", "nested/empty/", "nested/result.txt"}
        assert archive.read("nested/result.txt") == b"synthetic result"
    result = client.get("/api/v1/browse", params={"path": identifier}, headers=alice["headers"]).json()
    assert [entry["name"] for entry in result["entries"]] == ["nested"]


def test_archive_is_bounded_and_uploading_jobs_are_hidden(client, alice):
    uploading = reserve(client, alice).json()["id"]
    assert client.get("/api/v1/browse", headers=alice["headers"]).json()["entries"] == []
    assert client.get("/api/v1/browse", params={"path": uploading}, headers=alice["headers"]).status_code == 409
    identifier, root = populated(client, alice)
    client.app.state.settings.storage_quota_mb = 1
    (root / "large").write_bytes(b"x" * (1024 * 1024 + 1))
    assert client.get(f"/api/v1/jobs/{identifier}/archive", headers=alice["headers"]).status_code == 413


def test_linked_directory_cannot_be_browsed_or_archived(client, alice, tmp_path):
    identifier, root = populated(client, alice)
    outside = tmp_path / "outside"
    outside.mkdir()
    if os.name == "nt":
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "linked"), str(outside)], check=True, capture_output=True)
    else:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    try:
        assert client.get("/api/v1/browse", params={"path": identifier + "/linked"}, headers=alice["headers"]).status_code == 400
        assert client.get(f"/api/v1/jobs/{identifier}/archive?path=linked", headers=alice["headers"]).status_code == 400
    finally:
        if os.name == "nt":
            os.rmdir(root / "linked")
        else:
            (root / "linked").unlink()
