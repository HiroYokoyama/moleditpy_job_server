# Execution and limits

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
- Repeated reconciliation of an unchanged `NEEDS_RECHECK` warning does not start
  another update transaction. Uncertain jobs remain RUNNING with allocations reserved
  until an administrator chooses **Mark as lost**, which the server refuses while the
  job's own supervisor is still running.
- Job Manager backend operations reuse bounded in-memory authentication state;
  polling no longer creates a session and authentication event each time. Reuse is
  isolated by server/credential/TLS trust, respects expiry and administrative freshness,
  and is invalidated by key/trust changes or HTTP 401. Bearer tokens are not persisted.
- Privileged administrative operations require device authentication within 5 minutes.
- Automatic deletion is off; data and the idempotency ledger are retained.

This is a trusted-user service. Arbitrary commands have the OS account's permissions:
HTTP ACLs and working directories do not isolate mutually untrusted jobs. See
[SECURITY.md](../SECURITY.md) and [the specification audit](SPEC_AUDIT.md).
