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
Passwords use scrypt with a random salt (N=32768, r=8, p=1). Enrollment sessions cannot
submit jobs and are consumed after one successful device registration. Registering an
additional device requires fresh authentication. Ed25519 nonce signatures bind protocol,
server identity, device and purpose.
Nonces expire after 60 seconds and are consumed after any verification attempt.
Access tokens expire after 15 minutes by default and are stored hashed. Disabling a user
or revoking a device invalidates outstanding tokens. Administrative writes require fresh
device authentication. Initial administration is local-only with the daemon stopped.

The daemon binds loopback and refuses elevated execution except an explicit isolated-test
override. Private LAN/Tailscale clients must use validated HTTPS terminating on the same host.
Forwarded headers are ignored. Only exact configured browser origins are accepted;
there is no permissive CORS response. Browser keys remain in IndexedDB as non-exportable
CryptoKeys, and access tokens are not placed in URLs or persistent browser storage.
The web UI uses a restrictive CSP, no third-party assets and textContent for job data.

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
