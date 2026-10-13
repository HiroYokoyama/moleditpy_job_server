import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .errors import reject


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        reject(400, "password_policy", "Password must contain 12–1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    )
    return base64.b64encode(salt + digest).decode()


def verify_password(password, encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        calculated = hashlib.scrypt(
            password.encode(), salt=raw[:16], n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
        )
        return hmac.compare_digest(raw[16:], calculated)
    except (ValueError, TypeError, AttributeError):
        return False


def signing_message(server_id, device_id, nonce):
    return json.dumps(
        {
            "purpose": "moleditpy-job-server-access",
            "protocol_version": "1",
            "server_id": server_id,
            "device_id": device_id,
            "nonce": nonce,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class Auth:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.attempts = defaultdict(deque)
        self.rate_lock = threading.Lock()
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))

    def rate_limit(self, address):
        with self.rate_lock:
            now = time.monotonic()
            if len(self.attempts) > 4096:
                self.attempts = defaultdict(
                    deque, {k: v for k, v in self.attempts.items() if v and v[-1] > now - 60}
                )
            history = self.attempts[address]
            while history and history[0] < now - 60:
                history.popleft()
            if len(history) >= 20:
                reject(429, "rate_limit", "Authentication rate limit exceeded")
            history.append(now)

    def create_user(self, username, password, role="user", actor=None):
        if (
            not isinstance(username, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username)
            or role not in {"admin", "user"}
        ):
            reject(400, "invalid_user", "Invalid username or role")
        encoded = password_hash(password)
        identifier = str(uuid.uuid4())
        with self.db.transaction() as c:
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                reject(409, "user_exists", "Username already exists")
            c.execute(
                "INSERT INTO users VALUES (?,?,?,?,1,?)",
                (identifier, username, encoded, role, time.time()),
            )
            self.db.event(c, "user_created", actor, user_id=identifier, role=role)
        return {"id": identifier, "username": username, "role": role}

    def issue(self, c, user_id, device_id=None, scope="access", lifetime=None):
        token = secrets.token_urlsafe(32)
        expires = time.time() + (lifetime or self.settings.token_seconds)
        c.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user_id,
                device_id,
                scope,
                expires,
                time.time(),
            ),
        )
        return {"token": token, "expires_at": expires, "scope": scope}

    def login(self, username, password):
        user = self.db.one("SELECT * FROM users WHERE username=?", (username,))
        if (
            not verify_password(password, user["password_hash"] if user else self.dummy_hash)
            or not user
            or not user["enabled"]
        ):
            reject(401, "invalid_credentials", "Invalid credentials")
        with self.db.transaction() as c:
            self.db.event(c, "login", user["id"])
            return self.issue(c, user["id"], scope="enrollment", lifetime=300)

    def principal(self, token, scope="access"):
        if not isinstance(token, str) or len(token) > 256:
            reject(401, "unauthenticated", "Authentication required")
        result = self.db.one(
            """SELECT s.*,u.role,u.username,u.enabled,d.revoked_at FROM sessions s
                              JOIN users u ON u.id=s.user_id LEFT JOIN devices d ON d.id=s.device_id
                              WHERE digest=?""",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
        if (
            not result
            or not result["enabled"]
            or result["expires"] <= time.time()
            or result["scope"] != scope
            or result["revoked_at"]
        ):
            reject(401, "unauthenticated", "Credential expired or revoked")
        return result

    def admin(self, principal):
        if principal["role"] != "admin":
            reject(403, "admin_required", "Administrator required")
        if time.time() - principal["authenticated_at"] > 300:
            reject(401, "reauthenticate", "Repeat device authentication for this operation")

    def enroll(self, principal, device_id, public_key):
        try:
            uuid.UUID(device_id)
            key = base64.b64decode(public_key, validate=True)
            Ed25519PublicKey.from_public_bytes(key)
        except (ValueError, TypeError, AttributeError):
            reject(400, "invalid_key", "A UUID device ID and raw Ed25519 public key are required")
        with self.db.transaction() as c:
            if c.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
                reject(409, "device_exists", "Device already registered")
            c.execute(
                "INSERT INTO devices(id,user_id,public_key,created_at) VALUES (?,?,?,?)",
                (device_id, principal["user_id"], public_key, time.time()),
            )
            self.db.event(c, "device_enrolled", principal["user_id"], device_id=device_id)
        return {"device_id": device_id}

    def challenge(self, device_id):
        device = self.db.one(
            "SELECT d.*,u.enabled FROM devices d JOIN users u ON u.id=d.user_id WHERE d.id=?",
            (device_id,),
        )
        if not device or device["revoked_at"] or not device["enabled"]:
            reject(401, "invalid_device", "Device unavailable")
        nonce = secrets.token_urlsafe(32)
        expires = time.time() + self.settings.challenge_seconds
        with self.db.transaction() as c:
            c.execute("DELETE FROM challenges WHERE expires<?", (time.time(),))
            c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            c.execute("INSERT INTO challenges VALUES (?,?,?)", (nonce, device_id, expires))
        return {
            "nonce": nonce,
            "expires_at": expires,
            "server_id": self.db.metadata("server_id"),
            "protocol_version": "1",
        }

    def token(self, device_id, nonce, signature):
        with self.db.transaction() as c:
            challenge = c.execute(
                "SELECT * FROM challenges WHERE nonce=? AND device_id=?", (nonce, device_id)
            ).fetchone()
            c.execute("DELETE FROM challenges WHERE nonce=?", (nonce,))
            device = c.execute(
                "SELECT d.*,u.enabled FROM devices d JOIN users u ON u.id=d.user_id WHERE d.id=?",
                (device_id,),
            ).fetchone()
            valid = bool(
                challenge
                and challenge["expires"] > time.time()
                and device
                and device["enabled"]
                and not device["revoked_at"]
            )
            if valid:
                try:
                    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(device["public_key"]))
                    key.verify(
                        base64.b64decode(signature, validate=True),
                        signing_message(self.db.metadata("server_id"), device_id, nonce),
                    )
                except (ValueError, InvalidSignature, TypeError):
                    valid = False
            if valid:
                c.execute("UPDATE devices SET last_access=? WHERE id=?", (time.time(), device_id))
                self.db.event(c, "device_authenticated", device["user_id"], device_id=device_id)
                result = self.issue(c, device["user_id"], device_id)
        # Consume a failed signature too; raising inside the transaction would undo the DELETE.
        if not valid:
            reject(401, "invalid_signature", "Invalid or expired challenge signature")
        return result

    def revoke(self, device_id, actor):
        with self.db.transaction() as c:
            if not c.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
                reject(404, "not_found", "Device not found")
            c.execute("UPDATE devices SET revoked_at=? WHERE id=?", (time.time(), device_id))
            c.execute("DELETE FROM sessions WHERE device_id=?", (device_id,))
            self.db.event(c, "device_revoked", actor, device_id=device_id)
