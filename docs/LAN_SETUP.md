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
3. Under **Reachable from other PCs at**, tick the PC's LAN IP. Tick more than one if the
   PC has several networks. **Connect using** shows the resulting URLs.
4. Save, then choose **Server > Restart**, or **Start** if the server is off.
5. Open **Create / show CA certificate...** and keep the SHA-256 fingerprint in view.
6. Confirm the server is listening on the LAN IP. Steps 6 to 9 are for Windows; for
   Linux or Mac, see [Server on Linux or Mac](#server-on-linux-or-mac).

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

### Server on Linux or Mac

Steps 6 to 9 above are for Windows. On Linux or Mac, do the following instead. Each rule
stays in place after a restart. If no firewall is active, no rule is needed.

Confirm the server is listening on the LAN IP:

```bash
# Linux
ss -ltn | grep -E ':(8766|8767)'
# Mac
lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(8766|8767)'
```

**Linux with ufw** (Ubuntu and Debian). Check `sudo ufw status` first; use your own subnet
in place of `192.168.1.0/24`.

```bash
sudo ufw allow from 192.168.1.0/24 to 192.168.1.20 port 8767 proto tcp
```

**Linux with firewalld** (Fedora and RHEL). Check `sudo firewall-cmd --state` first. Use
your interface's zone from `sudo firewall-cmd --get-active-zones` in place of `public`.

```bash
sudo firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="192.168.1.0/24" destination address="192.168.1.20" port port="8767" protocol="tcp" accept'
sudo firewall-cmd --reload
```

**Mac.** The built-in firewall allows or blocks applications rather than ports, so it
cannot limit access to one subnet. Check its state first:

```bash
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate
```

If it is on, allow the Python that runs Job Server. Replace the path with the output of
`python3 -c "import os, sys; print(os.path.realpath(sys.executable))"` in that environment.

```bash
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /path/to/python3
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp /path/to/python3
```

CI tests the server on Windows and Linux, not as a Mac server.

### On the client PC

1. Check that the port is reachable. `-k` skips certificate checks, so use it only for
   this test, which sends no credentials.

   ```powershell
   curl.exe -k https://192.168.1.20:8767/api/v1/health
   ```

   On Linux or Mac, use `curl` instead of `curl.exe`.

   A response containing `"status":"ok"` means the connection works.

2. In Job Manager, open the host settings and set **Backend** to **Job Server (HTTPS)**.
3. Set **Server URL** to `https://192.168.1.20:8767`.
4. Choose **Trust local server CA...**. Compare the displayed fingerprint with the one
   on the server PC, and choose **Yes** only if they match.
5. Choose **Sign in to Job Server...** and enter your username and password.

## Using the Web UI over local HTTPS

A browser shows a certificate warning at `https://192.168.1.20:8767` until the PC trusts
the server's CA. Do this on each PC that opens the Web UI. It is not needed for Tailscale,
which has a publicly trusted certificate, or for LAN HTTP, which has no certificate.

The certificate grants trust on that PC to anything this CA signs for private addresses
and localhost. Trust it only if you trust the server PC and everyone who can run jobs on
it. See [SECURITY.md](../SECURITY.md).

### 1. Get the certificate

On the server PC, open **Job Server > Settings > Create / show CA certificate...**, choose
**Export public CA certificate...**, and copy `job-server-ca.pem` to the other PC.

Alternatively, download it on the other PC from
`https://192.168.1.20:8767/api/v1/tls/ca`, passing the browser's warning for this one
download. Step 2 is then essential.

### 2. Check the fingerprint

Compare the result with the SHA-256 fingerprint on the server PC. Letter case and colons
do not matter.

- Windows: use the **last** `Cert Hash(sha256)` line of the output.

  ```powershell
  certutil -dump job-server-ca.pem
  ```

- Mac and Linux:

  ```bash
  openssl x509 -in job-server-ca.pem -noout -fingerprint -sha256
  ```

### 3. Install it

| Browser and OS | How to install |
|---|---|
| Edge or Chrome on Windows | Run `certutil -user -addstore Root job-server-ca.pem` and accept the Windows prompt. Not needed if **Trust local server CA...** in Job Manager already succeeded for this Windows user |
| Safari or Chrome on Mac | Run `security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db job-server-ca.pem` |
| Chrome on Linux | Run `certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "MoleditPy Job Server Local CA" -i job-server-ca.pem` (from `libnss3-tools`) |
| Firefox on any OS | **Settings > Privacy & Security > Certificates > View Certificates > Authorities > Import**, then select **Trust this CA to identify websites** |
| iPhone or iPad | Open the file, install the profile in **Settings**, then enable it under **General > About > Certificate Trust Settings** |
| Android | Under **Settings > Security**, install it as a **CA certificate**. The menu location varies by device |

### 4. Open the Web UI

Restart the browser and open `https://192.168.1.20:8767`. If the server PC's IP changes,
the CA stays the same, so only the URL changes.

To remove the trust on Windows, open `certmgr.msc` and delete **MoleditPy Job Server Local
CA** under **Trusted Root Certification Authorities**.

## Regenerating the CA

If the CA's key may have been exposed, choose **Job Server > Settings > Regenerate CA
certificate...**, then restart the server. Every PC and browser must then trust the new CA, as
above, and should remove the old one. The old files move to a `retired-` folder under the data
directory's `tls` folder.

## Using a .local name

`https://your-pc.local:8767` keeps working when the server PC's IP changes. It needs Job
Server 0.6.2 or later and a Job Manager release that accepts `.local` names. With **Allow trusted
LAN HTTP** on, `http://your-pc.local:8766` works too, unencrypted. Any device on the LAN can
answer for a `.local` name, so use HTTP only on a LAN whose devices you trust.

1. Check from the other PC that the name resolves: `ping your-pc.local`. Windows, Mac and
   Linux with Avahi announce the PC's name. A guest network or a managed network may block it.
2. On the server PC, also tick `your-pc.local` under **Reachable from other PCs at**. Keep the
   LAN IP ticked: the server listens on IP addresses, and the name leads to one of them.
3. Save and restart. The CA is limited to this one name, so a CA created without it, or for
   another name, needs replacing, and Settings asks first. Every
   PC and browser must then trust the new CA, following the steps above, and should remove the
   old one. The old files move to a `retired-` folder under the data directory's `tls` folder.
4. In Job Manager, set **Server URL** to `https://your-pc.local:8767` and choose **Trust local
   server CA...**.

## Tailscale

Both PCs must be signed in to the same tailnet. Use private Tailscale Serve only. Never
use Funnel or router port forwarding.

1. On the server PC, run the following and note the URL it prints:

   ```powershell
   tailscale serve --bg http://127.0.0.1:8766
   ```

2. Add that URL to **Job Server > Settings > Other web URLs**, save and restart.
3. In Job Manager, set **Server URL** to `https://your-pc.your-tailnet.ts.net`.
4. Choose **Sign in to Job Server...**. **Trust local server CA...** is not needed,
   because the certificate is publicly trusted.

## LAN HTTP

Passwords, tokens and job data can be read by anyone able to intercept LAN traffic. Use
this only on a trusted LAN.

1. On the server PC, enable **Allow trusted LAN HTTP**, tick the address under
   **Reachable from other PCs at**, save and restart.
2. Follow steps 7 to 9 of Local HTTPS, or [Server on Linux or Mac](#server-on-linux-or-mac),
   using port `8766` and, on Windows, a separate rule name.
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
| `origin_forbidden` in a browser | The address is not registered | Tick it in Settings, or add the exact origin to **Other web URLs**, and restart |
| A `.local` name does not resolve | The network blocks multicast, or the PC does not announce it | Use the IP address |
| A `.local` name resolves but times out | The name led to an address that is not ticked, or the firewall | Tick that address, or add a firewall rule for it |

If the server PC is managed by your organization, ask its administrator before changing
the network profile or firewall rules.
