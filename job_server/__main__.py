import argparse
import getpass
import json
import sys
from pathlib import Path

from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description="MoleditPy Job Server — Trusted Lab Preview")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("serve", "status", "init", "create-admin"):
        child = sub.add_parser(name)
        child.add_argument("--config", type=Path, default=argparse.SUPPRESS)
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
            from .instance import InstanceLock

            # Bootstrap is local, interactive, and unavailable while the daemon owns the DB.
            lock = InstanceLock(settings.root).acquire()
            db = Database(settings.root)
            try:
                if db.one("SELECT 1 FROM users WHERE role='admin' AND enabled=1"):
                    raise RuntimeError(
                        "An administrator already exists; use the authenticated admin API"
                    )
                username = input("Administrator username: ").strip()
                password = getpass.getpass("Initial password (12+ characters): ")
                if password != getpass.getpass("Repeat password: "):
                    raise ValueError("Passwords differ")
                print(json.dumps(Auth(db, settings).create_user(username, password, "admin")))
            finally:
                db.close()
                lock.release()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
