import argparse
import getpass
import json
import sys
from pathlib import Path

from .settings import Settings, pc_resources


def _command(action, args):
    """The command line for a next step, carrying a non-default --config along."""
    config = getattr(args, "config", None)
    return f"moleditpy-job-server {action}" + (f' --config "{config}"' if config else "")


def _ask_admin(username=None):
    """Ask for the first administrator's credentials; a password never comes from argv."""
    from .auth import MIN_PASSWORD_LENGTH

    if username is None:
        username = input("Administrator username: ").strip()
    password = getpass.getpass("Initial password: ")
    if password != getpass.getpass("Repeat password: "):
        raise ValueError("Passwords differ")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"The password needs at least {MIN_PASSWORD_LENGTH} characters")
    return username, password


def _create_admin(settings, credentials=None):
    from .auth import Auth
    from .db import Database

    # Bootstrap uses local filesystem access and an atomic SQLite guard.
    # It does not require stopping an already-running daemon.
    db = Database(settings.root)
    try:
        if db.one("SELECT 1 FROM users WHERE role='admin' AND enabled=1"):
            raise RuntimeError("An administrator already exists; use the authenticated admin API")
        # Asked only once it is known that the answer will be used.
        username, password = credentials or _ask_admin()
        return Auth(db, settings).create_initial_admin(username, password)
    finally:
        db.close()


def _init(args):
    """Write a complete first configuration, so the first start is the only start."""
    path = args.config or Path.home() / ".moleditpy" / "job_server" / "settings.json"
    if path.exists():
        raise RuntimeError(
            f"Configuration already exists: {path}. Edit it there or in the desktop window, "
            "or remove it to set up from the beginning."
        )
    lan = args.tls or args.lan_http
    address = args.lan_address or ""
    if not lan and (address or args.extra_address or args.hostname):
        raise ValueError(
            "A LAN address or name needs a listener: add --tls (recommended) or --lan-http"
        )
    if lan and not address:
        from .listeners import available_lan_addresses

        available = available_lan_addresses()
        if len(available) != 1:
            raise ValueError(
                "Choose the address other PCs reach this one at with --lan-address: "
                + ", ".join(available)
                if available
                else "This PC has no private LAN address to listen on; connect it to the LAN first"
            )
        # Only one candidate, so asking for it would be a formality.
        address = available[0]
        print(f"Using this PC's private address {address}")
    cpus, memory_mb = pc_resources()
    settings = Settings(
        cpus=cpus if args.cpus is None else args.cpus,
        memory_mb=memory_mb if args.memory_mb is None else args.memory_mb,
        lan_bind_address=address,
        lan_extra_addresses=args.extra_address,
        lan_hostname=args.hostname,
        lan_tls=args.tls,
        allow_lan_http=args.lan_http,
    )
    for key, value in (("port", args.port), ("tls_port", args.tls_port), ("data_dir", args.data_dir)):
        if value is not None:
            setattr(settings, key, value)
    settings.validate()
    # Ask before anything is written: a mistyped password then leaves nothing behind.
    admin = _ask_admin(args.admin) if args.admin else None
    settings.save(path)
    print(f"Wrote {path}")
    if settings.lan_tls:
        # Create the CA during setup, not at the first start, so its
        # fingerprint can be shared with client PCs right away.
        from .tls import ca_fingerprint, ensure_certificates

        try:
            ensure_certificates(settings)
        except Exception:
            path.unlink(missing_ok=True)  # keep a failed setup repeatable
            raise
        print(f"Local CA created. SHA-256 fingerprint: {ca_fingerprint(settings)}")
        print("Give this fingerprint to each client PC; it confirms the CA there.")
    for url in settings.connection_urls:
        print(f"Will serve: {url}")
    if not settings.connection_urls:
        print("Local only: no LAN listener. Add --tls for other PCs on the LAN.")
    if admin:
        try:
            _create_admin(settings, admin)
        except Exception as exc:
            raise RuntimeError(
                f"{exc}. The configuration is written; finish with: {_command('create-admin', args)}"
            ) from exc
        print(f"Administrator {admin[0]} created.")
        print(f"Next: {_command('serve', args)}")
    else:
        print(f"Next: {_command('create-admin', args)}, then {_command('serve', args)}")
    print("No restart is needed after this setup.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="MoleditPy Job Server — LAN computational helper")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("serve", "status", "init", "create-admin", "reset-password"):
        child = sub.add_parser(name)
        child.add_argument("--config", type=Path, default=argparse.SUPPRESS)
        if name == "init":
            # First-run choices belong here, before the first start, so a new
            # server does not need a start/stop/restart cycle per setting.
            child.add_argument(
                "--lan-address",
                help="private LAN address to listen on (default: this PC's, if it has only one)",
            )
            child.add_argument(
                "--extra-address", action="append", default=[],
                help="additional private LAN address (repeatable)",
            )
            child.add_argument(
                "--tls", action="store_true",
                help="serve the LAN address over local HTTPS and create the CA now",
            )
            child.add_argument(
                "--lan-http", action="store_true",
                help="also serve the LAN address over plain HTTP",
            )
            child.add_argument("--hostname", default="", help="this PC's .local name")
            child.add_argument("--port", type=int, help="local/LAN HTTP port")
            child.add_argument("--tls-port", type=int, help="local HTTPS port")
            child.add_argument("--cpus", type=int, help="CPU budget (default: this PC)")
            child.add_argument("--memory-mb", type=int, help="memory budget in MB (default: this PC)")
            child.add_argument("--data-dir", help="data folder (default: ~/.moleditpy/job_server)")
            child.add_argument(
                "--admin", metavar="USERNAME",
                help="also create the first administrator; the password is asked for, never passed",
            )
        if name == "reset-password":
            child.add_argument("username")
            child.add_argument(
                "--revoke-devices", action="store_true",
                help="also revoke every device registered to this account",
            )
    args = parser.parse_args()
    try:
        if args.action == "init":
            return _init(args)
        settings = Settings.load(args.config)
        if args.action == "serve":
            from .server_app import serve

            serve(settings)
        elif args.action == "status":
            from .client import JobServerClient
            from .lifecycle import directory_is_idle

            if not settings.root.exists() or directory_is_idle(settings.root):
                raise RuntimeError(f"Server is OFF. Start it with: {_command('serve', args)}")
            client = JobServerClient(f"http://127.0.0.1:{settings.port}", timeout=3)
            print(json.dumps(client.request("GET", "/health", authenticated=False)))
        elif args.action == "create-admin":
            print(json.dumps(_create_admin(settings)))
        elif args.action == "reset-password":
            from .auth import Auth
            from .db import Database
            # Recovery for a forgotten password. Write access to the data folder on
            # the server PC already implies this authority; never expose it over HTTP.
            db = Database(settings.root)
            try:
                user = db.one("SELECT id FROM users WHERE username=?", (args.username,))
                if not user:
                    raise RuntimeError("No such user")
                password = getpass.getpass("New password: ")
                if password != getpass.getpass("Repeat password: "):
                    raise ValueError("Passwords differ")
                print(json.dumps(Auth(db, settings).reset_password(
                    user["id"], password, local=True, revoke_devices=args.revoke_devices,
                )))
            finally:
                db.close()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
