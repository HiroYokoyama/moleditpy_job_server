"""Build a companion Job Manager with the high-level server backend.

The upstream submodule and sibling working checkout are never modified.
Every replacement is anchored to the audited revision and fails on drift.
"""

import difflib
import sys
import tempfile
from pathlib import Path
from shutil import copytree
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def replace(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError(f"Upstream source drift at {before[:80]!r}")
    return text.replace(before, after)


def patch_models(text):
    text = replace(
        text,
        'BACKEND_OPENSSH = "openssh"',
        'BACKEND_JOB_SERVER = "job_server"\nBACKEND_OPENSSH = "openssh"',
    )
    text = replace(text, "class HostProfile:\n", "class HostProfile:\n")  # revision guard
    text = replace(
        text,
        "    backend: str = BACKEND_OPENSSH",
        '    server_url: str = ""\n    server_id: str = ""\n    server_key_path: str = ""\n    server_ca_file: str = ""\n    backend: str = BACKEND_OPENSSH',
    )
    text = replace(
        text,
        "    def uses_remote_runner(self) -> bool:",
        "    def is_job_server(self) -> bool:\n        return self.backend == BACKEND_JOB_SERVER\n\n    @property\n    def uses_remote_runner(self) -> bool:",
    )
    text = replace(
        text,
        "        return self.concurrency_mode == MODE_RUNNER and self.scheduler in (",
        "        return not self.is_job_server and self.concurrency_mode == MODE_RUNNER and self.scheduler in (",
    )
    text = replace(
        text,
        "    def target(self) -> str:\n",
        "    def target(self) -> str:\n        if self.is_job_server:\n            return self.server_url\n",
    )
    text = replace(text, "class Job:\n", "class Job:\n")
    text = replace(
        text,
        '    remote_job_id: str = ""',
        '    server_job_id: str = ""\n    server_id: str = ""\n    owner_user_id: str = ""\n    execution_os: str = ""\n    last_server_state: str = ""\n    remote_job_id: str = ""',
    )
    return text


def patch_submissions(text):
    text = replace(
        text,
        "        def work() -> Job:\n",
        """        def work() -> Job:
            if host.is_job_server:
                from .server_backend import backend_for
                if relay_source_dir or relay_filenames:
                    raise ValueError("Job Server file relay is not supported")
                after_id = self.service._chain_pid(after_job) if after_job else None
                if after_job and (after_job.host_id != host.id or not after_id):
                    raise ValueError("Job Server predecessor has not been submitted on this host")
                return backend_for(host).submit(preset,job,to_upload,after_id)
""",
    )
    text = replace(
        text,
        "            stored.remote_dir = job.remote_dir",
        """            for field in ("server_job_id", "server_id", "owner_user_id", "execution_os", "last_server_state"):
                setattr(stored,field,getattr(job,field))
            stored.remote_dir = job.remote_dir""",
    )
    return text


def patch_poller(text):
    text = replace(
        text,
        "            transport = self.transport_factory(self.host)",
        """            if self.host.is_job_server:
                from .server_backend import backend_for
                updates = backend_for(self.host).poll(self.jobs)
            else:
                transport = self.transport_factory(self.host)""",
    )
    text = replace(
        text,
        "            if self.host.uses_remote_runner:\n",
        "            if self.host.is_job_server:\n                pass\n            elif self.host.uses_remote_runner:\n",
    )
    return text


def patch_service(text):
    text = replace(
        text,
        "    def transport_for(self, host: HostProfile):\n",
        """    def transport_for(self, host: HostProfile):
        if host.is_job_server:
            raise ValueError("Job Server uses the high-level backend, not a shell transport")
""",
    )
    text = replace(
        text,
        "        def work() -> List[str]:\n            transport = self.transport_for(host)\n            try:\n                # As deep",
        """        def work() -> List[str]:
            if host.is_job_server:
                from .server_backend import backend_for
                return [entry["path"] for entry in backend_for(host).files(job)]
            transport = self.transport_for(host)
            try:
                # As deep""",
    )
    text = replace(
        text,
        "        def work() -> List[str]:\n            transport = self.transport_for(host)\n            try:\n                # An explicit",
        """        def work() -> List[str]:
            if host.is_job_server:
                from .server_backend import backend_for
                patterns = [glob.escape(name) for name in names] if names else job.fetch_globs
                return backend_for(host).fetch(job,local_dir,patterns)
            transport = self.transport_for(host)
            try:
                # An explicit""",
    )
    text = replace(
        text,
        "            transport = self.transport_for(host)\n            try:\n                paths = fetch_results(transport, job, cache_dir",
        """            if host.is_job_server:
                from .server_backend import backend_for
                target = os.path.join(cache_dir,*safe_name.split("/"))
                return str(backend_for(host).fetch_file(job,safe_name,target))
            transport = self.transport_for(host)
            try:
                paths = fetch_results(transport, job, cache_dir""",
    )
    text = replace(
        text,
        "        dependents = self.store.dependents_of(job.id) if release_dependents else []",
        "        dependents = self.store.dependents_of(job.id) if release_dependents and not host.is_job_server else []",
    )
    text = replace(
        text,
        "        def work() -> None:\n            transport = self.transport_for(host)\n            try:\n                if host.uses_remote_runner:",
        """        def work() -> None:
            if host.is_job_server:
                from .server_backend import backend_for
                return backend_for(host).cancel(job)
            transport = self.transport_for(host)
            try:
                if host.uses_remote_runner:""",
    )
    text = replace(
        text,
        "        def done(_result) -> None:\n            job.touch(STATE_CANCELLED)",
        """        def done(_result) -> None:
            if host.is_job_server:
                job.touch(_result["state"])
                self.store.save_jobs()
                self.job_updated.emit(job.id)
                self.poller.prime(host.id)
                return
            job.touch(STATE_CANCELLED)""",
    )
    text = replace(
        text,
        "        def work() -> str:\n            transport = self.transport_for(host)\n            try:\n                return tail_log(transport, job, lines)",
        """        def work() -> str:
            if host.is_job_server:
                from .server_backend import backend_for
                return backend_for(host).tail(job,lines)
            transport = self.transport_for(host)
            try:
                return tail_log(transport, job, lines)""",
    )
    text = replace(
        text,
        "        def work() -> str:\n            transport = self.transport_for(host)\n            try:\n                return tail_remote_file(transport, job, filename, lines)",
        """        def work() -> str:
            if host.is_job_server:
                from .server_backend import backend_for
                target = os.path.join(self.store.cache_dir(job.id,create=True),*filename.split("/"))
                from job_server.files import validate_path
                validate_path(filename)
                backend_for(host).fetch_file(job,filename,target)
                with open(target,encoding="utf-8",errors="replace") as stream:
                    return "\\n".join(stream.read().splitlines()[-lines:])
            transport = self.transport_for(host)
            try:
                return tail_remote_file(transport, job, filename, lines)""",
    )
    return text


def patch_form(text):
    text = replace(
        text,
        '        self.cmb_backend.addItem("This machine (no SSH)", BACKEND_LOCAL)',
        '        self.cmb_backend.addItem("Job Server (HTTPS)", "job_server")\n        self.cmb_backend.addItem("This machine (no SSH)", BACKEND_LOCAL)',
    )
    text = replace(
        text,
        '        form.addRow("Backend", self.cmb_backend)',
        """        form.addRow("Backend", self.cmb_backend)
        self.txt_server_url = QLineEdit()
        self.txt_server_url.setPlaceholderText("https://compute.example.org")
        self.txt_server_key = QLineEdit()
        self.txt_server_key.setPlaceholderText("Credential file path; private key is stored separately")
        self.txt_server_ca = QLineEdit()
        self.txt_server_ca.setPlaceholderText("Optional CA certificate file")
        form.addRow("Server URL",self.txt_server_url)
        form.addRow("Device credential path",self.txt_server_key)
        form.addRow("CA certificate path",self.txt_server_ca)
        sign_in = QPushButton("Sign in / Register server device...")
        sign_in.clicked.connect(self._server_sign_in)
        form.addRow("",sign_in)""",
    )
    text = replace(
        text,
        "    def _collect(self, host: HostProfile) -> HostProfile:\n",
        """    def _server_sign_in(self):
        from .server_backend import show_enrollment
        show_enrollment(self,self.txt_server_url.text().strip(),self.txt_server_key,self.txt_server_ca.text().strip())

    def _collect(self, host: HostProfile) -> HostProfile:
        host.server_url = self.txt_server_url.text().strip()
        host.server_key_path = self.txt_server_key.text().strip()
        host.server_ca_file = self.txt_server_ca.text().strip()
""",
    )
    text = replace(
        text,
        "        host.backend = self.cmb_backend.currentData()",
        """        host.backend = self.cmb_backend.currentData()
        if host.is_job_server:
            host.hostname = host.server_url
            host.load_profile = False
            host.login_commands = []
            host.monitor_usage = False""",
    )
    return text


def patch_hosts(text):
    text = replace(
        text,
        "        def work() -> str:\n            transport = self.service.transport_for(host)\n            try:\n                return transport.test_connection()",
        """        def work() -> str:
            if host.is_job_server:
                from .server_backend import backend_for
                capabilities = backend_for(host).client.request("GET", "/capabilities")
                return "Job Server (" + capabilities["execution_os"] + ")"
            transport = self.service.transport_for(host)
            try:
                return transport.test_connection()""",
    )
    text = replace(
        text,
        '        self.txt_hostname.setText("")',
        """        self.txt_hostname.setText("")
        self.profile_form.txt_server_url.clear()
        self.profile_form.txt_server_key.clear()
        self.profile_form.txt_server_ca.clear()""",
    )
    text = replace(
        text,
        "        self.txt_hostname.setText(host.hostname)",
        """        self.txt_hostname.setText(host.hostname)
        self.profile_form.txt_server_url.setText(host.server_url)
        self.profile_form.txt_server_key.setText(host.server_key_path)
        self.profile_form.txt_server_ca.setText(host.server_ca_file)""",
    )
    text = replace(
        text,
        "        backend = self.cmb_backend.currentData()\n        local =",
        """        backend = self.cmb_backend.currentData()
        if backend == "job_server":
            self.lbl_backend_hint.setText("Authenticated Job Server. Resources, dependencies and cancellation are managed by the server. Choose the scheduler matching its execution OS.")
            self._set_ssh_fields_enabled(False)
            self.txt_remote_root.setEnabled(False)
            self.txt_equal_path.setEnabled(False)
            self.chk_monitor_usage.setChecked(False)
            self.chk_load_profile.setChecked(False)
            self.chk_ask_password.setChecked(False)
            return
        self.txt_remote_root.setEnabled(True)
        local =""",
    )
    return text


PATCHERS = {
    "models.py": patch_models,
    "submission_workflow.py": patch_submissions,
    "poller.py": patch_poller,
    "service.py": patch_service,
    "host_profile_form.py": patch_form,
    "hosts_dialog.py": patch_hosts,
}


def build(destination):
    source = ROOT / "upstream" / "job_manager" / "job_manager"
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Build destination must not exist")
    copytree(
        source, destination, ignore=lambda _p, n: [name for name in n if name == "__pycache__"]
    )
    # Current upstream includes the audited server integration itself.
    if (source / "server_backend.py").exists():
        models = (source / "models.py").read_text("utf-8")
        if 'BACKEND_JOB_SERVER = "job_server"' not in models:
            raise RuntimeError("Upstream server integration is incomplete")
        return ""
    diffs = []
    for name, patcher in PATCHERS.items():
        original = (source / name).read_text("utf-8")
        patched = patcher(original)
        (destination / name).write_text(patched, "utf-8")
        diffs.extend(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                patched.splitlines(keepends=True),
                fromfile="a/job_manager/" + name,
                tofile="b/job_manager/" + name,
            )
        )
    (destination / "server_backend.py").write_text(
        (ROOT / "integration" / "server_backend.py").read_text("utf-8"), "utf-8"
    )
    return "".join(diffs)


def main():
    output = ROOT / "dist" / "job_manager_job_server.zip"
    output.parent.mkdir(exist_ok=True)
    (ROOT / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="companion-", dir=ROOT / "build") as temporary:
        target = Path(temporary) / "job_manager"
        patch = build(target)
        (ROOT / "integration" / "job_manager.patch").write_text(patch, "utf-8")
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*.py")):
                archive.write(path, "job_manager/" + path.relative_to(target).as_posix())
            archive.write(ROOT / "upstream" / "job_manager" / "LICENSE", "job_manager/LICENSE")
    print(output)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        build(sys.argv[1])
    else:
        main()
