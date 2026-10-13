# Security audit — 2026-10-13

Follow-up: [2026-10-14 security audit and authentication race fixes](SECURITY_AUDIT_2026-10-14.md).

Scope: all first-party Python modules, browser JavaScript/HTML, native/WSL process
adapters, Job Manager bridge/build overlays, GitHub workflows, package contents,
tracked files and repository Git history. The upstream source remains pinned and
unmodified. Review combined manual source inspection, adversarial regression tests,
Bandit, pip-audit, credential-signature scanning and workflow linting. This is an
engineering audit, not an independent penetration test or a security certification.

## Findings and changes

| Finding | Change |
|---|---|
| Known advisories in locally installed AnyIO 4.12.0, Cryptography 46.0.5, and Starlette 0.52.1 | Raise package minimums to audited versions, including transitive dependencies. Daemon startup refuses environments below those floors. Validate in a separate environment; do not modify shared user Python. |
| Newly created virtual environments inherited pip 25.2 with advisories | Upgrade the isolated environment and CI's pip. Security tooling requires pip >=26.2. |
| Predictable `.tmp` paths could follow a pre-existing link or truncate an unrelated file | Credentials, settings and supervisor JSON use exclusively created random sibling files, mode 0600 on POSIX, fsync and atomic replacement. |
| Reading a job-created FIFO could block before checking its file type | Open with nonblocking/no-follow flags where supported, then reject nonregular files and hardlinks. A bounded subprocess regression proves FIFOs are rejected without a writer. |
| Windows junction detection depended on Python 3.12+ convenience methods | Check reparse attributes using lstat, including Python 3.11. Reject linked roots, nested paths and transfer endpoints. |
| Client `.partial` writes and credential reads followed linked final paths | Use the same regular-file checks and bounded credential reads. |
| An old access token could register another persistent device key | Require authentication within five minutes. Consume password-based enrollment sessions after one registration and recheck their presence under the database write lock. |
| CLI status used a redirect-following, proxy-aware URL opener | Use the normal client: loopback bypasses environment proxies, redirects are forbidden and responses are bounded. |
| New release/registry paths could become privileged script-injection or fork-code paths | Strict version/tag validation, event inputs through environment variables, read-only test tokens, exact action hashes, separate registry secret job, release after test/security gates. |

The initial dependency scan reported multiple advisory records, including duplicate
aliases; their count is not a count of distinct exploitable vulnerabilities. Some
affected framework/cryptography features are unused by this service. Versions were
updated rather than suppressing advisories. The final dependency and Bandit scan
results are recorded in [verification](VERIFICATION.md) and CI artifacts.

Bandit's low-severity findings for process invocation are reviewed: execution is the
product's intended trusted-user operation, arguments are supplied as lists, and WSL
shell paths are quoted. Developer scripts invoke Git/pytest explicitly. No
`shell=True`, disabled TLS verification, dynamic eval, unsafe deserialization, or
untrusted HTML insertion was found in first-party code. Medium/high findings gate CI;
the complete report retains low-severity findings for review.

The exact upstream execution modules bundled into Job Server were separately
scanned (2,161 lines excluding metadata-only `__init__.py`): **no Bandit findings**.
Scanning the full companion's pinned upstream Python package (22,064 lines) found
four medium warnings and no high warnings. Two B608 SQL warnings match escaped Qt
labels beginning with "Select a file", not SQL queries. B601 covers intentional
SSH command execution in Paramiko; Job Server uses its HTTP backend instead. B310
covers the optional user-configured notification webhook, whose entry point checks
HTTP/HTTPS and hostname before opening it. These are reviewed upstream warnings,
not suppressed first-party findings. The complete vendor report is retained in CI;
the bundled server execution modules are also a medium/high gate. Optional upstream
SSH/notification features are not a network-isolation boundary.

## Boundaries that remain

**Never expose the service to the public internet. Localhost, private LAN over HTTPS or a private Tailscale
tailnet only; no Funnel, public reverse proxies/tunnels or router port forwarding.**
The daemon always binds 127.0.0.1, but cannot determine whether an external proxy has
published it. Tailscale membership/access policy supplements the application's own
device authentication; admit trusted people only.

Arbitrary job commands share the service OS identity. A malicious job can read or
modify other jobs, configuration, source or the SQLite ledger when that identity
permits it. HTTP ACLs, symlink checks and Windows Job Objects do not provide OS
tenant isolation or eliminate filesystem races against that same identity. Use
separate OS identities/containers/VMs before accepting untrusted users.

CPU/RAM reservations remain advisory. Per-job output-size sampling can overshoot;
aggregate output storage is not currently charged per user. Expired staging jobs
and retained job metadata can consume storage. Use OS quotas and monitor disk space.
The TLS proxy must enforce read/write deadlines and per-client connection limits.
Browser transfer hashing buffers files in memory. Authentication rate limiting uses
the socket peer, which is shared when requests arrive through a local proxy.

Windows-hosted WSL execution has source/adapter review, not end-to-end acceptance on
this host. WSL is not an isolation boundary against the Windows host. Native CI
does not establish WSL acceptance.

Credential-signature scans are pattern-based, not proof that every possible secret
format is absent. Native Windows private-file permissions inherit account ACLs;
keep the whole service data directory private. Scanner results depend on advisories
available on the audit date and must be rerun as dependencies change.

References: [GitHub workflow security](https://docs.github.com/en/actions/reference/security/secure-use),
[Bandit](https://bandit.readthedocs.io/), [pip-audit](https://github.com/pypa/pip-audit),
[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve),
[Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel).
