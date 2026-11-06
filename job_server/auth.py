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
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        reject(400, "password_policy", "Password must contain 1–1024 characters")
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

    def create_initial_admin(self, username, password):
        """Local bootstrap only; never expose this operation through HTTP."""
        return self.create_user(username, password, "admin", initial_admin=True)

    def create_user(
        self, username, password, role="user", actor=None, *, principal=None, initial_admin=False
    ):
        if (
            not isinstance(username, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username)
            or role not in {"admin", "user"}
        ):
            reject(400, "invalid_user", "Invalid username or role")
        encoded = password_hash(password)
        identifier = str(uuid.uuid4())
        with self.db.transaction() as c:
            if initial_admin and c.execute(
                "SELECT 1 FROM users WHERE role='admin' AND enabled=1"
            ).fetchone():
                reject(
                    409, "admin_exists",
                    "An administrator already exists. Sign in with that administrator account.",
                )
            if principal is not None:
                actor = self.admin(principal)["user_id"]
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

    def login(self, username, password, *, web=False):
        user = self.db.one("SELECT * FROM users WHERE username=?", (username,))
        if (
            not verify_password(password, user["password_hash"] if user else self.dummy_hash)
            or not user
            or not user["enabled"]
        ):
            reject(401, "invalid_credentials", "Invalid credentials")
        with self.db.transaction() as c:
            # Password verification is deliberately outside the write lock. A
            # disable/password change that completed meanwhile must win.
            if not c.execute(
                "SELECT 1 FROM users WHERE id=? AND enabled=1 AND password_hash=?",
                (user["id"], user["password_hash"]),
            ).fetchone():
                reject(401, "invalid_credentials", "Invalid credentials")
            self.db.event(c, "login", user["id"])
            c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            if web:
                return self.issue(c, user["id"], scope="web", lifetime=min(self.settings.token_seconds, 900))
            return self.issue(c, user["id"], scope="enrollment", lifetime=300)

    def logout(self, principal):
        with self.db.transaction() as c:
            principal = self.current(principal)
            c.execute("DELETE FROM sessions WHERE digest=?", (principal["digest"],))

    @staticmethod
    def _revoke_user_devices(c, user_id, keep=None):
        """Revoke a user's devices, optionally keeping the one making the request."""
        return c.execute(
            "UPDATE devices SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL "
            "AND id IS NOT ?",
            (time.time(), user_id, keep),
        ).rowcount

    def change_password(self, principal, current, new, *, revoke_devices=False):
        """Change the caller's own password; only proof of the old one authorizes it."""
        # Per account as well as per address, so one stolen token cannot guess quickly.
        self.rate_limit("password:" + principal["user_id"])
        user = self.db.one("SELECT password_hash FROM users WHERE id=?", (principal["user_id"],))
        if not verify_password(current, user["password_hash"] if user else self.dummy_hash) or not user:
            # Not 401: a mistyped password must not end the signed-in session.
            reject(403, "invalid_password", "The current password is incorrect")
        if new == current:
            reject(400, "password_unchanged", "Choose a password different from the current one")
        encoded = password_hash(new)
        with self.db.transaction() as c:
            # Verified outside the write lock: a disable or another change that
            # completed meanwhile must win.
            principal = self.current(principal)
            changed = c.execute(
                "UPDATE users SET password_hash=? WHERE id=? AND enabled=1 AND password_hash=?",
                (encoded, principal["user_id"], user["password_hash"]),
            ).rowcount
            if not changed:
                reject(409, "password_changed", "The password changed meanwhile; try again")
            c.execute(
                "DELETE FROM sessions WHERE user_id=? AND digest<>?",
                (principal["user_id"], principal["digest"]),
            )
            revoked = (
                self._revoke_user_devices(c, principal["user_id"], principal["device_id"])
                if revoke_devices else 0
            )
            self.db.event(c, "password_changed", principal["user_id"], devices_revoked=revoked)
        return {"changed": True, "devices_revoked": revoked}

    def reset_password(self, user_id, new, *, principal=None, local=False, revoke_devices=False):
        """Replace another user's password. `local` is the on-server CLI recovery only."""
        if principal is None and not local:
            raise ValueError("A password reset needs an administrator or local access")
        encoded = password_hash(new)
        with self.db.transaction() as c:
            actor = None
            if principal is not None:
                principal = self.admin(principal)
                actor = principal["user_id"]
                if user_id == actor:
                    # Otherwise a stolen administrator token could skip the old password.
                    reject(409, "self_reset", "Use Change password for your own account")
            if not c.execute(
                "UPDATE users SET password_hash=? WHERE id=?", (encoded, user_id)
            ).rowcount:
                reject(404, "not_found", "User not found")
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            revoked = self._revoke_user_devices(c, user_id) if revoke_devices else 0
            self.db.event(
                c, "password_reset", actor, user_id=user_id, devices_revoked=revoked, local=local,
            )
        return {"reset": True, "devices_revoked": revoked}

    def set_enabled(self, user_id, enabled, principal):
        if not enabled and user_id == principal["user_id"]:
            reject(409, "self_disable", "Use another administrator to disable your own account")
        with self.db.transaction() as c:
            principal = self.admin(principal)
            if not c.execute(
                "UPDATE users SET enabled=? WHERE id=?", (int(enabled), user_id)
            ).rowcount:
                reject(404, "not_found", "User not found")
            if not enabled:
                c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self.db.event(
                c, "user_enabled" if enabled else "user_disabled", principal["user_id"],
                user_id=user_id,
            )

    def set_role(self, user_id, role, principal):
        # The actor stays an administrator, so at least one always remains.
        if user_id == principal["user_id"]:
            reject(409, "self_role", "Use another administrator to change your own role")
        with self.db.transaction() as c:
            principal = self.admin(principal)
            if not c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id)).rowcount:
                reject(404, "not_found", "User not found")
            # Sessions read the role live; ending them makes the change visible at once.
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self.db.event(c, "user_role_changed", principal["user_id"], user_id=user_id, role=role)

    def principal(self, token, scope="access", *, allow_web=False):
        if not isinstance(token, str) or len(token) > 256:
            reject(401, "unauthenticated", "Authentication required")
        return self._session(hashlib.sha256(token.encode()).hexdigest(), scope, allow_web=allow_web)

    def current(self, principal):
        """Revalidate a snapshot under the caller's mutation transaction."""
        return self._session(principal["digest"], principal["scope"])

    def _session(self, digest, scope, *, allow_web=False):
        result = self.db.one(
            """SELECT s.*,u.role,u.username,u.enabled,d.revoked_at FROM sessions s
                              JOIN users u ON u.id=s.user_id LEFT JOIN devices d ON d.id=s.device_id
                              WHERE digest=?""",
            (digest,),
        )
        if (
            not result
            or not result["enabled"]
            or result["expires"] <= time.time()
            or (result["scope"] != scope and not (allow_web and scope == "access" and result["scope"] == "web"))
            or (result["scope"] == "web" and not self.settings.web_ui)
            or result["revoked_at"]
        ):
            reject(401, "unauthenticated", "Credential expired or revoked")
        return result

    def admin(self, principal):
        principal = self.current(principal)
        if principal["role"] != "admin":
            reject(403, "admin_required", "Administrator required")
        self.fresh(principal)
        return principal

    def fresh(self, principal):
        if time.time() - principal["authenticated_at"] > 300:
            reject(401, "reauthenticate", "Sign in again for this operation")

    def enroll(self, principal, device_id, public_key):
        if principal["scope"] == "web":
            reject(403, "device_registration_forbidden", "Register persistent devices from the desktop client")
        self.fresh(principal)
        try:
            uuid.UUID(device_id)
            key = base64.b64decode(public_key, validate=True)
            Ed25519PublicKey.from_public_bytes(key)
        except (ValueError, TypeError, AttributeError):
            reject(400, "invalid_key", "A UUID device ID and raw Ed25519 public key are required")
        with self.db.transaction() as c:
            # Recheck under the write lock: concurrent use of a one-use enrollment
            # session must not register multiple persistent keys.
            principal = self.current(principal)
            self.fresh(principal)
            if c.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
                reject(409, "device_exists", "Device already registered")
            c.execute(
                "INSERT INTO devices(id,user_id,public_key,created_at) VALUES (?,?,?,?)",
                (device_id, principal["user_id"], public_key, time.time()),
            )
            self.db.event(c, "device_enrolled", principal["user_id"], device_id=device_id)
            if principal["scope"] == "enrollment":
                c.execute("DELETE FROM sessions WHERE digest=?", (principal["digest"],))
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

    def revoke(self, device_id, actor, *, principal=None):
        with self.db.transaction() as c:
            device = c.execute("SELECT user_id FROM devices WHERE id=?", (device_id,)).fetchone()
            if principal is not None:
                principal = self.current(principal)
                if principal["role"] == "admin":
                    self.admin(principal)
                elif device and device["user_id"] != principal["user_id"]:
                    reject(404, "not_found", "Device not found")
                actor = principal["user_id"]
            if not device:
                reject(404, "not_found", "Device not found")
            c.execute("UPDATE devices SET revoked_at=? WHERE id=?", (time.time(), device_id))
            c.execute("DELETE FROM sessions WHERE device_id=?", (device_id,))
            self.db.event(c, "device_revoked", actor, device_id=device_id)
