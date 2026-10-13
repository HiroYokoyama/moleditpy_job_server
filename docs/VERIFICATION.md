# Verification

Updated 2026-10-14. All tests use isolated generated accounts and temporary job data.

## Results

- Local server/GUI/client/security/workflow suite: **87 passed**, 3 POSIX-only skips
  on Windows / Python 3.13, in an isolated environment with the hardened dependency floors.
- Real Job Manager optional client: enrollment, password-free reauthentication,
  native completion with exit 0, bulk polling, log/file tail, SHA-256 result download,
  cancellation and cross-user file rejection passed against a live loopback server.
  Dependency-free imports and ordinary GUI/service routing passed separately.
- pip-audit: **no known vulnerabilities** in 72 third-party distributions in the
  isolated environment (the editable first-party project is skipped by the scanner).
  The original runtime had 13 unique advisory IDs across AnyIO, Cryptography and
  Starlette; those versions are now rejected by the daemon's startup checks.
- Bandit: **0 medium/high findings**; low-severity process invocation/control-flow
  findings were manually reviewed. GitHub workflow lint: **actionlint 1.7.12 passed**.
- Bundled execution core: **no Bandit findings**. Full companion upstream: no high
  findings; four medium warnings reviewed in [the audit](SECURITY_AUDIT.md).
- Companion Job Manager regression suite: **2,958 passed**, 25 platform/optional skips,
  196 subtests passed. Git Bash `awk` is required for upstream's CPU-counter check.
- Real browser: device enrollment, password-free reauthentication, job submission,
  native execution, live log, DONE/exit 0, SHA-256 verified result download, and
  responsive dashboard with the reused Job Manager icon were verified in the in-app browser.
- Built wheel, source distribution, server plugin ZIP, and companion Job Manager ZIP.
- GitHub CI includes Linux/Windows Python 3.11/3.12/3.13, regression jobs and
  dependency/static/history security checks. All eight test jobs and Security passed
  on the preceding main revision. Two additional jobs now verify the real Job Manager
  HTTP client on Linux and Windows; current results are on the Actions page.

Local Windows tests cover authentication/enrollment, successful and failed nonce replay,
server binding, key revocation, account disable, fresh administrative authentication,
owner ACLs on every job route, idempotent parallel creation, resumable chunk retries,
corrupt input reset, atomic commit, ranged results, unsafe paths and hardlinks, resource
admission, pause and cancellation, real native exit codes, real Bash wrappers, process
tree cleanup, walltime, supervisor recovery, retained ambiguous allocations, the OS lock,
web CSP/origin checks, plugin registration and offscreen GUI construction.

The source audit and native acceptance findings are in `SPEC_AUDIT.md`. The repository's
CI matrix runs Linux/Windows on Python 3.11/3.12/3.13, builds wheels/sdist and both plugin ZIPs.
Upstream regression testing runs against an isolated patched copy of Job Manager.

Windows-hosted WSL was unavailable in the initial environment (`WSL/E_ACCESSDENIED`);
real WSL execution/cancellation and Linux-filesystem UNC rename must be checked on a
WSL-capable host before claiming the draft's full three-platform acceptance matrix.
An elevated distribution-list check on 2026-10-14 succeeded, but found only a
container-managed distribution and no calculation distribution. No calculation was
run there; the new Windows-to-WSL README examples remain source/documentation-based.
