# Connecting from a Mac

Use this page when a Mac cannot reach a Job Server over local HTTPS, from a web
browser or from MoleditPy's Job Manager. Set up the server first with
[Connecting from another PC](LAN_SETUP.md).

Replace these placeholders in the commands below:

| Placeholder | Meaning | Example |
|---|---|---|
| `SERVER` | The server's `.local` name or IP address, as you connect to it | `my-pc.local` |
| `SERVER_IP` | The server's LAN IP | `192.168.1.50` |
| `PORT` | The HTTPS port | `8767` |
| `PYTHON` | The Python that runs MoleditPy | `/path/to/python3` |

## Quick reference

| Symptom | Where | Cause | Fix |
|---|---|---|---|
| `ERR_CERT_AUTHORITY_INVALID`, or "This connection is not private" | Browser | The Mac does not trust the server's local CA | [Fix A](#fix-a-trust-the-ca-in-the-keychain-browsers) |
| `No route to host` (`Errno 65`) although `ping` works | MoleditPy | The app that started MoleditPy has no **Local Network** permission | [Fix B](#fix-b-allow-local-network-access-moleditpy) |
| `CERTIFICATE_VERIFY_FAILED` | MoleditPy | Python does not read the macOS keychain | [Fix C](#fix-c-give-job-manager-the-ca-moleditpy) |

## 1. Rule out the network

Run these in Terminal:

```bash
dscacheutil -q host -a name SERVER     # does the name resolve? (.local uses mDNS)
ping -c 2 SERVER                       # is the PC reachable?
nc -vz -G 3 SERVER_IP PORT             # is the port open?
curl -k https://SERVER:PORT/ -o /dev/null -w '%{http_code}\n'   # does the server answer? (-k skips TLS checks)
curl https://SERVER:PORT/ -o /dev/null                          # does TLS verify?
```

- **Everything works except the last `curl`** (`self signed certificate in certificate
  chain`): the server is fine and only trust is missing. Go to Fix A or Fix C.
- **`nc` reports "Connection refused" on an IPv6 address, then succeeds on IPv4:** normal.
  The server listens only on the addresses ticked under **Reachable from other PCs at**,
  and the client falls back to the next address.
- **`ping` fails:** a network problem such as a wrong IP, another subnet or VLAN, the
  server's firewall, or a sleeping server. See
  [Troubleshooting](LAN_SETUP.md#troubleshooting).

To see the certificates the server presents:

```bash
echo | openssl s_client -connect SERVER_IP:PORT -servername SERVER -showcerts 2>/dev/null | grep -E "^ *[0-9] s:|i:"
```

The server presents two:

```
0 s:CN=MoleditPy Job Server              i:CN=MoleditPy Job Server Local CA
1 s:CN=MoleditPy Job Server Local CA     i:CN=MoleditPy Job Server Local CA
```

The server certificate must name the address you connect with. Check it with:

```bash
echo | openssl s_client -connect SERVER_IP:PORT -servername SERVER 2>/dev/null | openssl x509 -noout -ext subjectAltName
```

If it is missing, tick that address or `.local` name on the server and restart the server.

## 2. Get the CA certificate and check it

Every fix below needs the CA certificate, not the server certificate. Get it as described
in [Get the certificate](LAN_SETUP.md#1-get-the-certificate): export it from the server
PC's Job Server window, or download it from `https://SERVER:PORT/api/v1/tls/ca`. On the
server PC it is also `tls/ca.pem` in the Job Server data folder.

**Check the fingerprint before trusting it**, especially when it came over the network.
It must match the SHA-256 fingerprint shown on the server PC:

```bash
openssl x509 -in job-server-ca.pem -noout -subject -fingerprint -sha256
```

Keep the file somewhere permanent, for example in `~/.moleditpy/job_manager/`.

## Fix A: Trust the CA in the keychain (browsers)

```bash
security add-trusted-cert -r trustRoot -p ssl -k ~/Library/Keychains/login.keychain-db job-server-ca.pem
```

macOS asks for your password. Alternatively, drag the file into **Keychain Access**, open
it, and set **Trust > Secure Sockets Layer (SSL)** to **Always Trust**.

To check it:

```bash
echo | openssl s_client -connect SERVER_IP:PORT -servername SERVER -showcerts 2>/dev/null | awk '/BEGIN CERT/,/END CERT/' > chain.pem
security verify-cert -c chain.pem -p ssl -s SERVER      # expect: ...certificate verification successful.
```

Quit the browser completely (Command-Q) and open it again. The sign-in page shows
"Server online" with the server's version.

- Safari and Chrome use the keychain. **Firefox has its own store**: **Settings > Privacy &
  Security > Certificates > View Certificates > Authorities > Import**.
- The system `curl` and Python **do not read the keychain**. For MoleditPy, see Fix C.

## Fix B: Allow Local Network access (MoleditPy)

**Symptom:** Job Manager reports `No route to host`, but `ping` and `nc` work.

**Cause:** Since macOS 15, connecting to a LAN address needs **Local Network** permission.
macOS checks it against the *responsible app*, the app that started the process, not
against `python3` itself. When MoleditPy is started from Terminal, iTerm, an editor's
terminal or any other app without that permission, every LAN connection fails. The same
command can succeed from another app that has the permission, which makes the cause hard
to see.

**Confirm.** Find the app that started MoleditPy:

```bash
p=$(pgrep -f moleditpy | head -1); while [ "$p" -gt 1 ]; do ps -o pid=,comm= -p $p; p=$(ps -o ppid= -p $p | tr -d ' '); done
```

Then run this **in that same app**:

```bash
PYTHON -c "import socket; socket.create_connection(('SERVER_IP', PORT), timeout=4); print('OK')"
```

`OSError: [Errno 65] No route to host` confirms the cause.

**Fix:**

1. Open **System Settings > Privacy & Security > Local Network** and turn on the app that
   started MoleditPy, for example **Terminal**. An app is listed only after its first LAN
   connection attempt; try again, or answer the prompt.
2. **Quit that app completely (Command-Q)**, open it again, and start MoleditPy again. The
   permission does not reach processes that are already running.
3. Run the one-line check again. It prints `OK`.

### When the app never appears in the list

Sometimes the prompt never appears, so the app is never listed and stays blocked. There is
no button to add an app by hand. This happens especially with programs Apple did not sign,
such as a conda `python3` started from Terminal, while Apple's own `curl`, `ping` and `nc`
still connect from the same window. Restarting the Mac sometimes brings the prompt back.

The `MoleditPy.app` that `moleditpy-installer` creates starts MoleditPy **through
Terminal**, so it depends on Terminal's permission and can hit the same block.

**Reliable fix: a launcher of its own.** A small app bundle that runs Python directly
becomes the responsible app itself: it gets its own prompt and its own entry in the Local
Network list. It needs the Xcode Command Line Tools (`xcode-select --install`).

Set `PY` to your `PYTHON` first:

```bash
PY=/path/to/python3
A=~/Applications/"MoleditPy Direct.app"; C="$A/Contents"
mkdir -p "$C/MacOS" "$C/Resources"
cp "$("$PY" -c 'import moleditpy,os;print(os.path.dirname(moleditpy.__file__))')/assets/icon.icns" "$C/Resources/icon.icns"

# A native launcher: it stays as the app's main process and runs run.sh as a child.
cat > /tmp/mlauncher.c <<'EOF'
#include <spawn.h>
#include <sys/wait.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mach-o/dyld.h>
#include <libgen.h>
extern char **environ;
int main(int argc, char **argv) {
    char exe[4096]; uint32_t sz = sizeof exe;
    if (_NSGetExecutablePath(exe, &sz) != 0) return 1;
    char script[4400];
    snprintf(script, sizeof script, "%s/../Resources/run.sh", dirname(exe));
    char **args = calloc(argc + 2, sizeof(char *));
    int n = 0;
    args[n++] = "/bin/bash"; args[n++] = script;
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "-psn_", 5) != 0) args[n++] = argv[i];
    pid_t pid;
    if (posix_spawn(&pid, "/bin/bash", NULL, NULL, args, environ) != 0) return 1;
    int st;
    while (waitpid(pid, &st, 0) < 0) {}
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
EOF
clang -O2 -mmacosx-version-min=11.0 -o "$C/MacOS/MoleditPy" /tmp/mlauncher.c

cat > "$C/Resources/run.sh" <<EOF
#!/bin/bash
export PATH="$(dirname "$PY"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LOG="\$HOME/Library/Logs/MoleditPy.log"
echo "=== \$(date '+%F %T') start ===" >> "\$LOG"
"$PY" -m moleditpy "\$@" >> "\$LOG" 2>&1
EOF
chmod +x "$C/Resources/run.sh"

cat > "$C/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key><string>MoleditPy Direct</string>
	<key>CFBundleIdentifier</key><string>local.moleditpy.direct</string>
	<key>CFBundleExecutable</key><string>MoleditPy</string>
	<key>CFBundleIconFile</key><string>icon</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleVersion</key><string>1.0</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>NSHighResolutionCapable</key><true/>
	<key>NSLocalNetworkUsageDescription</key><string>MoleditPy connects to Job Servers on your local network.</string>
</dict>
</plist>
EOF
codesign --force --sign - "$A"     # an ad-hoc signature, so macOS can identify the app
```

Then:

1. Open **MoleditPy Direct** from Finder or Launchpad. **Do not** start it from a
   terminal: the terminal would become the responsible app again.
2. At the first Job Server connection, click **Allow** in the "find and connect to devices
   on your local network" prompt.
3. Check that **MoleditPy Direct** is listed and **switched on** under **System Settings >
   Privacy & Security > Local Network**, then quit and open the app again.

The connection that raises the prompt fails with `No route to host` while the prompt is
waiting. If the prompt is dismissed or hidden behind other windows, the app is listed with
its switch **off**, and every later attempt fails until you turn it on.

**Use a compiled launcher, not a shell script.** With a bash script as
`CFBundleExecutable`, macOS can record a Local Network entry that never appears in System
Settings and is never answered, so access stays blocked. If you change the launcher, change
`CFBundleIdentifier` too, so that macOS treats it as a new app and asks again.

Its limits: console output goes to `~/Library/Logs/MoleditPy.log` instead of a terminal
window, and dropping files on the icon does not open them. Use **File > Open** in
MoleditPy instead.

| Launcher | How it starts MoleditPy | Job Server on the LAN | Other |
|---|---|---|---|
| `MoleditPy.app` from `moleditpy-installer` | Through Terminal | Only if Terminal has Local Network permission | Opens dropped files; output visible in Terminal |
| `MoleditPy Direct.app` (above) | Directly, as its own app | Yes, once allowed | Output in `~/Library/Logs/MoleditPy.log`; no file dropping |

Keeping both is harmless. Give them distinct names in the Dock so you start the right one.
Upgrading MoleditPy in the same Python does not affect the Direct launcher; rebuild it only
if that Python moves.

Without a launcher, you can also start MoleditPy from any app that is already listed and
switched on under Local Network.

## Fix C: Give Job Manager the CA (MoleditPy)

MoleditPy's Python checks certificates against its own CA bundle, **not** the macOS
keychain, so Fix A does not help Job Manager. A typical log line, for example when signing
in:

```
Job Manager: background task failed: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: self-signed certificate in certificate chain>
```

**Easiest: the button.** In Job Manager's host settings, click **Trust local server CA...**.
It downloads the server's CA and shows its SHA-256 fingerprint. **Compare it with the one
on the server PC** before you click **Yes**. Job Manager then saves the CA as
`~/.moleditpy/job_manager/identity/ca/<sha256>.pem`, fills in the CA file field and checks
the connection. Then sign in again. On Windows the button adds the CA to the current
user's trusted roots instead.

**By hand:** enter the path of your `job-server-ca.pem` in the host's **CA file** field.

Either way the path is saved as `server_ca_file` in
`~/.moleditpy/job_manager/settings.json`. Edit that file only while MoleditPy is closed;
a running MoleditPy can overwrite the change.

To check that this Python accepts the CA:

```bash
PYTHON -c "import ssl,socket; c=ssl.create_default_context(cafile='job-server-ca.pem'); s=c.wrap_socket(socket.create_connection(('SERVER_IP',PORT)),server_hostname='SERVER'); print('TLS OK', s.version())"
```

## Things to watch

- **The CA is kept until it is replaced.** It changes only when the server's
  administrator regenerates it, or when a new `.local` name needs a new CA. Then repeat
  section 2, Fix A and Fix C, and delete the old **MoleditPy Job Server Local CA** in
  Keychain Access. See [Regenerating the CA](LAN_SETUP.md#regenerating-the-ca).
- **Connect with a name the certificate lists.** An IP or name that is not in it fails
  verification even with the CA trusted.
- **Never expose the Job Server to the internet.** Use localhost, a private LAN over
  HTTPS, or a private Tailscale network. No port forwarding and no Tailscale Funnel.
