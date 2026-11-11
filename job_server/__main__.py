import argparse
import getpass
import json
import sys
from pathlib import Path

from .settings import Settings, pc_resources


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
            child.add_argument("--lan-address", help="private LAN address to listen on")
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
        if name == "reset-password":
            child.add_argument("username")
            child.add_argument(
                "--revoke-devices", action="store_true",
                help="also revoke every device registered to this account",
            )
    args = parser.parse_args()
    try:
        if args.action == "init":
            path = args.config or Path.home() / ".moleditpy" / "job_server" / "settings.json"
            if path.exists():
                raise RuntimeError("Configuration already exists")
            cpus, memory_mb = pc_resources()
            settings = Settings(
                cpus=args.cpus or cpus,
                memory_mb=args.memory_mb or memory_mb,
                lan_bind_address=args.lan_address or "",
                lan_extra_addresses=args.extra_address,
                lan_hostname=args.hostname,
                lan_tls=args.tls,
                allow_lan_http=args.lan_http,
            )
            if args.port is not None:
                settings.port = args.port
            if args.tls_port is not None:
                settings.tls_port = args.tls_port
            try:
                settings.validate()
            except ValueError as exc:
                if args.tls or args.lan_http:
                    from .listeners import available_lan_addresses

                    available = available_lan_addresses()
                    if available:
                        raise ValueError(
                            f"{exc}. Private addresses on this PC: {', '.join(available)}"
                        ) from exc
                raise
            settings.save(path)
            print(f"Wrote {path}")
            if settings.lan_tls:
                # Create the CA during setup, not at the first start, so its
                # fingerprint can be shared with client PCs right away.
                from .tls import ca_fingerprint, ensure_certificates

                ensure_certificates(settings)
                print(f"Local CA created. SHA-256 fingerprint: {ca_fingerprint(settings)}")
                print("Give this fingerprint to each client PC; it confirms the CA there.")
            for url in settings.connection_urls:
                print(f"Will serve: {url}")
            print("Next: create-admin, then serve. No restart is needed after this setup.")
            return 0
        settings = Settings.load(args.config)
        if args.action == "serve":
            from .server_app import serve

            serve(settings)
        elif args.action == "status":
            from .client import JobServerClient

            client = JobServerClient(f"http://127.0.0.1:{settings.port}", timeout=3)
            print(json.dumps(client.request("GET", "/health", authenticated=False)))
        elif args.action == "create-admin":
            from .auth import Auth
            from .db import Database
            # Bootstrap uses local filesystem access and an atomic SQLite guard.
            # It does not require stopping an already-running daemon.
            db = Database(settings.root)
            try:
                if db.one("SELECT 1 FROM users WHERE role='admin' AND enabled=1"):
                    raise RuntimeError(
                        "An administrator already exists; use the authenticated admin API"
                    )
                username = input("Administrator username: ").strip()
                password = getpass.getpass("Initial password: ")
                if password != getpass.getpass("Repeat password: "):
                    raise ValueError("Passwords differ")
                print(json.dumps(Auth(db, settings).create_initial_admin(username, password)))
            finally:
                db.close()
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
