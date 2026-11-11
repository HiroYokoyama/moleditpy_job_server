"""Attack sweeps kept from the 0.8.1 penetration test.

Each test plays an attacker against the HTTP API: no credentials, another
user's credentials, or an ordinary user reaching for administration. They
assert the refusal, and that a marker planted outside the job never comes back.
"""

import base64
import hashlib
import io
import os
import subprocess
import time
import uuid
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from job_server.auth import signing_message

from .conftest import enroll, reserve

API = "/api/v1"
MARKER = b"PENTEST-SECRET-OUTSIDE-THE-JOB"
KEY = base64.b64encode(b"\x01" * 32).decode()


def committed_job(client, identity, files=None):
    """A committed job whose work folder holds a result and a nested result."""
    files = {"in.txt": b"hello"} if files is None else files
    response = reserve(
        client, identity,
        files=[
            {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        ],
    )
    assert response.status_code == 201, response.text
    job = response.json()["id"]
    for name, data in files.items():
        uploaded = client.put(
            f"{API}/jobs/{job}/files/{name}", content=data,
            headers={**identity["headers"], "Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}"},
        )
        assert uploaded.status_code == 200, uploaded.text
    assert client.post(f"{API}/jobs/{job}/commit", headers=identity["headers"]).status_code == 200
    work = client.app.state.jobs.directory(job)
    (work / "out.txt").write_bytes(b"result")
    (work / "sub").mkdir()
    (work / "sub" / "n.txt").write_bytes(b"nested")
    # work is <root>/jobs/<id>/work, so the marker sits three levels up.
    (client.app.state.settings.root / "secret.txt").write_bytes(MARKER)
    return job


def call(client, method, path, headers=None, json=None):
    return client.request(method, API + path, headers=headers, json=json)


PROTECTED = [
    ("GET", "/capabilities", None),
    ("GET", "/me", None),
    ("GET", "/jobs", None),
    ("GET", "/browse", None),
    ("GET", "/browse/stat", None),
    ("GET", "/system/resources", None),
    ("GET", "/admin/jobs", None),
    ("GET", "/admin/users", None),
    ("GET", "/admin/devices", None),
    ("GET", "/admin/events", None),
    ("POST", "/jobs", {"command": "whoami"}),
    ("POST", "/auth/logout", {}),
    ("POST", "/me/password", {"current_password": "a-password", "new_password": "b-password"}),
    ("POST", "/me/devices", {"device_id": str(uuid.uuid4()), "public_key": KEY}),
    ("POST", "/auth/devices", {"device_id": str(uuid.uuid4()), "public_key": KEY}),
    ("POST", "/admin/users", {"username": "evil", "password": "evil-password", "role": "admin"}),
    ("POST", "/admin/queue/pause", {"paused": True}),
    ("POST", "/admin/shutdown", {}),
    ("POST", "/admin/users/00000000-0000-0000-0000-000000000000/disable", {}),
    ("POST", "/admin/users/00000000-0000-0000-0000-000000000000/role", {"role": "admin"}),
    ("DELETE", "/admin/users/00000000-0000-0000-0000-000000000000", None),
]


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED, ids=[f"{m} {p}" for m, p, _ in PROTECTED])
def test_protected_endpoint_refuses_anonymous_caller(client, method, path, body):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    assert call(client, method, path, headers, body).status_code == 401
    assert not client.app.state.db.rows("SELECT 1 FROM users")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", API + "/openapi.json"])
