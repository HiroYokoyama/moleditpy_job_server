"""High-level backend contract for Job Manager; no remote shell transport."""

import fnmatch
import uuid
from pathlib import Path

from .client import JobServerClient, job_path
from .files import validate_path


class JobServerBackend:
    def __init__(self, host):
        self.host = host
        self.client = JobServerClient(
            host.server_url,
            host.server_key_path,
            host.server_ca_file or None,
            host.server_id or None,
            reuse_auth=True,
        )

    def submit(self, preset, job, local_files, after_id=None):
        if (
            preset.modules
            or preset.extra_directives
            or preset.submit_options
            or preset.nodes != 1
            or preset.ntasks != 1
        ):
            raise ValueError(
                "Job Server does not accept HPC modules, directives, submit options, nodes or tasks"
            )
        if job.remote_dir_provided or job.remote_input or job.start_after or job.force_run:
            raise ValueError(
                "Job Server does not accept existing work directories, start times or force-run"
            )
        from .core import execution_core

        execution_core()
        from ._execution.schedulers.base import parse_memory_mb

        parts = preset.walltime.split(":")
        if len(parts) != 3 or not all(part.isdecimal() for part in parts):
            raise ValueError("Use HH:MM:SS walltime")
        hours, minutes, seconds = map(int, parts)
        if minutes >= 60 or seconds >= 60:
            raise ValueError("Use HH:MM:SS walltime with minutes and seconds below 60")
        memory = parse_memory_mb(preset.memory) if preset.memory else 512
        if memory <= 0:
            raise ValueError("Invalid memory request")
        names = [Path(path).name for path in local_files]
        if len(set(name.casefold() for name in names)) != len(names):
            raise ValueError("Input files must have distinct basenames")
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, job.id))
        spec = {
            "name": job.name,
            "command": "\n".join([*preset.pre_commands, preset.command_template]),
            "resources": {
                "cpus_per_task": preset.cpus_per_task,
                "memory_mb": memory,
                "walltime_seconds": hours * 3600 + minutes * 60 + seconds,
            },
            "fetch_globs": preset.fetch_globs,
            "auto_download": job.auto_download,
            "after_job": after_id,
            "chain_any": job.chain_any,
        }
        result = self.client.submit(spec, dict(zip(names, local_files)), key)
        job.server_job_id = result["id"]
        job.server_id = self.client.server_id
        job.owner_user_id = result["owner_id"]
        job.execution_os = self.client.request("GET", "/capabilities")["execution_os"]
        job.last_server_state = result["state"]
        job.remote_job_id = result["id"]
        job.remote_dir = result["id"]
        job.command = spec["command"]
        job.log_file = "job.log"
        import time

        job.submitted_at = time.time()
        job.touch(result["state"])
        return job

    def poll(self, jobs):
        result = {}
        for job in jobs:
            remote = self.client.request("GET", job_path(job.remote_job_id))
            job.rc = remote["exit_code"]
            job.last_server_state = remote["state"]
            result[job.id] = remote["state"]
        return result

    def cancel(self, job):
        return self.client.request("POST", job_path(job.remote_job_id) + "/cancel")

    def tail(self, job, lines=100):
        route = job_path(job.remote_job_id)
        info = self.client.request("GET", route + "/log?limit=1")
        offset = max(0, info.get("size", 0) - 1048576)
        result = self.client.request("GET", route + f"/log?offset={offset}&limit=1048576")
        return "\n".join(result["text"].splitlines()[-lines:])

    def files(self, job):
        return self.client.request("GET", job_path(job.remote_job_id) + "/files")

    def fetch_file(self, job, relative, target):
        return self.client.download(job.remote_job_id, relative, target)

    def fetch(self, job, directory, patterns):
        directory = Path(directory)
        fetched = []
        for entry in self.files(job):
            relative = validate_path(entry["path"])
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
                target = directory / relative
                if not target.resolve().is_relative_to(directory.resolve()):
                    raise ValueError("Result escapes download directory")
                self.fetch_file(job, relative, target)
                fetched.append(str(target))
        return fetched
