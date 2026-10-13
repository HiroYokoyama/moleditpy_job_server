import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS devices (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), public_key TEXT NOT NULL,
 created_at REAL NOT NULL, revoked_at REAL, last_access REAL);
CREATE TABLE IF NOT EXISTS sessions (
 digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT, scope TEXT NOT NULL,
 expires REAL NOT NULL, authenticated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS challenges (
 nonce TEXT PRIMARY KEY, device_id TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), spec TEXT NOT NULL,
 state TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, ended_at REAL,
 exit_code INTEGER, pid INTEGER, process_started REAL, cancel_requested INTEGER DEFAULT 0,
 reason TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS job_files (
 job_id TEXT NOT NULL REFERENCES jobs(id), path TEXT NOT NULL, size INTEGER NOT NULL,
 sha256 TEXT NOT NULL, complete INTEGER DEFAULT 0, PRIMARY KEY(job_id,path));
CREATE TABLE IF NOT EXISTS idempotency_keys (
 user_id TEXT NOT NULL, key TEXT NOT NULL, job_id TEXT NOT NULL, digest TEXT NOT NULL,
 PRIMARY KEY(user_id,key));
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, actor TEXT, timestamp REAL NOT NULL,
 event TEXT NOT NULL, details TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS job_state ON jobs(state,created_at);
"""


class Database:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(root / "server.db", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.executescript(SCHEMA)
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('server_id',?)", (str(uuid.uuid4()),)
            )
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('paused','false')")
            self.connection.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def rows(self, query, parameters=()):
        with self.lock:
            return [dict(row) for row in self.connection.execute(query, parameters).fetchall()]

    def one(self, query, parameters=()):
        rows = self.rows(query, parameters)
        return rows[0] if rows else None

    def metadata(self, key):
        return self.one("SELECT value FROM metadata WHERE key=?", (key,))["value"]

    @staticmethod
    def event(connection, event, actor=None, job_id=None, **details):
        connection.execute(
            "INSERT INTO events(job_id,actor,timestamp,event,details) VALUES (?,?,?,?,?)",
            (job_id, actor, time.time(), event, json.dumps(details)),
        )

    def close(self):
        with self.lock:
            self.connection.close()
