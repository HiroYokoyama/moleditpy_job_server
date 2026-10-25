from types import SimpleNamespace

import pytest

from job_server.client import JobServerClient
from job_server.core import execution_core
from job_server.server_backend import JobServerBackend


def backend():
    return JobServerBackend(
        SimpleNamespace(
            server_url="http://127.0.0.1:8766",
            server_key_path=None,
            server_ca_file="",
            server_id="",
        )
    )


@pytest.mark.parametrize("identifier", ["../../admin/shutdown?ignored=", "invalid", ""])
def test_imported_job_ids_cannot_escape_the_job_endpoint(identifier, tmp_path):
    service = backend()

    def unexpected(*args, **kwargs):
        pytest.fail("An invalid imported job ID reached the HTTP client")

    service.client.request = unexpected
    job = SimpleNamespace(remote_job_id=identifier)
    for operation in (
        lambda: service.poll([job]),
        lambda: service.cancel(job),
        lambda: service.tail(job),
        lambda: service.files(job),
        lambda: service.fetch_file(job, "result.out", tmp_path / "new/out"),
    ):
        with pytest.raises(ValueError, match="UUID"):
            operation()
    assert not (tmp_path / "new").exists()


def test_invalid_reservation_id_does_not_start_upload_requests():
    client = JobServerClient("http://127.0.0.1:8766")
    requests = []

    def request(method, path, *args, **kwargs):
        requests.append((method, path))
        assert requests == [("POST", "/jobs")]
        return {"id": "../../admin/shutdown?ignored="}

    client.request = request
    with pytest.raises(ValueError, match="UUID"):
        client.submit({"name": "synthetic", "command": "echo synthetic"})


@pytest.mark.parametrize("walltime", ["00:60:00", "00:00:60"])
def test_out_of_range_walltime_is_not_silently_normalized(walltime):
    preset_type, _, _ = execution_core()
    preset = preset_type(walltime=walltime)
    job = SimpleNamespace(
        remote_dir_provided=False, remote_input="", start_after=0, force_run=False
    )
    with pytest.raises(ValueError, match="below 60"):
        backend().submit(preset, job, [])
