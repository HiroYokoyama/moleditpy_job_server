# MoleditPy Job Server

This branch targets **MoleditPy 5.0.0a1–5.x** and uses **PySide6**. Plugin licensing is unchanged.

[![Tests](https://github.com/HiroYokoyama/moleditpy_job_server/actions/workflows/test.yml/badge.svg)](https://github.com/HiroYokoyama/moleditpy_job_server/actions/workflows/test.yml)
[![Security](https://github.com/HiroYokoyama/moleditpy_job_server/actions/workflows/security.yml/badge.svg)](https://github.com/HiroYokoyama/moleditpy_job_server/actions/workflows/security.yml)
[![Release](https://github.com/HiroYokoyama/moleditpy_job_server/actions/workflows/release.yml/badge.svg)](https://github.com/HiroYokoyama/moleditpy_job_server/actions/workflows/release.yml)
[![Version](https://img.shields.io/badge/version-0.2.0-blue)](pyproject.toml)
[![Downloads](https://img.shields.io/github/downloads/HiroYokoyama/moleditpy_job_server/total)](https://github.com/HiroYokoyama/moleditpy_job_server/releases)
[![License: GPL v3](https://img.shields.io/badge/License-LGPLv3-blue.svg)](LICENSE)
[![MoleditPy](https://img.shields.io/badge/MoleditPy-5.x-3577F7)](https://github.com/HiroYokoyama/python_molecular_editor)

A LAN computational helper for MoleditPy Job Manager, with an authenticated shared
job queue, headless daemon, MoleditPy administration plugin, and browser UI.
Version **0.2.0**. Access is for trusted users on a private network.

> [!WARNING]
> **Never expose Job Server to the public internet. Use localhost, a private LAN over HTTPS, or a private Tailscale tailnet only.**
> Do not use Tailscale Funnel, router port forwarding, a public reverse proxy, or an internet tunnel.
> Jobs execute arbitrary commands under the service OS account. Only admit trusted users,
> restrict tailnet access, and run under a low-privilege account. Authentication is not an OS sandbox.

![Job Server browser dashboard at desktop size (1440 × 1000)](docs/web-ui.jpg)

The daemon owns CPU/RAM reservations and a durable SQLite job ledger. Jobs run in
detached supervisors, so closing MoleditPy or stopping/restarting the daemon preserves
running calculations. Native Bash and PowerShell wrappers come from an immutable
Job Manager source dependency, rather than a second maintained copy.

## Use from MoleditPy (plugin workflow)

Install **`job_server_0.1.4.zip`** using MoleditPy's Plugin Installer. Then open
**Plugin → Job Server → Server Dashboard…**.
If required Python libraries are missing, opening a Job Server dialog shows their
names and installation guidance. Install them in MoleditPy's Python environment
and retry; no daemon is started by this check.
The ZIP is available from [GitHub Releases](https://github.com/HiroYokoyama/moleditpy_job_server/releases),
and the ZIPs are included in the [Tests workflow's build artifacts](https://github.com/HiroYokoyama/moleditpy_job_server/actions/workflows/test.yml).
Extract the artifact download and install the inner plugin ZIP, or build it from source below.

You can operate the server from MoleditPy dialogs; the command line is optional:

1. On the **server PC**, open **Users & Devices… → Create initial administrator**.
   This also works if the daemon is already running. An existing enabled
   administrator prevents a second bootstrap account.
2. Click **Start** in Server Dashboard.
3. Click **Sign in / register device** once for the administrator.
4. Use the dashboard to pause/resume the queue, view jobs, or cancel a selected job.
5. Open **Users & Devices…** to add users, disable accounts, or revoke device keys.
6. Open **Settings…** to configure resource budgets, limits, and the daemon runtime.
7. Click **Open Web UI** to open the browser interface when you want it.

Username and password are entered together in one dialog. Successful actions show
a **Success** dialog, and interactive errors remain visible until dismissed.
The administration window, Job Manager, and browser each register their own device:
signing in to one does not sign in the others. **Stop** and **Add user** require
signing in within the administration window first. If its credential file is
missing, the dialog explains how to register instead of reporting "File not found".

For a remote server, create the initial account on that server PC (for example,
through a remote desktop session), then register Job Manager on the client PC using
that account and the server's HTTPS URL. Initial-account creation is not exposed
as an unauthenticated network operation.

**Start, Stop, Restart, Users & Devices, Settings, and Open Logs** are available from
the plugin's dialogs/menu. Closing or reloading the dialog does not stop the daemon;
running jobs continue when MoleditPy closes. Stop preserves running supervisors.

For ordinary Python-based MoleditPy, the plugin uses the application's Python runtime.
For a frozen/portable application, select a Python executable in **Settings → Daemon
Python runtime**, with the server dependencies installed in that environment.

Job Manager **2.6.0 or later** adds the server as a normal destination for submission,
monitoring and downloading. Both the MoleditPy dialogs and browser UI are supported;
use whichever suits the task. Neither requires operating the headless CLI day to day.

## Optional headless installation

Python 3.11+; use a low-privilege OS account.

```sh
git clone --recurse-submodules https://github.com/HiroYokoyama/moleditpy_job_server.git
cd moleditpy_job_server
python -m pip install ".[gui]"
python -m job_server init
python -m job_server create-admin
python -m job_server serve
```

Open **http://127.0.0.1:8766/** on the same machine. Sign in with the administrator
you created. The browser registers an Ed25519 device key; subsequent sign-ins need
only the username in that browser. Private keys are non-exportable Web Crypto keys
in IndexedDB. Bearer tokens stay in page memory. Clearing browser storage requires
registering a new device and revoking the old one.

CLI commands accept `--config /path/to/settings.json` before or after the command.
Persistent data defaults to `~/.moleditpy/job_server/`, outside installed plugin code.
The listener starts only on explicit `serve` or GUI Start and binds **127.0.0.1**.

### MoleditPy plugin

```sh
python scripts/build_plugin.py
```

Install `dist/job_server_0.1.4.zip` using MoleditPy's Plugin Installer. Open
**Plugin → Job Server → Server Dashboard…**. The GUI can bootstrap the first
administrator, start/stop/restart the daemon, pause/resume the queue, manage accounts
and device keys, and cancel jobs. Reloading or closing the plugin does not stop the daemon.
For a frozen/portable MoleditPy application, install the server in a Python environment
and set `python_executable` in settings to that environment's interpreter.

### Job Manager integration

Install [Job Manager 2.6.0 or later](https://github.com/HiroYokoyama/moleditpy_job_manager/releases)
through its normal Plugin Installer/registry entry. Install optional
`cryptography>=50.0.2` in Job Manager's Python environment to enable **Job Server (HTTPS)**.
Existing SSH and local backends do not require it. In **Job Manager → Hosts**, add a
Job Server host, configure the HTTPS origin (HTTP is allowed only on localhost),
and click **Sign in / register device**. The server's execution OS selects the native
scheduler. Submit, monitor, tail, cancel, and download then use the normal Job Manager UI.

For development and regression checks, build a reproducible companion from this
server's pinned older Job Manager source:

```sh
python scripts/build_job_manager.py
```

The test-only companion is `dist/job_manager_job_server.zip`. CI builds and validates
it, but it is excluded from release downloads, distribution artifacts and `SHA256SUMS`.
Install the normal Job Manager 2.6.0+ release for everyday use; installing this test
companion would replace it with the pinned older version plus the integration overlay.

The companion is generated from the audited upstream commit and an explicit integration
overlay. It does not change your sibling Job Manager repository or its upstream branch.
`integration/job_manager.patch` records changes for upstream review;
`integration/server_backend.py` is the additional bridge module.

### HTTPS on a private LAN or Tailscale tailnet

**Setting up HTTPS yourself on a LAN can be cumbersome.** It requires a TLS proxy,
a certificate matching the server address, and a way for every client to trust that
certificate. A private IP address alone does not provide a trusted HTTPS endpoint.
For a small lab, **Tailscale Serve is the simpler setup**: it manages HTTPS
certificates and forwards connections to the local Job Server.

1. Install Tailscale on the server PC and each client PC, and connect them to the
   same tailnet with access permitted between them.
2. Start Job Server on the server PC. Its default local address is
   `http://127.0.0.1:8766`.
3. On that **server PC**, run:

   ```sh
   tailscale serve --bg http://127.0.0.1:8766
   ```

   If prompted, follow Tailscale's setup link to enable HTTPS for the tailnet.
   If you changed Job Server's port, replace `8766` with that port.
4. Copy the **HTTPS URL printed by Tailscale Serve** into Job Manager's
   **Hosts → Server URL**, then use **Sign in / register device**. Use the printed
   hostname, not the server's LAN IP, and do not append `:8766` to that HTTPS URL.
5. To use the browser UI, also set Job Server's `public_url` to that exact HTTPS
   origin and restart the daemon. This permits that browser origin; other origins
   remain rejected.

The daemon stays on loopback and ignores forwarded headers. HTTP on
`127.0.0.1:8766` is the local connection from Tailscale Serve to Job Server; remote
clients connect to the HTTPS URL. Same-PC clients can use the loopback HTTP URL
directly without setting up HTTPS.
Limit access to trusted members using your tailnet access policy. Verify that this
service is not exposed by an existing Funnel or public proxy configuration.
[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) shares within
your tailnet. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel)
publishes to the internet and **must never be used with Job Server**.

For a LAN without Tailscale, terminate HTTPS in a proxy on the server PC, restrict
it with your firewall to trusted local clients, and configure certificate trust on
each client. Job Manager's host profile supports a CA certificate file for your
own certificate authority. Set `public_url` to the proxy's exact HTTPS origin if
using the browser UI. Python clients validate TLS and refuse redirects. Never
forward the service through an internet-facing proxy or router port mapping.

## Python client

```python
from job_server.client import JobServerClient

client = JobServerClient("https://compute.example.org", "device.key")
# Once per device: client.enroll("alice", initial_password)
job = client.submit(
    {"name": "example", "command": "mycommand {input} > {stem}.out",
     "resources": {"cpus_per_task": 2, "memory_mb": 1024, "walltime_seconds": 3600}},
    {"input.inp": "./input.inp"},
    idempotency_key="b176bbfe-bc31-463e-af17-18a2f0678dbd",
)
status = client.request("GET", f"/jobs/{job['id']}")
client.download(job["id"], "result.out", "./results/result.out")
```

Reuse the same idempotency key for retries of a submission. Uploads resume at a
verified contiguous offset. Python downloads resume into `.partial`, verify SHA-256,
and rename on success. Browser downloads verify hashes but buffer the result in memory;
use the Python client for large results. Browser input hashing also uses memory.
Windows client credentials use DPAPI; POSIX credential files require mode 0600.

## Submitting from Windows to WSL

You can launch a Linux program by putting `wsl.exe` in the command field of a
Windows-native Job Server destination. For example, with ORCA installed inside Ubuntu:

```powershell
wsl.exe -d Ubuntu --exec /opt/orca/orca {input}
```

Replace `Ubuntu` and `/opt/orca/orca` with your distribution and Linux executable.
`{input}` is the uploaded input filename; leave the placeholder unquoted because
the server quotes it. The WSL distribution must be available to the **Windows account
running Job Server**. For a remote server, this means WSL on that server, not on the
PC submitting the job. Check its distributions with `wsl.exe --list --verbose`.

WSL normally inherits the Windows command's working directory, so this example
uses the uploaded files in the Windows job directory through its mounted drive.
Explicit paths passed to Linux programs must use Linux syntax (`/home/...` or
`/mnt/c/...`), not `C:\...`. `--exec` directly launches the executable; if the
calculation needs Conda, MPI or other shell setup, put that setup in a Linux wrapper
script and invoke the script instead. See Microsoft's
[Windows/WSL interoperability documentation](https://learn.microsoft.com/en-us/windows/wsl/filesystems).

**Calling `wsl.exe` from a Windows job does not establish WSL process-group management.**
Cancellation and walltime enforcement in Windows mode terminate the Windows process
tree; do not assume every Linux descendant stops. Files written elsewhere inside WSL
are also outside that Windows job's result directory and storage checks.

For the server's WSL execution adapter, set these fields in its existing settings,
then restart the daemon:

```json
{
  "executor": "wsl",
  "wsl_distribution": "Ubuntu",
  "wsl_root": "/home/jobserver/.moleditpy/job_server"
}
```

Use a `/home/...` root writable by the distribution's execution user. This mode
places inputs/results in the Linux filesystem and uses `setsid` plus Linux
process-group signals for cancellation. Submit Linux commands directly, such as
`/opt/orca/orca {input} > {stem}.out`, without another `wsl.exe` prefix. Job Manager
selects the Bash scheduler when registering with this server. Execution mode is
**server-wide**; each job cannot independently switch a Windows server to WSL.

The Windows-hosted WSL adapter has not yet passed end-to-end calculation,
cancellation and filesystem validation on a calculation-capable WSL host; see
[verification](docs/VERIFICATION.md). The command example follows the documented
WSL invocation interface, but is not a claim that ORCA or WSL execution was tested
in this development environment.

For WSL on your own PC without a shared server, Job Manager also offers
**Hosts → This machine, inside WSL (no SSH)** with a distribution selector.

## Execution and limits

- Linux native and WSL-native daemon: Bash and process groups.
- Windows native: PowerShell `-NoProfile -NonInteractive`, suspended launch into a
  Windows Job Object before execution, process-tree cleanup on cancellation.
- Windows-hosted WSL: configure `executor: "wsl"`, an approved `wsl_distribution`,
  and a `/home/...` `wsl_root`. Upload/commit/work directories live in the distribution
  through its UNC filesystem; commands run through `wsl.exe` and `setsid`.
- CPU/RAM limits are **advisory queue budgets**, not kernel-enforced usage limits.
  Walltime and output storage checks stop offending jobs. The initial storage setting
  limits cumulative declared inputs per user and work-directory size per job.
- Dependencies use server job IDs. Failed dependencies remain visibly BLOCKED unless
  `chain_any` is set. Arbitrary existing directories, force-run, HPC module/directive
  options, and scheduled start times are rejected.
- An ambiguous dispatch claim or missing supervisor is held with `NEEDS_RECHECK`,
  preserving its allocation and never automatically executing it again.
- Privileged administrative operations require device authentication within 5 minutes.
- Automatic deletion is off; data and the idempotency ledger are retained.

This is a trusted-user service. Arbitrary commands have the OS account's permissions:
HTTP ACLs and working directories do not isolate mutually untrusted jobs. See
[SECURITY.md](SECURITY.md) and [the specification audit](docs/SPEC_AUDIT.md).

## Development

```sh
python -m pip install -e ".[test,gui]"
python -m pytest -q
python -m ruff check job_server scripts tests integration setup.py
python scripts/run_regression.py
python -m build
python scripts/build_plugin.py
python scripts/build_job_manager.py
```

CI covers Python 3.11/3.12/3.13 on Linux and Windows, including Qt dialogs, real native
job execution, coverage reports, packaging, and the patched Job Manager regression suite.
Security CI audits dependencies and source on each change and weekly.
Windows-hosted WSL needs a machine
with an installed distribution; its end-to-end acceptance is not claimed by native CI.
See [verification](docs/VERIFICATION.md) for actual results and outstanding platform checks.
See [workflows](docs/WORKFLOWS.md) for release/registry setup and
[security audit](docs/SECURITY_AUDIT.md) for findings and remaining limitations.

LGPL-3.0-or-later. Upstream execution code retains its original license and attribution.

## Licensing scope

First-party code in this migration is LGPL-3.0-or-later. See [LICENSE](LICENSE), [COPYING](COPYING) and [dependency/provenance terms](LICENSING.md). Third-party components and historical releases retain their own terms.
