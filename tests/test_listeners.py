import socket
import threading
import time

import httpx
import pytest

from job_server.listeners import available_lan_addresses, listener_sockets
from job_server.settings import Settings


@pytest.mark.parametrize("address", [
    "0.0.0.0", "::", "127.0.0.1", "::1", "8.8.8.8", "169.254.1.2",
    "100.64.0.1", "host.local", "192.168.1.2:8766", "fd12::1%eth0", None,
])
def test_lan_listener_rejects_wildcard_public_special_and_dns_addresses(address):
    with pytest.raises(ValueError):
        Settings(allow_lan_http=True, lan_bind_address=address).validate()


def test_lan_listener_requires_explicit_http_permission_and_fixed_port():
    with pytest.raises(ValueError, match="Enable LAN HTTP"):
        Settings(lan_bind_address="192.168.1.2").validate()
    with pytest.raises(ValueError, match="requires a port"):
        Settings(allow_lan_http=True, lan_bind_address="192.168.1.2", port=0).validate()


def test_lan_origin_roundtrip_and_ipv6_format(tmp_path):
    settings = Settings(allow_lan_http=True, lan_bind_address="fd12:0000::2").validate()
    assert settings.lan_url == "http://[fd12::2]:8766"
    assert settings.lan_url in settings.connection_urls
    path = tmp_path / "settings.json"
    settings.save(path)
    assert Settings.load(path).lan_bind_address == "fd12::2"


def test_default_and_http_permission_alone_listen_only_on_loopback():
    for opt_in in (False, True):
        with listener_sockets(Settings(port=0, allow_lan_http=opt_in)) as streams:
            assert len(streams) == 1
            assert streams[0].getsockname()[0] == "127.0.0.1"
        assert streams[0].fileno() == -1


def test_bind_failure_closes_all_sockets_without_falling_back(monkeypatch):
    from job_server import listeners

    streams = []

    class Stream:
        def __enter__(self):
            streams.append(self)
            self.closed = False
            return self

        def __exit__(self, *args):
            self.closed = True

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            if address[0] != "127.0.0.1":
                raise OSError("fixture address is unavailable")

    monkeypatch.setattr(listeners.socket, "socket", lambda *args: Stream())
    with pytest.raises(RuntimeError, match="Select an address assigned to this PC"):
        with listener_sockets(Settings(allow_lan_http=True, lan_bind_address="192.168.1.2")):
            pytest.fail("A failed LAN bind must not start the server")
    assert len(streams) == 2 and all(stream.closed for stream in streams)


def test_real_daemon_accepts_lan_and_localhost_with_the_same_authentication(tmp_path, monkeypatch):
    from job_server import server_app

    addresses = [address for address in available_lan_addresses() if ":" not in address]
    if not addresses:
        pytest.skip("No private IPv4 interface is assigned to this test machine")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(
        data_dir=str(tmp_path), port=port, allow_privileged=True,
        allow_lan_http=True, lan_bind_address=addresses[0],
    ).validate()
    servers, failures = [], []
    real_app, real_server = server_app.create_app, server_app.uvicorn.Server

    def app_factory(settings):
        app = real_app(settings, dispatch=False)
        app.state.auth.create_initial_admin("fixture-admin", "synthetic-test-password")
        return app

    def server_factory(config):
        server = real_server(config)
        servers.append(server)
        return server

    monkeypatch.setattr(server_app, "create_app", app_factory)
    monkeypatch.setattr(server_app.uvicorn, "Server", server_factory)

    def run():
        try:
            server_app.serve(settings)
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not failures:
            if servers and servers[0].started:
                break
            time.sleep(0.02)
        assert not failures
        assert servers and servers[0].started
        with httpx.Client(trust_env=False, timeout=5) as client:
            local = f"http://127.0.0.1:{port}"
            assert client.get(local + "/api/v1/health").status_code == 200
            assert client.get(settings.lan_url + "/").status_code == 200
            assert client.get(settings.lan_url + "/api/v1/jobs").status_code == 401
            login = client.post(
                settings.lan_url + "/api/v1/auth/web-login",
                headers={"Origin": settings.lan_url},
                json={"username": "fixture-admin", "password": "synthetic-test-password"},
            )
            assert login.status_code == 200
            headers = {"Authorization": "Bearer " + login.json()["token"]}
            for base in (local, settings.lan_url):
                assert client.get(base + "/api/v1/me", headers=headers).status_code == 200
            assert client.get(
                settings.lan_url + "/api/v1/health", headers={"Origin": "http://unlisted.local"}
            ).status_code == 403
            from job_server.daemon_ownership import verify_local_daemon

            verify_local_daemon(settings.root, port)
    finally:
        for server in servers:
            server.should_exit = True
        thread.join(15)
        assert not thread.is_alive()