def test_api_schema_is_not_published(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("carrier", [
    "empty", "scheme_only", "oversized", "basic", "prefixed", "double_scheme", "lowercase_scheme",
    "cookie", "custom_header", "query",
])
def test_token_is_accepted_only_as_a_bearer_header(client, alice, carrier):
    token = alice["headers"]["Authorization"][7:]
    assert client.get(API + "/me", headers=alice["headers"]).status_code == 200
    headers, query = {}, ""
    if carrier == "empty":
        headers = {"Authorization": "Bearer "}
    elif carrier == "scheme_only":
        headers = {"Authorization": "Bearer"}
    elif carrier == "oversized":
        headers = {"Authorization": "Bearer " + "A" * 300}
    elif carrier == "basic":
        headers = {"Authorization": "Basic " + base64.b64encode(b"alice:" + token.encode()).decode()}
    elif carrier == "prefixed":
        headers = {"Authorization": "Bearer x" + token}
    elif carrier == "double_scheme":
        headers = {"Authorization": "Bearer Bearer " + token}
    elif carrier == "lowercase_scheme":
        headers = {"Authorization": "bearer " + token}
    elif carrier == "cookie":
        headers = {"Cookie": f"token={token}; access_token={token}"}
    elif carrier == "custom_header":
        headers = {"X-Api-Key": token, "X-Auth-Token": token}
    else:
        query = f"?token={token}&access_token={token}"
    assert client.get(API + "/me" + query, headers=headers).status_code == 401


def test_token_scopes_do_not_cross(client, alice):
    device = {"device_id": str(uuid.uuid4()), "public_key": KEY}
    # A used enrollment session is gone, and was never an API token.
    assert client.get(API + "/jobs", headers=alice["enrollment"]).status_code == 401
    # An access token cannot use the enrollment endpoint.
    assert client.post(API + "/auth/devices", headers=alice["headers"], json=device).status_code == 401
    web = client.post(
        API + "/auth/web-login", json={"username": "alice", "password": "example-password-123"}
    ).json()
    web_headers = {"Authorization": "Bearer " + web["token"]}
    assert client.get(API + "/me", headers=web_headers).status_code == 200
    # A browser session cannot turn itself into a persistent device.
    assert client.post(API + "/me/devices", headers=web_headers, json=device).status_code == 403
    assert client.post(API + "/auth/devices", headers=web_headers, json=device).status_code == 401
    assert client.post(API + "/auth/logout", headers=web_headers).status_code == 200
    assert client.get(API + "/me", headers=web_headers).status_code == 401


@pytest.mark.parametrize("origin", [
    "http://evil.example", "null", "http://127.0.0.1:8766.evil.example", "https://127.0.0.1:8766",
    "http://127.0.0.1:8766/", "http://LOCALHOST:8766", "file://",
])
def test_foreign_browser_origin_is_refused_without_cors_headers(client, origin):
    port = client.app.state.settings.port
    origin = origin.replace("8766", str(port))
    for response in (
        client.get(API + "/health", headers={"Origin": origin}),
        client.post(
            API + "/auth/web-login", headers={"Origin": origin},
            json={"username": "alice", "password": "example-password-123"},
        ),
    ):
        assert response.status_code == 403
        assert not any(name.lower().startswith("access-control") for name in response.headers)
    own = client.get(API + "/health", headers={"Origin": f"http://127.0.0.1:{port}"})
    assert own.status_code == 200
    assert not any(name.lower().startswith("access-control") for name in own.headers)


def test_foreign_host_header_gains_nothing_without_a_token(client):
    assert client.get(API + "/jobs", headers={"Host": "evil.example"}).status_code == 401


@pytest.mark.parametrize("path", [
    "/assets/../auth.py", "/assets/..%2fauth.py", "/assets/%2e%2e/auth.py", "/assets/..%5cauth.py",
    "/assets/%252e%252e/auth.py", "/assets/....//auth.py", "/assets/..%2f..%2fpyproject.toml",
    "/assets//../../auth.py", "/assets/%2e%2e%2f%2e%2e%2fjob_server/auth.py", "/..%2fjob_server/auth.py",
    "/assets/..%2f__init__.py",
])
def test_static_files_do_not_leave_the_web_root(client, path):
    response = client.get(path)
    assert response.status_code != 200
    assert b"scrypt" not in response.content and b"PLUGIN_VERSION" not in response.content


@pytest.mark.parametrize("extra", [
    {"owner_id": "00000000-0000-0000-0000-000000000000"},
    {"priority": 1000},
    {"force_run": True},
    {"state": "DONE"},
    {"work_root": "C:/"},
    {"shell": "bash"},
    {"command": "who\x00ami"},
    {"command": "   "},
    {"resources": {"cpus_per_task": -1}},
    {"resources": {"cpus_per_task": True}},
    {"resources": {"cpus_per_task": 1, "nodes": 4}},
], ids=lambda extra: next(iter(extra)))
def test_job_cannot_set_privileged_or_unknown_fields(client, alice, extra):
    assert reserve(client, alice, **{"command": "whoami", **extra}).status_code == 400
    assert not client.app.state.db.rows("SELECT 1 FROM jobs")


UNSAFE_PATHS = [
    "../x", "a/../../x", "a\\b", "/abs", "C:/x", "a:b", "job.log", "JOB.LOG", "payload.ps1",
    "moleditpy_run.sh", ".moleditpy_rc", ".server_process", "CON", "nul.txt", "com1", "LPT1.txt",
    "a.", "a ", "a/./b", "a//b", "x\x00y", "a\nb", "x" * 300, "a*b", "a?b", 'a"b', "a|b", "a<b",
]


@pytest.mark.parametrize("path", UNSAFE_PATHS, ids=[repr(path)[:24] for path in UNSAFE_PATHS])
def test_unsafe_manifest_path_is_rejected(client, alice, path):
    spec = [{"path": path, "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}]
    assert reserve(client, alice, files=spec).status_code == 400
    assert not client.app.state.db.rows("SELECT 1 FROM jobs")


@pytest.mark.parametrize("paths", [["a", "a/b"], ["A.txt", "a.txt"], ["d/x", "D/x"]])
def test_overlapping_manifest_paths_are_rejected(client, alice, paths):
    digest = hashlib.sha256(b"x").hexdigest()
    spec = [{"path": path, "size": 1, "sha256": digest} for path in paths]
    assert reserve(client, alice, files=spec).status_code == 400


MALFORMED = [
    ("GET", "/jobs?offset=-1", None),
    ("GET", "/jobs?limit=0", None),
    ("GET", "/jobs?limit=abc", None),
    ("GET", "/jobs?offset=99999999999999999999999", None),
    ("GET", "/jobs/%00", None),
    ("GET", "/jobs/'%20OR%201=1--", None),
    ("GET", "/browse?path=" + "a" * 3000, None),
    ("GET", "/browse?path='%20OR%201=1", None),
    ("GET", "/admin/events?after=-5", None),
    ("POST", "/jobs", b"null"),
    ("POST", "/jobs", b"[]"),
    ("POST", "/jobs", b"\xff\xfe{"),
    ("POST", "/jobs", b'{"command": "x", "files": "nope"}'),
    ("POST", "/jobs", b"[" * 100000),
    ("POST", "/auth/login", b"[" * 100000),
    ("POST", "/auth/token", b'{"device_id": 1, "nonce": [], "signature": {}}'),
]


@pytest.mark.parametrize(("method", "path", "body"), MALFORMED, ids=[f"{m} {p[:28]} {i}" for i, (m, p, _) in enumerate(MALFORMED)])
def test_malformed_input_is_a_client_error(client, alice, method, path, body):
    headers = {**alice["headers"], "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"}
    response = client.request(method, API + path, headers=headers, content=body)
    assert 400 <= response.status_code < 500


@pytest.mark.parametrize("key", [None, "", "x' OR 1=1", "not-a-uuid", "0" * 200])
def test_job_needs_a_uuid_idempotency_key(client, alice, key):
    headers = dict(alice["headers"])
    if key is not None:
        headers["Idempotency-Key"] = key
    assert client.post(API + "/jobs", headers=headers, json={"command": "whoami"}).status_code == 400


TRAVERSAL = [
    "../../../secret.txt", "..%2f..%2f..%2fsecret.txt", "%2e%2e/%2e%2e/%2e%2e/secret.txt",
    "%2e%2e%2f%2e%2e%2f%2e%2e%2fsecret.txt", "..%5c..%5c..%5csecret.txt",
    "....//....//....//secret.txt", "%252e%252e/%252e%252e/%252e%252e/secret.txt",
    "sub/../../../../secret.txt", "sub%2f..%2f..%2f..%2f..%2fsecret.txt", "C%3A/Windows/win.ini",
    "%5c%5c%3f%5cC%3A%5cWindows%5cwin.ini", "out.txt%3A%3A$DATA", "out.txt.", "out.txt%20",
    "out.txt%00", "out.txt%00.png", "CON", "NUL", "nul.txt", "job.log", "JOB.LOG", ".moleditpy_rc",
    "payload.ps1", "PAYLOAD.PS1", "moleditpy_run.ps1", "moleditpy_run.sh", ".server_process",
    "sub", "%2e", "..%00",
]


@pytest.mark.parametrize("payload", TRAVERSAL)
def test_download_and_stat_stay_inside_the_job(client, alice, payload):
    job = committed_job(client, alice)
    assert client.get(f"{API}/jobs/{job}/files/out.txt", headers=alice["headers"]).content == b"result"
    download = client.get(f"{API}/jobs/{job}/files/{payload}", headers=alice["headers"])
    assert download.status_code in (400, 404) and MARKER not in download.content
    for raw in (True, False):
        # Once as the client wrote it, once as a correctly encoded parameter.
        stat = (
            client.get(f"{API}/browse/stat?path={job}/{payload}&digest=true", headers=alice["headers"])
            if raw else
            client.get(f"{API}/browse/stat", params={"path": f"{job}/{payload}", "digest": "true"},
                       headers=alice["headers"])
        )
        assert MARKER not in stat.content
        assert stat.status_code in (400, 404) or (payload == "sub" and stat.json()["type"] == "directory")
        listing = client.get(f"{API}/browse", params={"path": f"{job}/{payload}"}, headers=alice["headers"])
        assert MARKER not in listing.content and b"secret.txt" not in listing.content


@pytest.mark.parametrize("form", ["..%2f..%2fcontrol", "%2e%2e", "{job}%2f..", "{JOB}", "{{{job}}}", "{hex}"])
def test_job_id_must_be_the_exact_identifier(client, alice, form):
    job = committed_job(client, alice)
    identifier = form.format(job=job, JOB=job.upper(), hex=job.replace("-", ""))
    for route in (f"/jobs/{identifier}/files/out.txt", f"/jobs/{identifier}/log", f"/jobs/{identifier}/archive"):
        assert client.get(API + route, headers=alice["headers"]).status_code in (400, 404)


@pytest.mark.parametrize("value", ["bytes=abc", "bytes=5-1", "bytes=0-0,2-3", "bytes=-5", "items=0-1", "bytes=99999999999999999999-"])
def test_malformed_range_is_rejected(client, alice, value):
    job = committed_job(client, alice)
    response = client.get(f"{API}/jobs/{job}/files/out.txt", headers={**alice["headers"], "Range": value})
    assert response.status_code == 416


def test_links_planted_inside_a_job_are_not_served(client, alice, tmp_path_factory):
    """A job runs as the server account and can plant links to anything it can read."""
    job = committed_job(client, alice)
    work = client.app.state.jobs.directory(job)
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_bytes(MARKER)
    planted = []
    try:
        os.link(outside / "secret.txt", work / "hard.txt")
        planted.append("hard.txt")
    except OSError:
        pass
    try:
        (work / "sym.txt").symlink_to(outside / "secret.txt")
        (work / "symdir").symlink_to(outside, target_is_directory=True)
        planted += ["sym.txt", "symdir/secret.txt"]
    except OSError:
        pass
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(work / "junction"), str(outside)],
            capture_output=True, check=False,
        )
        if created.returncode == 0:
            planted.append("junction/secret.txt")
    if not planted:
        pytest.skip("This account cannot create hard links, symlinks or junctions here")
    for path in planted:
        response = client.get(f"{API}/jobs/{job}/files/{path}", headers=alice["headers"])
        assert response.status_code in (400, 404) and MARKER not in response.content, path
        stat = client.get(f"{API}/browse/stat", params={"path": f"{job}/{path}", "digest": "true"},
                          headers=alice["headers"])
        assert stat.status_code in (400, 404), path
    roots = {path.split("/")[0] for path in planted}
    manifest = client.get(f"{API}/jobs/{job}/files", headers=alice["headers"]).json()
    assert not roots & {entry["path"].split("/")[0] for entry in manifest}
    listing = client.get(f"{API}/browse", params={"path": job}, headers=alice["headers"]).json()
    assert not roots & {entry["name"] for entry in listing["entries"]}
    archive = client.get(f"{API}/jobs/{job}/archive", headers=alice["headers"])
    assert archive.status_code == 200 and MARKER not in archive.content
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert "out.txt" in names and not any("secret" in name for name in names)
    for root in roots - {"hard.txt", "sym.txt"}:
        linked = client.get(f"{API}/jobs/{job}/archive", params={"path": root}, headers=alice["headers"])
        assert linked.status_code in (400, 404) and MARKER not in linked.content


def test_upload_is_bound_to_owner_manifest_and_state(client, alice):
    bob = enroll(client, "bob")
    digest = hashlib.sha256(b"hello").hexdigest()
    job = reserve(client, alice, files=[{"path": "in.txt", "size": 5, "sha256": digest}]).json()["id"]
    put = lambda who, path, data, total: client.put(  # noqa: E731
        f"{API}/jobs/{job}/files/{path}", content=data,
        headers={**who["headers"], "Content-Range": f"bytes 0-{len(data) - 1}/{total}"},
    )
    assert put(alice, "other.txt", b"x", 1).status_code == 404
    assert put(alice, "..%2f..%2fserver.db", b"x", 1).status_code in (400, 404)
    assert put(bob, "in.txt", b"hello", 5).status_code == 404
    assert put(alice, "in.txt", b"x" * 50, 50).status_code == 400
    assert client.post(f"{API}/jobs/{job}/commit", headers=bob["headers"]).status_code == 404
    assert client.post(f"{API}/jobs/{job}/commit", headers=alice["headers"]).status_code == 409
    assert client.get(f"{API}/browse", params={"path": job}, headers=alice["headers"]).status_code == 409
    assert put(alice, "in.txt", b"hello", 5).status_code == 200
    assert client.post(f"{API}/jobs/{job}/commit", headers=alice["headers"]).status_code == 200
    assert put(alice, "in.txt", b"hello", 5).status_code == 409


OTHER_USER = [
    ("GET", "/jobs/{job}"), ("GET", "/jobs/{job}/log"), ("GET", "/jobs/{job}/files"),
    ("GET", "/jobs/{job}/files/out.txt"), ("GET", "/jobs/{job}/archive"), ("GET", "/jobs/{job}/uploads"),
    ("POST", "/jobs/{job}/cancel"), ("POST", "/jobs/{job}/commit"), ("GET", "/browse?path={job}"),
    ("GET", "/browse/stat?path={job}/out.txt"), ("GET", "/browse?path={job}/sub"),
]


@pytest.mark.parametrize(("method", "path"), OTHER_USER, ids=[f"{m} {p}" for m, p in OTHER_USER])
def test_another_users_job_is_invisible(client, alice, method, path):
    bob = enroll(client, "bob")
    job = committed_job(client, alice)
    response = call(client, method, path.format(job=job), bob["headers"], {} if method == "POST" else None)
    assert response.status_code == 404 and b"result" not in response.content
    assert client.app.state.db.one("SELECT state FROM jobs WHERE id=?", (job,))["state"] == "PENDING"


def test_another_users_job_cannot_be_listed_chained_or_its_device_revoked(client, alice):
    bob = enroll(client, "bob")
    job = committed_job(client, alice)
    assert job not in client.get(API + "/jobs", headers=bob["headers"]).text
    assert job not in client.get(API + "/browse", headers=bob["headers"]).text
    assert reserve(client, bob, after_job=job).status_code == 404
    revoke = client.post(f"{API}/me/devices/{alice['device_id']}/revoke", headers=bob["headers"])
    assert revoke.status_code == 404
    assert client.get(f"{API}/jobs/{job}/files/out.txt", headers=alice["headers"]).status_code == 200


ADMIN_ONLY = [
    ("GET", "/admin/users", None), ("GET", "/admin/jobs", None), ("GET", "/admin/events", None),
    ("GET", "/admin/devices", None),
    ("POST", "/admin/users", {"username": "evil", "password": "evil-password", "role": "admin"}),
    ("POST", "/admin/users/{self}/role", {"role": "admin"}),
    ("POST", "/admin/users/{admin}/password", {"new_password": "pwned-password"}),
    ("POST", "/admin/users/{admin}/disable", {}),
    ("POST", "/admin/users/{self}/enable", {}),
    ("DELETE", "/admin/users/{admin}", None),
    ("POST", "/admin/queue/pause", {"paused": True}),
    ("POST", "/admin/jobs/{job}/priority", {"priority": 5}),
    ("POST", "/admin/jobs/{job}/force-run", {"force_run": True}),
    ("POST", "/admin/jobs/{job}/mark-lost", {}),
    ("POST", "/admin/devices/{device}/revoke", {}),
    ("POST", "/admin/shutdown", {}),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY, ids=[f"{m} {p}" for m, p, _ in ADMIN_ONLY])
def test_ordinary_user_cannot_reach_administration(client, method, path, body):
    admin = enroll(client, "admin", "admin")
    bob = enroll(client, "bob")
    job = committed_job(client, bob)
    path = path.format(self=bob["user"]["id"], admin=admin["user"]["id"], job=job, device=admin["device_id"])
    assert call(client, method, path, bob["headers"], body).status_code == 403
    db = client.app.state.db
    assert db.one("SELECT role FROM users WHERE id=?", (bob["user"]["id"],))["role"] == "user"
    assert db.one("SELECT count(*) AS n FROM users")["n"] == 2
    assert db.metadata("paused") == "false"
    assert client.get(API + "/admin/users", headers=admin["headers"]).status_code == 200


def test_administrator_cannot_lock_itself_out_or_skip_its_own_password(client):
    admin = enroll(client, "admin", "admin")
    own = admin["user"]["id"]
    for method, path, body in (
        ("POST", f"/admin/users/{own}/disable", {}),
        ("POST", f"/admin/users/{own}/role", {"role": "user"}),
        ("DELETE", f"/admin/users/{own}", None),
        ("POST", f"/admin/users/{own}/password", {"new_password": "another-password"}),
    ):
        assert call(client, method, path, admin["headers"], body).status_code == 409
    assert client.get(API + "/admin/users", headers=admin["headers"]).status_code == 200


@pytest.mark.parametrize("body", [
    {"username": "weak", "password": "abc"},
    {"username": "../x", "password": "long-enough-password"},
    {"username": "a b", "password": "long-enough-password"},
    {"username": "x" * 65, "password": "long-enough-password"},
    {"username": "ok", "password": "long-enough-password", "role": "deleted"},
    {"username": "ok", "password": "long-enough-password", "role": "root"},
    {"username": "ok", "password": "long-enough-password", "enabled": False},
])
def test_administrator_cannot_create_a_malformed_account(client, body):
    admin = enroll(client, "admin", "admin")
    assert client.post(API + "/admin/users", headers=admin["headers"], json=body).status_code == 400
    assert client.app.state.db.one("SELECT count(*) AS n FROM users")["n"] == 1


def test_sessions_end_with_disable_and_device_revocation(client):
    admin = enroll(client, "admin", "admin")
    bob = enroll(client, "bob")
    user = bob["user"]["id"]
    challenge = {"device_id": bob["device_id"]}
    assert client.post(f"{API}/admin/users/{user}/disable", headers=admin["headers"]).status_code == 200
    assert client.get(API + "/me", headers=bob["headers"]).status_code == 401
    assert client.post(API + "/auth/challenge", json=challenge).status_code == 401
    assert client.post(f"{API}/admin/users/{user}/enable", headers=admin["headers"]).status_code == 200
    # Enabling the account again does not bring the old token back.
    assert client.get(API + "/me", headers=bob["headers"]).status_code == 401
    assert client.post(API + "/auth/challenge", json=challenge).status_code == 200
    revoked = client.post(f"{API}/admin/devices/{bob['device_id']}/revoke", headers=admin["headers"])
    assert revoked.status_code == 200
    assert client.post(API + "/auth/challenge", json=challenge).status_code == 401


def test_stale_session_cannot_administer_or_enroll(client):
    admin = enroll(client, "admin", "admin")
    with client.app.state.db.transaction() as connection:
        connection.execute("UPDATE sessions SET authenticated_at=?", (time.time() - 400,))
    stale = client.get(API + "/admin/users", headers=admin["headers"])
    assert stale.status_code == 401 and stale.json()["code"] == "reauthenticate"
    created = client.post(
        API + "/admin/users", headers=admin["headers"],
        json={"username": "late", "password": "late-password"},
    )
    assert created.status_code == 401
    device = {"device_id": str(uuid.uuid4()), "public_key": KEY}
    assert client.post(API + "/me/devices", headers=admin["headers"], json=device).status_code == 401
    # Ordinary access continues until the token itself expires.
    assert client.get(API + "/me", headers=admin["headers"]).status_code == 200


def sign(key, challenge, device):
    message = signing_message(challenge["server_id"], device, challenge["nonce"])
    return base64.b64encode(key.sign(message)).decode()


def test_challenge_cannot_be_replayed_forged_or_moved_to_another_device(client, alice):
    bob = enroll(client, "bob")
    device = alice["device_id"]
    request = lambda: client.post(API + "/auth/challenge", json={"device_id": device}).json()  # noqa: E731
    token = lambda who, nonce, signature: client.post(  # noqa: E731
        API + "/auth/token", json={"device_id": who, "nonce": nonce, "signature": signature}
    ).status_code

    challenge = request()
    good = sign(alice["key"], challenge, device)
    assert token(device, challenge["nonce"], good) == 200
    assert token(device, challenge["nonce"], good) == 401

    challenge = request()
    forged = sign(Ed25519PrivateKey.generate(), challenge, device)
    assert token(device, challenge["nonce"], forged) == 401
    # The failed attempt consumed the nonce, so the real key cannot use it either.
    assert token(device, challenge["nonce"], sign(alice["key"], challenge, device)) == 401

    challenge = request()
    other = bob["device_id"]
    assert token(other, challenge["nonce"], sign(bob["key"], challenge, other)) == 401
    assert token(device, challenge["nonce"], "not base64 !!") == 401
    assert client.post(API + "/auth/challenge", json={"device_id": str(uuid.uuid4())}).status_code == 401


def test_enrolled_device_key_cannot_be_replaced(client, alice):
    session = client.post(
        API + "/auth/login", json={"username": "alice", "password": "example-password-123"}
    ).json()
    response = client.post(
        API + "/auth/devices", headers={"Authorization": "Bearer " + session["token"]},
        json={"device_id": alice["device_id"], "public_key": KEY},
    )
    assert response.status_code == 409
    assert client.get(API + "/me", headers=alice["headers"]).status_code == 200


def test_login_failure_reveals_nothing_and_echoes_nothing(client, alice):
    secret = "S3cretMarker-do-not-echo"
    wrong = client.post(API + "/auth/login", json={"username": "alice", "password": secret})
    unknown = client.post(API + "/auth/login", json={"username": "nobody-here", "password": secret})
    injected = client.post(
        API + "/auth/login", json={"username": "alice' OR '1'='1", "password": "' OR ''='"}
    )
    invalid = client.post(
        API + "/auth/login", json={"username": "alice", "password": secret, "extra": secret}
    )
    assert wrong.status_code == unknown.status_code == injected.status_code == 401
    assert wrong.json()["message"] == unknown.json()["message"] == injected.json()["message"]
    assert invalid.status_code == 400
    assert not any(secret in response.text for response in (wrong, unknown, invalid))


def test_password_guessing_is_capped_and_forwarded_headers_do_not_reset_it(client, alice):
    client.app.state.auth.attempts.clear()
    guess = lambda headers=None: client.post(  # noqa: E731
        API + "/auth/login", headers=headers, json={"username": "alice", "password": "a-wrong-guess"}
    ).status_code
    assert [guess() for _ in range(20)] == [401] * 20
    assert guess() == 429
    spoofed = {"X-Forwarded-For": "203.0.113.9", "X-Real-IP": "203.0.113.9", "Forwarded": "for=203.0.113.9"}
    assert guess(spoofed) == 429
    body = {"username": "alice", "password": "example-password-123"}
    assert client.post(API + "/auth/web-login", json=body).status_code == 429
    assert client.post(API + "/auth/challenge", json={"device_id": alice["device_id"]}).status_code == 429
    # The limit covers sign-in only; a signed-in user keeps working.
    assert client.get(API + "/jobs", headers=alice["headers"]).status_code == 200
