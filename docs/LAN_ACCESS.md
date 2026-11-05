# LAN access

## Direct HTTP connection

1. Start Job Server and open **Settings**.
2. Enable **Allow unencrypted HTTP on trusted LAN addresses**.
3. Under **LAN HTTP listen address**, select this PC's private IP address.
4. Save the configuration, then choose **Server > Restart**.
5. Choose **Server > Copy connection addresses**. Open the LAN URL from another PC,
   for example `http://192.168.1.20:8766`.

The server listens on the selected private IP and on `127.0.0.1`, using the same
server port. The LAN URL is included automatically in the browser-origin allowlist.
**Local only (default)** disables the additional listener. Enabling HTTP permission
without selecting an address does not open a LAN listener.

Allow the TCP server port through the server PC's firewall for trusted LAN clients.
The application does not change firewall rules. On Windows, for a Private network,
run the following in a separate administrator PowerShell. Replace the example IP
and port with the values selected in Settings. Run Job Server under its normal,
non-administrator account.

```powershell
$lanAddress = "192.168.1.20"
$serverPort = 8766
New-NetFirewallRule -Name "MoleditPy-Job-Server-LAN" -DisplayName "Job Server LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $lanAddress -LocalPort $serverPort -RemoteAddress LocalSubnet -Profile Private
```

In Job Manager, use the copied URL and enable **Allow trusted LAN HTTP** for that
host before signing in. HTTPS remains recommended. HTTP exposes passwords, bearer
tokens, input files and results to interception or modification on the network.
Never use router port forwarding or a public tunnel.

## Web access URLs

Enter one browser origin per line in **Web access URLs**. Use this field for
Tailscale Serve or other private HTTPS proxy addresses. These entries permit
browser origins; they do not create listeners. Existing `public_url` and
`public_urls` settings appear together in this field. Saving uses `public_urls`.
The selected direct LAN URL is managed automatically and appears in the same field.

## Troubleshooting

- Confirm `http://127.0.0.1:<port>` opens on the server PC first.
- Use the LAN URL shown after saving; the server port defaults to 8766.
- If startup reports a bind failure, select an IP currently assigned to this PC
  and check whether another program or an earlier portproxy rule owns that port.
- If localhost works but the LAN URL times out, check the firewall, network
  profile, client routing and Wi-Fi client isolation.
- If the server IP changes, select the new address, save and restart.
- An origin error requires the browser's exact origin, including its port, in
  **Web access URLs**.

Version 0.5.0 supports LAN HTTP client URLs and origin registration but has only a
loopback listener. Direct LAN listening requires 0.5.1 or later. An external proxy
is still supported for HTTPS access.
