# Verification

Recorded 2026-10-13. All tests use isolated generated accounts and temporary job data.

## Results

- Local server/GUI/client suite: **52 passed** on Windows / Python 3.13.
- Companion Job Manager regression suite: **2,958 passed**, 25 platform/optional skips,
  196 subtests passed. Git Bash `awk` is required for upstream's CPU-counter check.
- Real browser: device enrollment, password-free reauthentication, job submission,
  native execution, live log, DONE/exit 0, SHA-256 verified result download, and
  responsive dashboard with the reused Job Manager icon were verified in the in-app browser.
- Built wheel, source distribution, server plugin ZIP, and companion Job Manager ZIP.

Local Windows tests cover authentication/enrollment, successful and failed nonce replay,
server binding, key revocation, account disable, fresh administrative authentication,
owner ACLs on every job route, idempotent parallel creation, resumable chunk retries,
corrupt input reset, atomic commit, ranged results, unsafe paths and hardlinks, resource
admission, pause and cancellation, real native exit codes, real Bash wrappers, process
tree cleanup, walltime, supervisor recovery, retained ambiguous allocations, the OS lock,
web CSP/origin checks, plugin registration and offscreen GUI construction.

The source audit and native acceptance findings are in `SPEC_AUDIT.md`. The repository's
CI matrix runs Linux/Windows on Python 3.11/3.13, builds wheels/sdist and both plugin ZIPs.
Upstream regression testing runs against an isolated patched copy of Job Manager.

Windows-hosted WSL was unavailable in the initial environment (`WSL/E_ACCESSDENIED`);
real WSL execution/cancellation and Linux-filesystem UNC rename must be checked on a
WSL-capable host before claiming the draft's full three-platform acceptance matrix.
