# Accounts

Every action here is available in the desktop window and the Web UI. In the desktop
window, right-click a user or device, or use the **Account** menu. In the Web UI, use
**My account** and, for administrators, **Users & devices**.

## Roles

| | User | Administrator |
|---|---|---|
| Submit jobs and read their own | Yes | Yes |
| Change their own password, revoke their own devices | Yes | Yes |
| See and control every job, pause the queue | | Yes |
| Manage users and devices, read the activity log | | Yes |

## Actions

| Action | What happens |
|---|---|
| **Add user** | Creates a user or an administrator. |
| **Change my password** | Needs the current password. Your other sign-ins end. This one stays. You can also revoke your other devices. |
| **Reset password** | An administrator sets a new password for someone else. Their sign-ins end. Their devices keep working unless you also revoke them, which you should if the account may be compromised. |
| **Disable / Enable** | Disabling stops sign-ins and devices at once. Queued and running jobs are not cancelled. Enabling restores the password and devices, but no old session. |
| **Make administrator / Make regular user** | Applies at once and ends that user's sessions. |
| **Delete** | Permanent, and you must type the username. See below. |
| **Revoke device** | Permanent. The device has to register again with a password. |

Administrators cannot disable, demote, reset or delete their own account. So at least one
enabled administrator always remains, and a stolen session cannot skip the current password.

### Deleting a user

A user with queued or running jobs cannot be deleted. Cancel those jobs, let them finish,
or disable the account instead.

Deleting removes the user's name, password, devices and sessions, and the username becomes
free. Finished jobs and their files stay on the server with a deleted owner, and
administrators can still open them. Someone who later takes the same username gets none
of the old account's jobs. The activity log keeps the name that was deleted.

## Passwords and devices

Passwords are stored as salted scrypt hashes and are never logged. Password attempts are
limited to 20 a minute, per address and per account. A wrong current password does not end
your session.

The server accepts passwords of 1 to 1024 characters and does not enforce a strength
policy. Choose a strong one for any server reachable from another PC, and use HTTPS: over
plain LAN HTTP, passwords can be read on the network.

Desktop clients sign in with a device key instead of a saved password. A window whose
device was revoked offers to register again, and replaces its key only after the server
accepts the new device.

## Forgotten password

Run this on the server PC, which works while the server is running:

```sh
moleditpy-job-server reset-password alice
```

Add `--revoke-devices` to revoke that account's devices as well. It needs access to the
server's data folder, and there is no network equivalent.

## Activity log

**Activity** in the desktop window and **Recent activity** in the Web UI show sign-ins,
account changes and job events, newest first. The log records who acted and on what, and
never records a password. It is not deleted automatically.
