import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .files import atomic_write


def pc_cpus():
    """(physical cores, threads) this server may use.

    Threads are the logical CPUs the OS schedules on; with hyper-threading there are
    more of them than cores. On Linux the process may be confined to fewer CPUs than
    the machine has (taskset, a container, a systemd unit), and those are what count.
    """
    import psutil

    threads = psutil.cpu_count() or 1
    try:
        threads = min(threads, len(os.sched_getaffinity(0))) or threads
    except (AttributeError, OSError):
        pass
    cores = psutil.cpu_count(logical=False) or threads
    return max(1, min(cores, threads)), threads


def pc_resources():
    """This PC's physical cores and installed memory in MB: a new server's budget.

    Cores rather than threads: calculations such as DFT rarely gain from the second
    thread of a hyper-threaded core, so a budget of threads would oversubscribe it.
    """
    import psutil

    return pc_cpus()[0], max(1, psutil.virtual_memory().total // (1024 * 1024))


def pc_size_label(memory_text):
    """The "Use this PC's size" button text, naming threads when they differ from cores."""
    cores, threads = pc_cpus()
    noun = "core" if cores == 1 else "cores"
    extra = f" of {threads} threads" if threads != cores else ""
    return f"Use this PC's size ({cores} {noun}{extra}, {memory_text} GiB)"


@dataclass
class Settings:
    data_dir: str = "~/.moleditpy/job_server"
    job_dir: str = ""
    finished_dir: str = ""
    #: Start each moved folder's name with its submission time, as 20261114-1738_<job name>.
    finished_date_prefix: bool = True
    db_backup: bool = True
    db_backup_hours: int = 24
    db_backup_keep: int = 7
    db_backup_dir: str = ""
    #: Months of activity log kept in server.db; older rows go to monthly archive files.
    event_keep_months: int = 3
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
    lan_extra_addresses: list[str] = field(default_factory=list)
    lan_hostname: str = ""
    lan_tls: bool = False
    tls_port: int = 8767
    python_executable: str = ""
    job_setup_commands: list[str] = field(default_factory=list)

    @property
    def connection_urls(self):
        return list(dict.fromkeys(
            url for url in [self.public_url, *self.public_urls, *self.lan_urls] if url
        ))

    @property
    def lan_url(self):
        return next(iter(self.lan_urls), "")

    @property
    def lan_addresses(self):
        """Every private IP this server listens on, the main one first."""
        if not self.lan_bind_address:
            return []
        return list(dict.fromkeys([self.lan_bind_address, *self.lan_extra_addresses]))

    @property
    def lan_urls(self):
        urls = []
        for address in self.lan_addresses:
            host = f"[{address}]" if ":" in address else address
            if self.lan_tls:
                urls.append(f"https://{host}:{self.tls_port}")
            if self.allow_lan_http:
                urls.append(f"http://{host}:{self.port}")
        if self.lan_hostname and self.lan_tls:
            urls.append(f"https://{self.lan_hostname}:{self.tls_port}")
        if self.lan_hostname and self.allow_lan_http:
            urls.append(f"http://{self.lan_hostname}:{self.port}")
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
    def job_root(self):
        """Where new jobs are placed; empty keeps them in the data directory."""
        return Path(self.job_dir).expanduser().resolve() if self.job_dir else self.root

    @property
    def finished_root(self):
        """Where ended jobs are moved to, as <user>/<job name>; None leaves them in place."""
        return Path(self.finished_dir).expanduser().resolve() if self.finished_dir else None

    @property
    def relocates(self):
        """1 when jobs ending now should be moved to the finished-jobs folder."""
        return int(bool(self.finished_dir))

    @property
    def backup_root(self):
        return (
            Path(self.db_backup_dir).expanduser().resolve()
            if self.db_backup_dir
            else self.root / "backups"
        )

    @property
    def event_archive_root(self):
        """Where activity log rows moved out of server.db are kept, one file per month."""
        return self.root / "event-archive"

    @property
    def execution_os(self):
        return (
            ("windows" if os.name == "nt" else "linux")
            if self.executor == "auto"
            else self.executor
        )

    def validate(self):
        for key in (
            "trusted_users_only", "allow_privileged", "web_ui", "allow_lan_http", "lan_tls",
            "db_backup", "finished_date_prefix",
        ):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        from .connection import is_mdns_name, validate_server_url
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
            if self.lan_tls and self.tls_port == self.port:
                raise ValueError("HTTP and HTTPS require different ports")

        if type(self.lan_extra_addresses) is not list:
            raise ValueError("lan_extra_addresses must be a list of private IP addresses")
        if self.lan_extra_addresses and not self.lan_bind_address:
            raise ValueError("Select a main LAN address before adding others")
        self.lan_extra_addresses = [
            address
            for address in dict.fromkeys(
                validate_lan_address(address) for address in self.lan_extra_addresses
            )
            if address != self.lan_bind_address
        ]
        if not isinstance(self.lan_hostname, str):
            raise ValueError("lan_hostname must be a .local name or empty")
        if self.lan_hostname:
            self.lan_hostname = self.lan_hostname.lower()
            if not is_mdns_name(self.lan_hostname):
                raise ValueError("The LAN name must be one label followed by .local")
            if not self.lan_bind_address:
                raise ValueError("Select a LAN address for the .local name to lead to")

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
            "db_backup_hours",
            "db_backup_keep",
            "event_keep_months",
        ):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("Invalid port")
        if self.execution_os not in {"linux", "windows", "wsl"}:
            raise ValueError("Invalid executor")
        self._validate_job_dir()
        self._validate_finished_dir()
        self._validate_backup_dir()
        self._validate_job_setup()
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

    def _validate_job_setup(self):
        commands = self.job_setup_commands
        if type(commands) is not list or any(type(command) is not str for command in commands):
            raise ValueError("Job setup commands must be a list of lines")
        if any("\n" in command or "\r" in command or "\0" in command for command in commands):
            raise ValueError("Each job setup command must be one line")
        if sum(len(command) for command in commands) > 65536:
            raise ValueError("Job setup commands are too long")

    def _validate_job_dir(self):
        if not isinstance(self.job_dir, str):
            raise ValueError("Job working directory must be a path or empty")
        if not self.job_dir:
            return
        if any(ord(c) < 32 for c in self.job_dir):
            raise ValueError("Job working directory contains invalid characters")
        path = Path(self.job_dir).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Job working directory must be an absolute path")
        if self.execution_os == "wsl":
            raise ValueError("WSL jobs use wsl_root; leave the job working directory empty")
        from .files import is_link

        if is_link(path) or (path.exists() and not path.is_dir()):
            raise ValueError("Job working directory must be a real directory, not a link or file")
        if self.job_root.is_relative_to(self.root / "tls"):
            raise ValueError("Job working directory must not be inside the TLS folder")

    @staticmethod
    def _check_folder(value, label):
        """A real absolute folder, or empty. Returns the resolved path or None."""
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a path or empty")
        if not value:
            return None
        if any(ord(c) < 32 for c in value):
            raise ValueError(f"{label} contains invalid characters")
        path = Path(value).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{label} must be an absolute path")
        from .files import is_link

        if is_link(path) or (path.exists() and not path.is_dir()):
            raise ValueError(f"{label} must be a real directory, not a link or file")
        return path.resolve()

    def _server_folders(self):
        """Folders the server itself writes to, which no other setting may point into."""
        folders = [self.root / name for name in ("jobs", "staging", "control", "logs", "tls", "event-archive")]
        folders += [self.job_root / "jobs", self.job_root / "staging"]
        return folders

    def _validate_finished_dir(self):
        path = self._check_folder(self.finished_dir, "Finished jobs folder")
        if path is None:
            return
        if path in {self.root, self.job_root} or any(
            path.is_relative_to(folder) for folder in [*self._server_folders(), self.backup_root]
        ):
            raise ValueError(
                "Finished jobs folder must be separate from the data, job and backup folders"
            )
        if self.root.is_relative_to(path) or self.job_root.is_relative_to(path):
            # A user named like the data folder would land inside it.
            raise ValueError("Finished jobs folder must not contain the data or job folder")

    def _validate_backup_dir(self):
        path = self._check_folder(self.db_backup_dir, "Database backup folder")
        if path is None:
            return
        if path in {self.root, self.job_root} or any(
            path.is_relative_to(folder) for folder in self._server_folders()
        ):
            raise ValueError("Database backup folder must not be a server or job folder")
        if self.finished_root and path.is_relative_to(self.finished_root):
            raise ValueError("Database backup folder must not be inside the finished jobs folder")

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
