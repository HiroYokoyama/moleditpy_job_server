# Security audit — 2026-10-14

scanned by GPT-6 Astra

Audit date: **2026-10-14 (Asia/Tokyo)**. Repository: `moleditpy_job_server`.
Baseline: `2929cb96360bb57352a8c0d6725a714651688364`.
Security fixes: `8f128af` and `c39781f`, included in version **0.1.1**.

This follow-up combines source review, dependency/static scans and adversarial
tests with generated accounts and temporary job data. No real user records or
credentials were inspected. It does not certify absence of vulnerabilities.

## Code checked

| Review | Files and focus |
|---|---|
| Authentication and authorization | `job_server/auth.py`, `job_server/api.py`, `job_server/db.py`, `job_server/schemas.py`: password verification, session scopes/expiry, enrollment, nonce consumption, revocation, administrator checks, database transactions and input validation |
| File and job boundaries | `job_server/files.py`, `job_server/jobs.py`, `job_server/client.py`, `job_server/server_backend.py`: ownership, paths, regular-file checks, upload/download ranges, hashing, TLS, redirects and result fetching |
| Credentials and browser | `job_server/credentials.py`, `job_server/web/app.js`: DPAPI/private key storage, enrollment, token handling and browser requests |
| Startup and execution | `job_server/server_app.py`, `job_server/__main__.py`, `job_server/settings.py`, `job_server/runtime.py`, `job_server/core.py`, `job_server/worker.py`: loopback binding, proxy-header policy, dependency floors, wrapper generation and trusted-user execution |
| Integration and release | `integration/server_backend.py`, `scripts/audit_publication.py`, `scripts/build_plugin.py`, `scripts/verify_release.py`, `.github/workflows/security.yml`, `.github/workflows/release.yml`, `pyproject.toml`: client bridge, secret signatures, packaging and release validation |
| Automated static coverage | Every Python file under `job_server/`, `scripts/` and `integration/`; separately, pinned `upstream/job_manager/job_manager/{__init__.py,models.py,remote_paths.py,remote_runner.py,schedulers/}` |
| Regression coverage | Entire `tests/` suite; new `tests/test_request_security.py` and `tests/test_auth_concurrency.py` exercise the findings below |

Static coverage is broader than the focused manual review. The upstream submodule
was not changed. The full companion regression suite was not rerun because the
integration overlay was unchanged; server-side integration tests were run.

## Confirmed findings and fixes

### 1. Administrator creation could survive concurrent credential revocation

An authenticated administrator could start creating another administrator, then
have their device revoked or account disabled while the new password was being
hashed. The pending request still inserted the new account and returned HTTP 201.
This required existing administrator credentials and a concurrent request; it was
not an unauthenticated privilege escalation.

Both variants were reproduced before the fix. Creation now rechecks the live
session, account status, device revocation, role and authentication freshness
inside the same database write transaction as account insertion. Both variants
now return HTTP 401 without creating the additional account.

Administrative disable/revoke/pause/shutdown operations and device enrollment also
recheck authentication under the write lock. Login rechecks the account and the
verified password hash before issuing its enrollment session, so a completed
account disable cannot be followed by issuance from a stale login snapshot.
These guards do not retroactively cancel operations already committed or jobs
already running.

### 2. Unbounded numeric inputs escaped normal HTTP error handling

Very long decimal `Content-Length` and upload range headers could reach Python's
integer conversion limit. Pagination/event offsets beyond SQLite's signed
64-bit range and oversized log seek offsets could raise overflow exceptions.
The regression tests reproduced these exceptions before the fix.

Content lengths are checked before conversion and buffering. Transfer ranges now
use bounded ASCII decimal fields. SQLite/log offsets have a signed 64-bit upper
bound. These requests now receive HTTP 400, 413 or 416 as appropriate instead of
unhandled exceptions. A complete service outage was not demonstrated.

### 3. Invalid download ranges caused unnecessary full-file hashing

Range syntax and bounds were previously checked after hashing the entire result.
An authenticated caller could request an invalid range and still force full-file
I/O. Range validation now precedes hashing, and preparation errors close the open
file. Tests cover malformed, huge, reversed, out-of-file and empty ranges.

## Authentication checks that passed

- Parallel use of one enrollment session registers exactly one device.
- Parallel replay of one valid signed nonce issues exactly one access token.
- Invalid/expired signatures and consumed nonces are rejected.
- Revoked-device and disabled-account tokens fail; ordinary users cannot access
  administrator routes; stale administrative authentication is rejected.
- Changing `X-Forwarded-For` or `X-Real-IP` does not bypass the socket-peer limit.
- Invalid Unicode authentication fields are already rejected by schema validation;
  this suspicion did not reproduce as a vulnerability and needed no source change.
- Existing TLS, loopback proxy bypass, ownership and private-key storage tests pass.

## Verification

Environment: isolated Windows/Python 3.13 environment; shared user Python was not
modified. Tests requiring asynchronous/native facilities were run outside the
restricted tool sandbox, with synthetic data only.

- Full suite after both security fixes: **111 passed, 3 skipped**. The skips are
  POSIX-only cases. One Starlette TestClient/httpx deprecation warning remains.
- Ruff: `python -m ruff check job_server scripts tests integration setup.py` passed.
- pip-audit 2.10.1: **no known vulnerabilities** in **65 third-party distributions**;
  the editable first-party package was skipped. This reflects the resolved audit
  environment, not every version allowed by the dependency ranges.
- Bandit 1.9.4: **0 medium/high findings**, 28 low findings. These cover intentional
  process invocation, executable lookup and the GUI's existing-daemon probe. They
  remain visible in the report; no scanner suppressions were added.
- Bundled upstream execution modules: **0 Bandit findings**.
- Credential-signature scanning of the Git index and history passed. This is a
  pattern-based check, not proof that every possible secret format is absent.
- Wheel, source distribution, server plugin ZIP and companion ZIP were built and
  validated with `scripts/verify_release.py --artifacts`.

Local machine-readable reports are under ignored `test-results/`:
`security-rescan-bandit.json`, `security-rescan-dependencies.json` and
`security-rescan-tests.xml`. Reproduce the scan with the commands in
`.github/workflows/security.yml` and run `python -m pytest -q` for the tests.

## Remaining boundaries

The existing [security model](../SECURITY.md) still applies: arbitrary job commands
share the service OS account, and HTTP authorization is not OS tenant isolation.
Use trusted users and a private network. The proxy must enforce slow-client
deadlines and suitable per-client rate limits; the application limit is shared
by clients arriving through one proxy peer. Windows ACL configuration and real
WSL execution were not newly validated in this follow-up. No new Linux run or
independent penetration test is claimed.
