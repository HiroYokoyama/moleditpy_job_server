# HTTPS, Tailscale and several addresses

For a step-by-step setup, see [Connecting from another PC](LAN_SETUP.md).

## Multiple connection addresses and optional LAN HTTP

In **Settings**, enter Tailscale or proxy origins, one per line, in **Other web URLs**. Existing
`public_url` and `public_urls` configurations appear in this single field.
Browser requests use an exact origin allowlist, without wildcards.

For direct access from another LAN PC, enable **local HTTPS**, tick this PC's
private IPs or `.local` name in **Reachable from other PCs at**, save, and accept the offered restart.
The HTTPS port defaults to 8767. The server creates a persistent local CA and
renews its server certificate when needed at startup. Use **Create / show CA
certificate...** to copy the fingerprint or export the public CA.

Job Manager's **Trust local server CA...** retrieves the CA and displays its
fingerprint for you to compare with the server PC before saving a local CA copy on every platform. Windows also registers
it in the current user's certificate store; Mac/Linux use the saved connection CA. Username and password share one sign-in
dialog. Browser trust registration uses the system/browser certificate manager.

The daemon retains localhost HTTP administration. LAN HTTP is also available by
explicit opt-in and uses the HTTP port, normally 8766. HTTP credentials and job
contents are unencrypted. All direct listeners require a specific private IP;
wildcard, public, DNS and link-local bind addresses are rejected. No firewall rules
are changed automatically. Restrict server ports to trusted LAN clients and never
use public tunnels or router forwarding. See [LAN setup](LAN_ACCESS.md).

Python clients must opt in separately:

```python
client = JobServerClient("http://192.168.1.20:8080", "device.key", allow_lan_http=True)
```

The generated development companion adds a per-host **Allow trusted LAN HTTP**
checkbox, used for enrollment and subsequent connections. Use a Job Manager release that includes the matching per-host LAN HTTP option;
older Job Manager releases require HTTPS for remote connections.
HTTPS and localhost remain compatible with existing clients. HTTP clients bypass
environment proxies and refuse redirects to keep credentials on the selected origin.

## HTTPS on a private LAN or Tailscale tailnet

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
5. To use the browser UI, paste that same HTTPS URL in Job Server's
   **Settings → Other web URLs**, save the configuration, then
   choose **Server → Restart**. The JSON setting is `public_urls` (one origin per list entry). This enables browser access at that address. Other website addresses remain rejected.

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
own certificate authority. Add the proxy's exact HTTPS origin to `public_urls` if
using the browser UI. Python clients validate TLS and refuse redirects. Never
forward the service through an internet-facing proxy or router port mapping.
