# Security model

> [!WARNING]
> **Never expose this server to the public internet. Localhost, private LAN or private Tailscale only.**
> Never use Tailscale Funnel, public reverse proxies/tunnels, or router port forwarding.
> Restrict tailnet access to trusted users. A loopback listener can still be exposed by a proxy;
> the daemon cannot detect or prevent a misconfigured proxy or Funnel.

This server is restricted to trusted users sharing a low-privilege execution account.
It does not provide OS-level multi-tenant isolation. Commands may read other jobs and
the daemon's data if their OS identity permits it. Never accept mutually untrusted users
until separate OS identities, containers or VMs and brokered execution are implemented.
Windows Job Objects manage process lifetime, not file-access isolation. WSL is not a
complete security boundary from its Windows host.

The API enforces owner/admin authorization for each job, log, upload and download.
Job mutations recheck session validity inside the write transaction so completed
device revocation, logout or user disable takes effect before the mutation.
Passwords use scrypt with a random salt (N=32768, r=8, p=1). Enrollment sessions cannot
submit jobs and are consumed after one successful device registration. Registering an
additional device requires fresh authentication. Ed25519 nonce signatures bind protocol,
server identity, device and purpose.
Nonces expire after 60 seconds and are consumed after any verification attempt.
Access tokens expire after 15 minutes by default and are stored hashed. Disabling a user
or revoking a device invalidates outstanding tokens. Administrative writes require fresh
device authentication. Initial administration is local-only using access to the
server's data directory. It works with the daemon running; a SQLite write
transaction atomically prevents creation when an enabled administrator exists.
There is no unauthenticated account-creation endpoint.

The daemon binds loopback by default and refuses elevated execution except an explicit isolated-test
override. Private LAN/Tailscale clients use validated HTTPS by default, terminating on the same host.
An explicit `allow_lan_http` exception permits unencrypted HTTP to literal RFC 1918 IPv4
or IPv6 ULA addresses. An optional `lan_bind_address` adds a listener on one specific
private interface while retaining the loopback listener. Enable local HTTPS or opt in to HTTP;
no wildcard, DNS, public, link-local or loopback LAN address is accepted. The OS must
own the selected IP; bind failures stop startup and close all opened sockets. The
application does not change firewall rules. Restrict the port to trusted LAN clients.
A private same-host reverse proxy remains supported. Clients opt in separately,
bypass environment proxies for HTTP and refuse redirects. DNS names and public, link-local,
unspecified and reserved IP addresses are excluded from the exception. Enrollment passwords,
bearer tokens and job contents are visible to anyone able to intercept that LAN traffic;
Ed25519 authentication does not provide transport confidentiality or prevent active interception.
Only use this exception on a trusted LAN with a restricted proxy/firewall. All registered
`public_url`/`public_urls` origins are checked exactly. The direct LAN origin is included automatically; adding
an origin alone never opens a listener.
Forwarded headers are ignored. Only exact configured browser origins are accepted;
there is no permissive CORS response. Web sign-in uses password verification and a short-lived opaque token held only in
tab-scoped session storage for reload recovery. It does not save passwords or
generate browser device keys. Restored sessions are checked against the server. Web sessions
cannot enroll persistent devices; desktop enrollment remains a separate scope.
Tokens are hashed server-side, expire, and are invalidated by logout or user disable.
No access token is placed in a URL, cookie or localStorage. Explicit logout clears
the tab token and invalidates it server-side.
The web UI uses a restrictive CSP, no third-party assets and textContent for job data.

Native LAN HTTPS uses TLS 1.2 or newer and a persistent, per-data-directory local
CA. The CA is generated with private/loopback IP name constraints, localhost DNS
constraints, server-auth usage and no intermediate CA delegation. Server leaf
keys are encrypted; the CA key and leaf-key password use DPAPI on Windows and
owner-only files on Unix. These files remain outside installed plugin code.
Certificate setup never rotates an existing CA silently. Public CA export parses
and serializes only the public certificate; no key has an HTTP export endpoint.

CA bootstrap is a separate, unauthenticated GET to a fixed public-certificate path
on a private HTTPS origin. That request alone permits an unverified TLS transport;
its response is bounded, redirects/proxies are disabled, and the full SHA-256
fingerprint supplied from the server PC must match before trust is installed.
Normal API requests retain certificate-chain and hostname verification. Windows
registration explicitly targets CurrentUser ROOT, not LocalMachine. Mac/Linux
use a saved per-connection CA. OS trust applies to other applications using the
same store and requires explicit user confirmation. Tests mock store changes.

Transfers reject traversal, Windows device names, alternate data streams, reserved wrapper
paths, links, junctions and hardlinks. Containment checks defend HTTP transfers; they are
not a sandbox for arbitrary job commands. Job logs, metadata and transfer hashes are
private to owners/admins. Request errors omit submitted passwords and credential values.
Special files are opened without blocking and rejected before transfer. Private writes
use exclusively created temporary files and atomic replacement. These measures do not
protect against a malicious job running under the same OS identity and modifying the
filesystem concurrently. Keep the service data directory private to its OS account;
Windows permissions depend on the account's directory ACLs.

Install current dependency versions. Daemon startup checks audited minimum versions;
security CI continues to check for new published advisories. Scanner results are not
a guarantee that vulnerabilities are absent. See [the audit](docs/SECURITY_AUDIT.md).

Request lengths and chunk sizes are bounded. Uvicorn limits concurrent connections and
idle keepalive; the HTTPS proxy must impose total read/write deadlines and connection/IP
rate limits for slow clients. Authentication has a daemon-local rolling limit of 20
attempts/minute per socket peer; behind a proxy the peer is the proxy, so large labs
should configure a suitable external per-client rate limiter as well.

Resource reservation is advisory. Disk checks sample job directories and can overshoot
between samples; use OS filesystem quotas for a strict cap. Cumulative declared input
size is limited per user. Results are limited per job in this server, not aggregated
per user. Restart recovery never automatically resubmits a claimed job. Ambiguous jobs
retain their allocation until an administrator inspects the supervisor and payload.

Report vulnerabilities privately to the repository owner. Do not include real job data,
passwords, bearer tokens or private keys in issues, logs or pull requests.
