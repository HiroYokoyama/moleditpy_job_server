import pytest

from job_server.client import JobServerClient
from job_server.connection import validate_server_url
from job_server.settings import Settings


@pytest.mark.parametrize("host", ["10.0.0.2", "172.16.1.2", "192.168.1.2", "[fd12::2]"])
def test_lan_http_requires_explicit_opt_in(host, monkeypatch):
    from urllib.request import ProxyHandler

    url = f"http://{host}:8080"
    with pytest.raises(ValueError):
        JobServerClient(url)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.org:8080")
    client = JobServerClient(url, allow_lan_http=True)
    assert client.url == url
    assert not any(isinstance(h, ProxyHandler) and h.proxies for h in client.opener.handlers)


@pytest.mark.parametrize("host", [
    "8.8.8.8", "0.0.0.0", "169.254.169.254", "100.64.0.1", "192.0.2.1",
    "172.32.0.1", "224.0.0.1", "255.255.255.255", "[::]", "[fe80::1]",
    "[::ffff:192.168.1.2]", "[fd12::2%25eth0]", "localhost.evil.org",
    "192.168.1.2.evil.org", "2130706433", "0x7f000001",
])
def test_http_exception_never_accepts_public_special_or_dns_addresses(host):
    with pytest.raises(ValueError):
        validate_server_url(f"http://{host}:8080", True)


@pytest.mark.parametrize("suffix", [
    "/path", "?token=secret", "#fragment", ":0", ":65536", ":abc", "\n", "\\evil",
])
def test_invalid_origins_are_rejected_before_network_access(suffix):
    with pytest.raises(ValueError):
        JobServerClient("http://192.168.1.2" + suffix, allow_lan_http=True)


def test_multiple_origins_normalize_deduplicate_and_roundtrip(tmp_path):
    settings = Settings(
        public_url="https://Compute.example.org/",
        public_urls=["http://192.168.1.2:8080/", "https://Compute.example.org/"],
        allow_lan_http=True,
    ).validate()
    assert settings.connection_urls == ["https://compute.example.org", "http://192.168.1.2:8080"]
    path = tmp_path / "settings.json"
    settings.save(path)
    assert Settings.load(path).connection_urls == settings.connection_urls
    settings.allow_lan_http = False
    with pytest.raises(ValueError):
        settings.validate()


@pytest.mark.parametrize("value", ["false", 1, None])
def test_lan_http_flags_require_json_booleans(value):
    with pytest.raises(ValueError):
        Settings(allow_lan_http=value).validate()
    with pytest.raises(ValueError):
        JobServerClient("http://192.168.1.2", allow_lan_http=value)


@pytest.mark.parametrize("value", ["https://example.org", [None], [1]])
def test_additional_origins_require_a_list_of_strings(value):
    with pytest.raises(ValueError):
        Settings(public_urls=value).validate()


def test_lan_http_cannot_follow_redirects():
    from job_server.client import ClientError, NoRedirect

    with pytest.raises(ClientError, match="redirects are forbidden"):
        NoRedirect().redirect_request(None, None, 302, "", {}, "http://8.8.8.8")


def test_local_name_over_http_needs_the_lan_http_opt_in():
    assert validate_server_url("http://Your-PC.local:8766", True) == "http://your-pc.local:8766"
    with pytest.raises(ValueError):
        validate_server_url("http://your-pc.local:8766")
    for url in ("http://a.b.local", "http://example.com", "http://local"):
        with pytest.raises(ValueError):
            validate_server_url(url, True)
