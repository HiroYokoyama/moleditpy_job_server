import argparse
import getpass
import json
import sys
from pathlib import Path

from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description="MoleditPy Job Server — LAN computational helper")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("serve", "status", "init", "create-admin", "reset-password"):
        child = sub.add_parser(name)
        child.add_argument("--config", type=Path, default=argparse.SUPPRESS)
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
            Settings().validate().save(path)
            print(f"Wrote {path}; listener remains OFF. Next: create-admin, then serve.")
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
