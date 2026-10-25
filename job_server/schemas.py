from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from .files import validate_path


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Resources(Model):
    cpus_per_task: StrictInt = Field(default=1, ge=1)
    memory_mb: StrictInt = Field(default=512, ge=1)
    walltime_seconds: StrictInt = Field(default=3600, ge=1, le=30 * 86400)


class FileSpec(Model):
    path: StrictStr
    size: StrictInt = Field(ge=0)
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def path_safe(cls, value):
        return validate_path(value)


class JobSpec(Model):
    name: StrictStr = Field(default="job", min_length=1, max_length=128)
    executor: Literal["auto", "linux", "windows", "wsl"] = "auto"
    command: StrictStr = Field(min_length=1, max_length=65536)
    shell: Literal["native"] = "native"
    resources: Resources = Field(default_factory=Resources)
    files: list[FileSpec] = Field(default_factory=list, max_length=10000)
    fetch_globs: list[StrictStr] = Field(default_factory=lambda: ["*"], max_length=100)
    after_job: StrictStr | None = None
    chain_any: StrictBool = False
    auto_download: StrictBool = True

    @field_validator("files")
    @classmethod
    def distinct_paths(cls, files):
        paths = sorted(file.path.casefold() for file in files)
        known = set()
        for path in paths:
            parts = path.split("/")
            if path in known or any(
                "/".join(parts[:index]) in known for index in range(1, len(parts))
            ):
                raise ValueError("Manifest has duplicate or overlapping paths")
            known.add(path)
        return files

    @field_validator("command", "name")
    @classmethod
    def no_nul(cls, text):
        if "\0" in text:
            raise ValueError("NUL is forbidden")
        return text

    @field_validator("command")
    @classmethod
    def command_required(cls, text):
        if not text.strip():
            raise ValueError("A nonblank command is required")
        return text


class Login(Model):
    username: StrictStr = Field(max_length=64)
    password: StrictStr = Field(max_length=1024)


class Enrollment(Model):
    device_id: StrictStr = Field(max_length=64)
    public_key: StrictStr = Field(max_length=128)


class Challenge(Model):
    device_id: StrictStr = Field(max_length=64)


class Token(Challenge):
    nonce: StrictStr = Field(max_length=128)
    signature: StrictStr = Field(max_length=128)


class UserCreate(Login):
    role: Literal["user", "admin"] = "user"


class Pause(Model):
    paused: StrictBool = True
