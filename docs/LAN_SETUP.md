# Connecting from another PC

A step-by-step setup for reaching Job Server from another PC. [日本語](LAN_SETUP_JP.md).
For design details and security notes, see [LAN_ACCESS.md](LAN_ACCESS.md) and
[SECURITY.md](../SECURITY.md).

The examples use these placeholder values. Replace them with your own.

| Value | Example |
|---|---|
| Server PC's LAN IP | `192.168.1.20` |
| HTTP port (default) | `8766` |
| HTTPS port (default) | `8767` |
| Tailscale name | `your-pc.your-tailnet.ts.net` |

Requires Job Server 0.6.0 or later on the server PC and Job Manager 3.9.0 or later on
the client PC. Job Server 0.5.0 listens only on `127.0.0.1`.

## Choose a connection

| Connection | URL | Firewall rule | Traffic | Use when |
|---|---|---|---|---|
| Local HTTPS (recommended) | `https://192.168.1.20:8767` | Required | Encrypted | Both PCs are on the same LAN |
| Tailscale | `https://your-pc.your-tailnet.ts.net` | Not required | Encrypted | Both PCs are on the same tailnet, or the firewall cannot be changed |
| LAN HTTP | `http://192.168.1.20:8766` | Required | **Not encrypted** | A trusted LAN, when HTTPS cannot be used |

The scheme and port must match. `8767` serves HTTPS only, so `http://192.168.1.20:8767`
cannot connect.

## Local HTTPS

### On the server PC

1. Open **Job Server > Settings**.
2. Enable **Enable local HTTPS with a persistent local CA**.
3. Under **LAN listen address**, select the PC's LAN IP.
4. Save, then choose **Server > Restart**, or **Start** if the server is off.
5. Open **Create / show CA certificate...** and keep the SHA-256 fingerprint in view.
6. Confirm the server is listening on the LAN IP:

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 8766,8767 | Select-Object LocalAddress,LocalPort
   ```

   The list must include `192.168.1.20`. If it shows only `127.0.0.1`, check steps 2 to 4.

7. Open an **administrator** PowerShell and check the network profile:

   ```powershell
   Get-NetConnectionProfile | Select-Object InterfaceAlias,NetworkCategory
   ```

8. If the LAN adapter is `Public` and the network is trusted, change it to `Private`.
   Use the adapter name shown in step 7.

   ```powershell
   Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private
   ```

9. Allow the port. Changing the profile alone does not open it.

   ```powershell
   New-NetFirewallRule -Name "MoleditPy-Job-Server-LAN-HTTPS" -DisplayName "Job Server LAN HTTPS" -Direction Inbound -Action Allow -Protocol TCP -LocalAddress 192.168.1.20 -LocalPort 8767 -RemoteAddress LocalSubnet -Profile Private
   ```

### On the client PC

1. Check that the port is reachable. `-k` skips certificate checks, so use it only for
   this test, which sends no credentials.

   ```powershell
   curl.exe -k https://192.168.1.20:8767/api/v1/health
   ```

   A response containing `"status":"ok"` means the connection works.

2. In Job Manager, open the host settings and set **Backend** to **Job Server (HTTPS)**.
3. Set **Server URL** to `https://192.168.1.20:8767`.
4. Choose **Trust local server CA...**. Compare the displayed fingerprint with the one
   on the server PC, and choose **Yes** only if they match.
5. Choose **Sign in to Job Server...** and enter your username and password.

## Tailscale

Both PCs must be signed in to the same tailnet. Use private Tailscale Serve only. Never
use Funnel or router port forwarding.

1. On the server PC, run the following and note the URL it prints:

   ```powershell
   tailscale serve --bg http://127.0.0.1:8766
   ```

2. Add that URL to **Job Server > Settings > Web access URLs**, save and restart.
3. In Job Manager, set **Server URL** to `https://your-pc.your-tailnet.ts.net`.
4. Choose **Sign in to Job Server...**. **Trust local server CA...** is not needed,
   because the certificate is publicly trusted.

## LAN HTTP

Passwords, tokens and job data can be read by anyone able to intercept LAN traffic. Use
this only on a trusted LAN.

1. On the server PC, enable **Allow trusted LAN HTTP**, select the **LAN listen address**,
   save and restart.
2. Follow steps 7 to 9 of Local HTTPS, using port `8766` and a separate rule name.
3. In Job Manager, set **Server URL** to `http://192.168.1.20:8766`.
4. Enable **Allow trusted LAN HTTP (unencrypted)**.
5. Choose **Sign in to Job Server...**.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Ping works but the port times out | Firewall on the server PC, or no LAN listener | Check the listeners, then add the firewall rule |
| Timeout after changing the profile to `Private` | No firewall rule for the port | Add the rule. The profile change alone is not enough |
| Timeout with the rule in place | Rule profile does not match the network, group policy, or other security software | Check the rule and profile, then ask your administrator |
| Only `127.0.0.1` is listening | LAN listener is off, or the server is older than 0.6.0 | Enable it in Settings and restart, or update |
| `Set-NetConnectionProfile` fails with a permission error | Not elevated, a domain network, or group policy | Retry as administrator. If it still fails, ask your administrator or use Tailscale |
| `http://` to port `8767` fails | The port serves HTTPS only | Use `https://`, or `http://` with port `8766` |
| Certificate error | CA is not trusted, or the IP changed | Run **Trust local server CA...** again. After an IP change, update the URL |
| `origin_forbidden` in a browser | The address is not registered | Add the exact origin to **Web access URLs** and restart |

If the server PC is managed by your organization, ask its administrator before changing
the network profile or firewall rules.
