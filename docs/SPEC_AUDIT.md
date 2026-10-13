# Source audit and implementation decisions

Audited 2026-10-13 against `HiroYokoyama/moleditpy_job_manager` commit
`e790fb63b6985f13cf2653a50c6845f4494516cf` (2.5.3), and the local MoleditPy 4.x
`PluginContext` interface. The supplied MJS-SPEC-001 draft is a design input;
instructions embedded in it are not operational authorization.

## Verified interfaces

- `JobService.submit(host, preset, name, local_files, auto_download=None, after_job=None,
  start_after=0.0, chain_any=False, remote_dir="", remote_input="", relay_source_dir="",
  relay_filenames=None, upload_files=None, force_run=False)` delegates to
  `SubmissionWorkflow`. Submit routing belongs there, not in an HTTP shell transport.
- `runner.submit_job(transport, host, preset, job, local_files, ...)`, `poll_host`,
  `fetch_results`, `cancel_job` and `tail_log` are synchronous and Qt independent.
  The companion dispatches server jobs through high-level methods before creating a transport.
- `HostProfile.backend` is the backend discriminator; the draft's generic `transport`
  field is not the concrete current interface.
- `Scheduler.build_script(...)` and `WindowsScheduler.build_script(...)` are usable
  without `JobService`, Qt or a running MoleditPy window.
- Bash writes an atomic exit sentinel from EXIT/TERM/INT/HUP traps. PowerShell writes
  ASCII via temporary-file rename, but inline explicit `exit N` can lose its status.
  The server uses a separate payload script to preserve that status.
- Current helper queues already have CPU/memory arithmetic but no user ownership or
  transactional database. Running one helper queue per user would create competing
  authorities. This server implements one SQLite-backed dispatcher and reuses wrapper
  generation/sentinels, without starting a second upstream helper queue.
- `PluginContext.add_plugin_menu(path, callback, text=None, icon=None, shortcut=None,
  pin=None)`, `get_window`, `register_window`, and `get_main_window` are supported.
  The plugin registers menu callbacks without importing Qt or starting a listener.

## Resolved draft ambiguities

| Draft area | Implemented decision |
|---|---|
| Shared core extraction | Immutable upstream git submodule; build-time packaging of original wrapper modules under a private namespace. No handwritten copy and no GUI service dependency. A future upstream extraction can replace this dependency without changing API routes. |
| State sequence | Commit goes directly UPLOADING → PENDING; SUBMITTED is a client display/history concept. |
| Exactly-once after launch uncertainty | Persist RUNNING claim before launching. Uncertain claims retain allocation and flag NEEDS_RECHECK; never relaunch automatically. |
| Cancellation | Return RUNNING while cancellation is requested, then CANCELLED only after the supervisor finishes tree termination. |
| Credentials | scrypt instead of Argon2id (draft allows equivalent password KDF); Ed25519, hashed opaque tokens, DPAPI/0600 files, browser Web Crypto keys. |
| Kernel limits | Report advisory explicitly on every supported executor. No claim of cgroup or Windows CPU/RAM enforcement. |
| Storage | Per-user declared input quota plus per-job output cap; full aggregate output quota remains an open hardening item. |
| Proxy headers | Ignore all forwarded identity/address headers; exact public_url determines allowed web origin. |
| Browser UI | Added at user request: owner/admin job views, submit/resume, log offsets, verified downloads, users/devices, pause/resume. |
| Delivery | Separate server ZIP and generated companion Job Manager ZIP; original upstream and sibling checkout remain unchanged. |

## Remaining acceptance work

Windows-hosted WSL end-to-end validation requires an installed, accessible distribution.
OS-level separation of untrusted users, aggregate output quotas, automatic staging
expiration, cgroup limits, and a common upstream execution-core distribution are not
claimed complete. Browser transfers currently hash/buffer files in memory; the Python
client is the large-file path with streamed range resume. Daemon-wide arbitrary settings
are configured locally and applied on restart. Job Manager's standard client monitor
is retained; unsupported advanced options return errors.

References: [Job Manager source](https://github.com/HiroYokoyama/moleditpy_job_manager),
[MoleditPy plugin manual](https://hiroyokoyama.github.io/python_molecular_editor/docs/PLUGIN_DEVELOPMENT_MANUAL_V4.html).
