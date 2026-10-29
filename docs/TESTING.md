# Workflow coverage

Normal push/PR CI runs the server suite with four workers on Linux and Windows
Python 3.11/3.12/3.13, security checks, Qt callback checks, the pinned companion
regression suite, and compatibility with a cloned Job Manager checkout.

**Installed Job Manager integration** is **workflow_dispatch only**. It installs
the latest stable Job Manager from PyPI and exercises its standalone window and local CLI
against a built server wheel (blank server-version) or a selected PyPI release.
The six environments are Ubuntu Python 3.11–3.14 and Windows/macOS Python 3.12.
Run this workflow before and after a release; it is not an extra push/PR gate.

| User workflow | Automated coverage |
|---|---|
| Install and explicitly start the daemon; serve browser UI | Installed wheel smoke: console entry point, health/version and HTML |
| Initial administrator and device enrollment | Installed wheel smoke, bootstrap/auth/concurrency tests |
| Submit inputs, monitor and read logs | Installed GUI/CLI smoke and real manager backend HTTP tests |
| Automatic download, explicit download and file preview | Installed GUI/CLI smoke verifies downloaded bytes and preview |
| Native failure exit code | Installed GUI/CLI smoke verifies exit 7 reaches the manager |
| Running cancellation | Installed GUI/CLI smoke verifies both manager and daemon reach CANCELLED |
| Queued cancellation and queue pause/resume | Installed GUI/CLI smoke |
| Daemon restart with an active job | Installed GUI/CLI smoke verifies reconnect, completion and exactly one dispatch |
| Quoted input paths and idempotent submission retry | Real manager backend HTTP tests |
| Dependency success, failure blocking and chain-any | Real manager backend HTTP tests |
| Authentication renewal, ownership and account/device revocation | Real manager HTTP, authentication and security tests |
| Resource reservations, concurrency, walltime and uncertain supervisor recovery | Execution and reconciliation tests |
| Disabled listener on plugin loading and optional-dependency GUI behavior | Plugin/GUI and manager optional-import checks |
| Build archives, version declarations and publication boundaries | Packaging/release tests and artifact verification |

The matrix uses synthetic loopback servers and temporary accounts/files. It does
not provision a real LAN HTTPS proxy or Tailscale tailnet, or a real WSL distro.
Those environments require separate deployment validation. Backend/API coverage
also does not mean every dialog click has an end-to-end UI test.

Local checks:

```sh
python -m pytest -q -n 4
python -m ruff check job_server scripts tests integration setup.py
```

The installed smoke scripts must be copied outside the checkout and run with the
interpreter that has the distributions installed:

```sh
python smoke_test_installed.py --version 0.3.2 --manager-version 3.4.1
```
