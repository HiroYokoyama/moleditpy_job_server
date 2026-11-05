"""Overlay the companion's pinned integration without editing the submodule."""

from pathlib import Path


def patch_lan_http(destination, replace):
    root = Path(__file__).resolve().parents[1]
    destination = Path(destination)

    def patch(name, before, after):
        path = destination / name
        path.write_text(replace(path.read_text("utf-8"), before, after), "utf-8")

    (destination / "job_server_client" / "connection.py").write_text(
        (root / "job_server" / "connection.py").read_text("utf-8"), "utf-8"
    )
    patch("models.py", '    server_ca_file: str = ""',
          '    server_ca_file: str = ""\n    server_allow_lan_http: bool = False')
    path = destination / "server_backend.py"
    text = path.read_text("utf-8")
    start = text.index("def validate_server_url(url):")
    end = text.index("def backend_for(host):", start)
    text = text[:start] + text[end:]
    text = replace(text, "from urllib.parse import urlsplit",
                   "from .job_server_client.connection import validate_server_url")
    path.write_text(text, "utf-8")
    patch("server_backend.py", "on_done=None):", "on_done=None, *, allow_lan_http=False):")
    patch("server_backend.py", "url = validate_server_url(url)",
          "url = validate_server_url(url, allow_lan_http)")
    patch("server_backend.py", "JobServerClient(url, path, ca_file or None)",
          "JobServerClient(url, path, ca_file or None, allow_lan_http=allow_lan_http)")
    patch("server_backend.py", "validate_server_url(host.server_url),",
          "validate_server_url(host.server_url, host.server_allow_lan_http),")
    patch("server_backend.py", "            host.server_id or None,",
          "            host.server_id or None,\n            allow_lan_http=host.server_allow_lan_http,")
    path = destination / "job_server_client" / "client.py"
    text = path.read_text("utf-8")
    start = text.index("        parts = urlsplit(url)")
    end = text.index("        self.key_path", start)
    text = text[:start] + (
        "        url = validate_server_url(url, allow_lan_http)\n"
        "        parts = urlsplit(url)\n        self.url = url\n"
    ) + text[end:]
    text = replace(text, "from .auth import signing_message",
                   "from .connection import validate_server_url\nfrom .auth import signing_message")
    text = replace(text, "server_id=None, timeout=30):",
                   "server_id=None, timeout=30, *, allow_lan_http=False):")
    text = replace(text, 'if parts.hostname in {"127.0.0.1", "localhost", "::1"}:',
                   'if parts.scheme == "http" or parts.hostname in {"127.0.0.1", "localhost", "::1"}:')
    path.write_text(text, "utf-8")
    patch("host_profile_form.py", '        self.txt_server_key = QLineEdit()',
          '        self.chk_server_lan_http = QCheckBox("Allow trusted LAN HTTP (unencrypted)")\n'
          '        self.chk_server_lan_http.setToolTip("Passwords, tokens and job data are readable on the network. Private IP addresses only.")\n'
          '        form.addRow("", self.chk_server_lan_http)\n        self.txt_server_key = QLineEdit()')
    patch("host_profile_form.py", "            self._server_enrolled,",
          "            self._server_enrolled,\n            allow_lan_http=self.chk_server_lan_http.isChecked(),")
    patch("host_profile_form.py", "            self.txt_server_url,",
          "            self.txt_server_url,\n            self.chk_server_lan_http,")
    patch("host_profile_form.py", "        host.server_url = self.txt_server_url.text().strip()",
          "        host.server_allow_lan_http = self.chk_server_lan_http.isChecked()\n"
          "        host.server_url = self.txt_server_url.text().strip()")
    patch("host_profile_form.py", "validate_server_url(host.server_url) if host.server_url",
          "validate_server_url(host.server_url, host.server_allow_lan_http) if host.server_url")
    patch("hosts_dialog.py", "        self.profile_form.txt_server_url.clear()",
          "        self.profile_form.txt_server_url.clear()\n"
          "        self.profile_form.chk_server_lan_http.setChecked(False)")
    patch("hosts_dialog.py", "        self.profile_form.txt_server_url.setText(host.server_url)",
          "        self.profile_form.txt_server_url.setText(host.server_url)\n"
          "        self.profile_form.chk_server_lan_http.setChecked(host.server_allow_lan_http)")
