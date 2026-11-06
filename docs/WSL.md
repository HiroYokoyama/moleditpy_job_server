# Running Linux programs from Windows

You can launch a Linux program by putting `wsl.exe` in the command field of a
Windows-native Job Server destination. For example, with ORCA installed inside Ubuntu:

```powershell
wsl.exe -d Ubuntu --exec /opt/orca/orca {input}
```

Replace `Ubuntu` and `/opt/orca/orca` with your distribution and Linux executable.
`{input}` is the uploaded input filename; leave the placeholder unquoted because
the server quotes it. The WSL distribution must be available to the **Windows account
running Job Server**. For a remote server, this means WSL on that server, not on the
PC submitting the job. Check its distributions with `wsl.exe --list --verbose`.

WSL normally inherits the Windows command's working directory, so this example
uses the uploaded files in the Windows job directory through its mounted drive.
Explicit paths passed to Linux programs must use Linux syntax (`/home/...` or
`/mnt/c/...`), not `C:\...`. `--exec` directly launches the executable; if the
calculation needs Conda, MPI or other shell setup, put that setup in a Linux wrapper
script and invoke the script instead. See Microsoft's
[Windows/WSL interoperability documentation](https://learn.microsoft.com/en-us/windows/wsl/filesystems).

**Calling `wsl.exe` from a Windows job does not establish WSL process-group management.**
Cancellation and walltime enforcement in Windows mode terminate the Windows process
tree; do not assume every Linux descendant stops. Files written elsewhere inside WSL
are also outside that Windows job's result directory and storage checks.

For the server's WSL execution adapter, set these fields in its existing settings,
then restart the daemon:

```json
{
  "executor": "wsl",
  "wsl_distribution": "Ubuntu",
  "wsl_root": "/home/jobserver/.moleditpy/job_server"
}
```

Use a `/home/...` root writable by the distribution's execution user. This mode
places inputs/results in the Linux filesystem and uses `setsid` plus Linux
process-group signals for cancellation. Submit Linux commands directly, such as
`/opt/orca/orca {input} > {stem}.out`, without another `wsl.exe` prefix. Job Manager
selects the Bash scheduler when registering with this server. Execution mode is
**server-wide**; each job cannot independently switch a Windows server to WSL.

Verified on Windows with the Ubuntu WSL distribution: synthetic job submission,
normal and failed completion, input/result transfer, roots containing spaces,
and cleanup of background descendants after completion. Wrappers use LF line
endings for Bash. ORCA calculations and live cancellation on this adapter have
not been verified; native Windows/Linux tests cover cancellation separately.

For WSL on your own PC without a shared server, Job Manager also offers
**Hosts → This machine, inside WSL (no SSH)** with a distribution selector.
