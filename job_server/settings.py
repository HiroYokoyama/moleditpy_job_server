import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


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
    python_executable: str = ""

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
        if self.public_url:
            from urllib.parse import urlsplit

            parts = urlsplit(self.public_url)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
            ):
                raise ValueError(
                    "public_url must be an HTTPS origin, for example https://compute.example.org"
                )
            self.public_url = self.public_url.rstrip("/")
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
                "This preview supports trusted users only; OS isolation is not implemented"
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
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), "utf-8")
        temporary.replace(path)
