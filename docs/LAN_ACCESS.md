# LAN access

For a step-by-step setup, see [LAN_SETUP.md](LAN_SETUP.md) ([日本語](LAN_SETUP_JP.md)).

## Local HTTPS

1. Open **Job Server > Settings**.
2. Enable **Enable local HTTPS with a persistent local CA**.
3. Select this PC's private IP under **Reachable from other PCs at**. The default HTTPS
   port is **8767**; local HTTP administration remains on **8766**.
4. Save and accept the restart the window offers, or choose **Server > Start** if the server is off.
5. Use **Server > Copy connection addresses** to copy the HTTPS URL.
6. Allow the HTTPS TCP port through the OS firewall for trusted LAN clients.

A local CA is created under the server data directory's `tls/` folder. The CA is
retained across application updates and LAN IP changes. A server certificate is
renewed at startup when necessary. Keep this folder in the server data backup.
CA expiry or an incomplete CA stops certificate setup; trust is not silently
replaced. Certificate generation does not start a listener.

Open **Create / show CA certificate...** in Settings to view and copy its SHA-256
fingerprint or export its public CA certificate. Only the public certificate is
exported. On Windows, **Trust CA for this Windows user...** registers it in that
user's ROOT store after confirmation. Browser trust on Mac/Linux is configured
with the operating system or browser certificate manager.

## Job Manager

1. Create a Job Server host with the copied HTTPS LAN URL.
2. Choose **Trust local server CA...**.
3. Compare the displayed SHA-256 fingerprint with the one on the server PC, then
   choose **Yes** if they match.
4. Choose **Sign in to Job Server...** and enter username and password.

The CA is retrieved without credentials and accepted only after you confirm its
fingerprint. If the values differ, choose **No**: something else may be answering
at that address. A copy is saved under Job Manager's `identity/ca/` on every platform.
On Windows it is also registered in the current user's ROOT store.
Applications using that store will trust certificates issued by the CA. On
Mac/Linux it is saved automatically for this connection and entered in the CA
field; the system trust store is unchanged. Normal API requests always validate
the certificate chain and server name. Publicly trusted HTTPS, such as Tailscale
Serve, does not need local CA registration.

If trust-store policy prevents Windows registration, export the public CA from
the server and select that file in the host's **CA certificate path** field.

## Optional LAN HTTP

Enable **Allow unencrypted HTTP on trusted LAN addresses**, choose a private LAN
IP, save and restart. The copied HTTP URL uses the HTTP server port, normally
8766. Job Manager also requires the per-host HTTP opt-in. Local HTTPS and LAN
HTTP can be enabled together on different ports.

HTTP exposes passwords, bearer tokens, inputs and results to network interception
or modification. HTTPS is recommended. The default remains local-only access;
setting Other web URLs alone does not open a LAN listener.

## Firewall

The application does not change firewall rules. For Linux and Mac, see
[Server on Linux or Mac](LAN_SETUP.md#server-on-linux-or-mac). On a Windows Private network,
run the following in a separate administrator PowerShell, replacing the example
IP and port. Run Job Server under its normal non-administrator account.

```powershell
$lanAddress = "192.168.1.20"
$serverPort = 8767
New-NetFirewallRule -Name "MoleditPy-Job-Server-LAN" -DisplayName "Job Server LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $lanAddress -LocalPort $serverPort -RemoteAddress LocalSubnet -Profile Private
```

Never use router port forwarding or a public tunnel.

## Other web URLs

Enter one browser origin per line. Use this field for Tailscale Serve or other
private HTTPS proxy addresses. Existing `public_url` and `public_urls` entries
appear together; saving uses `public_urls`. Direct LAN origins are added
automatically. Localhost administration remains available in the same daemon.

## Start, stop and restart

The status banner shows the requested operation and elapsed time. Start confirms
listener readiness before reporting success. Stop confirms release of the daemon
lock. Restart waits for shutdown before starting again. Running job supervisors
are preserved. Requests made during automatic refresh are queued, and duplicate
pending server commands are ignored. Configuration saving is disabled while a
server operation is pending. A stopped server shows **Server is OFF**.

Settings and setup are scrollable. Spin boxes accept wheel changes only while
focused, so scrolling over another field does not change its value.

## Troubleshooting

- Confirm `http://127.0.0.1:<HTTP port>` opens on the server PC.
- Use the copied URL, including its scheme and port.
- For certificate errors, check the server fingerprint and clock before trusting
  a CA. Do not disable certificate verification.
- For a bind failure, select an IP assigned to this PC and check for another
  program or an old portproxy rule using the port.
- For timeouts, check the firewall, network profile, routing and Wi-Fi client
  isolation. Startup failures are recorded in **File > Open Logs > startup.log**.
- If the LAN IP changes, select the new address, save and restart; the CA remains
  the same, but clients need the new URL.
- Add a browser's exact origin, including its port, for an origin error.

Direct LAN listeners and the local CA workflow require Job Server 0.6.0 or later.
Job Server 0.5.0 has only a loopback listener, even when LAN origins are configured.
