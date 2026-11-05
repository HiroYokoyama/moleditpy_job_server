import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .files import atomic_write


@dataclass
class Settings:
    data_dir: str = "~/.moleditpy/job_server"
    port: int = 8766
    cpus: int = 2
    memory_mb: int = 4096
    max_active_per_user: int = 2
    max_queued_per_user: int = 100
    storage_quota_mb: int = 1024
    max_chunk_bytes: int = 8 * 1024 * 1024
    max_json_bytes: int = 1024 * 1024
    max_connections: int = 64
    token_seconds: int = 900
    challenge_seconds: int = 60
    executor: str = "auto"
    wsl_distribution: str = ""
    wsl_root: str = "/home/jobserver/.moleditpy/job_server"
    trusted_users_only: bool = True
    allow_privileged: bool = False
    web_ui: bool = True
    public_url: str = ""
    public_urls: list[str] = field(default_factory=list)
    allow_lan_http: bool = False
    lan_bind_address: str = ""
    lan_tls: bool = False
    tls_port: int = 8767
    python_executable: str = ""

    @property
    def connection_urls(self):
        return list(dict.fromkeys(
            url for url in [self.public_url, *self.public_urls, *self.lan_urls] if url
        ))

    @property
    def lan_url(self):
        return next(iter(self.lan_urls), "")

    @property
    def lan_urls(self):
        if not self.lan_bind_address:
            return []
        host = self.lan_bind_address
        if ":" in host:
            host = f"[{host}]"
        urls = []
        if self.lan_tls:
            urls.append(f"https://{host}:{self.tls_port}")
        if self.allow_lan_http:
            urls.append(f"http://{host}:{self.port}")
        return urls

    @property
    def tls_url(self):
        if not self.lan_tls or not self.lan_bind_address:
            return ""
        host = self.lan_bind_address
        if ":" in host:
            host = f"[{host}]"
        return f"https://{host}:{self.tls_port}"

    @property
    def root(self):
        return Path(self.data_dir).expanduser().resolve()

    @property
    def execution_os(self):
        return (
            ("windows" if os.name == "nt" else "linux")
            if self.executor == "auto"
            else self.executor
        )

    def validate(self):
        for key in ("trusted_users_only", "allow_privileged", "web_ui", "allow_lan_http", "lan_tls"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        from .connection import validate_server_url
        from .listeners import validate_lan_address

        if not isinstance(self.lan_bind_address, str):
            raise ValueError("lan_bind_address must be a private IP address or empty")
        if type(self.tls_port) is not int or not 1 <= self.tls_port <= 65535:
            raise ValueError("Invalid HTTPS port")
        if self.lan_tls and not self.lan_bind_address:
            raise ValueError("Select a private LAN address for local HTTPS")
        if self.lan_bind_address:
            if not (self.allow_lan_http or self.lan_tls):
                raise ValueError("Enable LAN HTTP or local HTTPS before selecting a LAN listen address")
            if type(self.port) is not int or not 1 <= self.port <= 65535:
                raise ValueError("A LAN listener requires a port between 1 and 65535")
            self.lan_bind_address = validate_lan_address(self.lan_bind_address)
            if self.lan_tls and self.allow_lan_http and self.tls_port == self.port:
                raise ValueError("LAN HTTP and HTTPS require different ports")

        if type(self.public_urls) is not list or any(type(url) is not str for url in self.public_urls):
            raise ValueError("public_urls must be a list of origin strings")
        if self.public_url:
            self.public_url = validate_server_url(self.public_url, self.allow_lan_http)
        self.public_urls = list(dict.fromkeys(
            validate_server_url(url, self.allow_lan_http) for url in self.public_urls
        ))
        for key in (
            "cpus",
            "memory_mb",
            "max_active_per_user",
            "max_queued_per_user",
            "storage_quota_mb",
            "max_chunk_bytes",
            "max_json_bytes",
            "max_connections",
            "token_seconds",
            "challenge_seconds",
        ):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("Invalid port")
        if self.execution_os not in {"linux", "windows", "wsl"}:
            raise ValueError("Invalid executor")
        if not self.trusted_users_only:
            raise ValueError(
                "This server supports trusted users only; OS isolation is not implemented"
            )
        if self.execution_os == "wsl":
            if (
                os.name != "nt"
                or not self.wsl_distribution
                or not self.wsl_root.startswith("/home/")
            ):
                raise ValueError(
                    "WSL requires Windows, an approved distribution, and a /home/ work root"
                )
            if ".." in self.wsl_root.split("/") or any(
                c in self.wsl_distribution for c in "\r\n\0"
            ):
                raise ValueError("Invalid WSL settings")
        return self

    @classmethod
    def load(cls, path=None):
        if path is None:
            default = cls().root / "settings.json"
            if default.exists():
                path = default
        return (
            cls(**json.loads(Path(path).read_text("utf-8"))).validate()
            if path
            else cls().validate()
        )

    def save(self, path):
        atomic_write(path, json.dumps(asdict(self), indent=2).encode("utf-8"))
