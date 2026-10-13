import json
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.exceptions import HTTPException

from . import PLUGIN_VERSION
from .auth import Auth
from .core import build_wrapper
from .db import Database
from .errors import ServerError, reject
from .files import contained, open_regular, sha256
from .instance import InstanceLock
from .jobs import Jobs
from .scheduler import Scheduler
from .schemas import Challenge, Enrollment, JobSpec, Login, Pause, Token, UserCreate
from .settings import Settings


def create_app(settings=None, dispatch=True):
    settings = (settings or Settings()).validate()
    db = Database(settings.root)
    auth, jobs = Auth(db, settings), Jobs(db, settings)
    scheduler = Scheduler(db, jobs, settings)
    lock = InstanceLock(settings.root)

    @asynccontextmanager
    async def lifespan(app):
        lock.acquire()
        try:
            if dispatch:
                scheduler.start()
            yield
        finally:
            scheduler.stop()
            lock.release()
            db.close()

    app = FastAPI(
        title="MoleditPy Job Server",
        version=PLUGIN_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.db, app.state.auth, app.state.jobs, app.state.scheduler = db, auth, jobs, scheduler
    app.state.settings = settings
    if settings.web_ui:
        web_root = Path(__file__).parent / "web"
        app.mount("/assets", StaticFiles(directory=web_root), name="web-assets")

        @app.get("/", include_in_schema=False)
        def web_ui():
            return FileResponse(web_root / "index.html")

    def error_response(request, status, code, message):
        return JSONResponse(
            {
                "code": code,
                "message": message,
                "request_id": request.state.request_id,
                "api_version": "1",
                "server_version": PLUGIN_VERSION,
            },
            status_code=status,
        )

    @app.exception_handler(ServerError)
    async def server_error(request, exc):
        return error_response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response(request, 400, "invalid_request", "Invalid fields or request schema")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        import logging

        logging.exception("Request failed: %s", request.state.request_id)
        return error_response(request, 500, "internal_error", "Internal server error")

    @app.middleware("http")
    async def boundary(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        allowed_origins = {f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}"}
        if settings.public_url:
            allowed_origins.add(settings.public_url)
        if request.headers.get("origin") and request.headers["origin"] not in allowed_origins:
            return error_response(
                request, 403, "origin_forbidden", "Browser origins are not permitted"
            )
        if request.method in {"POST", "PUT"}:
            length = request.headers.get("content-length")
            cap = settings.max_chunk_bytes if request.method == "PUT" else settings.max_json_bytes
            if length is None or not length.isdecimal():
                return error_response(request, 400, "length_required", "Content-Length is required")
            if int(length) > cap:
                return error_response(
                    request, 413, "request_too_large", "Request exceeds configured size limit"
                )
            # FastAPI can otherwise buffer an unbounded chunked body before validation.
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > cap or len(data) > int(length):
                    return error_response(
                        request, 413, "request_too_large", "Request exceeds declared length"
                    )
            if len(data) != int(length):
                return error_response(
                    request, 400, "length_mismatch", "Body differs from Content-Length"
                )
            request._body = bytes(data)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-API-Version"] = "1"
        response.headers["X-Server-Version"] = PLUGIN_VERSION
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def principal(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            reject(401, "unauthenticated", "Authentication required")
        return auth.principal(authorization[7:])

    def enrollment(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            reject(401, "unauthenticated", "Enrollment session required")
        return auth.principal(authorization[7:], scope="enrollment")

    def admin(p=Depends(principal)):
        auth.admin(p)
        return p

    def limited(request):
        auth.rate_limit(request.client.host if request.client else "unknown")

    prefix = "/api/v1"

    @app.get(prefix + "/health")
    def health():
        return {"status": "ok", "api_version": "1", "server_version": PLUGIN_VERSION}

    @app.get(prefix + "/capabilities")
    def capabilities(p=Depends(principal)):
        return {
            "api_version": "1",
            "server_version": PLUGIN_VERSION,
            "server_id": db.metadata("server_id"),
            "execution_os": settings.execution_os,
            "enforcement": "advisory",
            "trusted_users_only": True,
            "features": [
                "idempotency",
                "resumable_upload",
                "range_download",
                "ed25519",
                "dependencies",
            ],
            "max_chunk_bytes": settings.max_chunk_bytes,
            "resources": scheduler.resources(),
        }

    @app.post(prefix + "/auth/login")
    def login(body: Login, request: Request):
        limited(request)
        return auth.login(body.username, body.password)

    @app.post(prefix + "/auth/devices", status_code=201)
    def devices(body: Enrollment, p=Depends(enrollment)):
        return auth.enroll(p, body.device_id, body.public_key)

    @app.post(prefix + "/auth/challenge")
    def challenge(body: Challenge, request: Request):
        limited(request)
        return auth.challenge(body.device_id)

    @app.post(prefix + "/auth/token")
    def token(body: Token, request: Request):
        limited(request)
        return auth.token(body.device_id, body.nonce, body.signature)

    @app.get(prefix + "/me")
    def me(p=Depends(principal)):
        return {
            "user_id": p["user_id"],
            "username": p["username"],
            "role": p["role"],
            "devices": db.rows(
                "SELECT id,created_at,revoked_at,last_access FROM devices WHERE user_id=?",
                (p["user_id"],),
            ),
        }

    @app.post(prefix + "/me/devices", status_code=201)
    def add_device(body: Enrollment, p=Depends(principal)):
        return auth.enroll(p, body.device_id, body.public_key)

    @app.post(prefix + "/me/devices/{device_id}/revoke")
    def revoke_own(device_id: str, p=Depends(principal)):
        if not db.one("SELECT 1 FROM devices WHERE id=? AND user_id=?", (device_id, p["user_id"])):
            reject(404, "not_found", "Device not found")
        auth.revoke(device_id, p["user_id"])
        return {"revoked": True}

    @app.post(prefix + "/jobs", status_code=201)
    def create_job(
        body: JobSpec, idempotency_key: str | None = Header(default=None), p=Depends(principal)
    ):
        try:
            build_wrapper(body.model_dump(), "/job/work", settings.execution_os == "windows")
        except ValueError as exc:
            reject(400, "invalid_command", str(exc))
        return jobs.create(body, p, idempotency_key)

    @app.get(prefix + "/jobs")
    def list_jobs(
        p=Depends(principal), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)
    ):
        return [
            jobs.public(job)
            for job in db.rows(
                "SELECT * FROM jobs WHERE owner_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (p["user_id"], limit, offset),
            )
        ]

    @app.get(prefix + "/jobs/{job_id}")
    def get_job(job_id: str, p=Depends(principal)):
        return jobs.public(jobs.authorize(job_id, p))

    @app.get(prefix + "/jobs/{job_id}/uploads")
    def upload_status(job_id: str, p=Depends(principal)):
        return jobs.upload_status(job_id, p)

    @app.put(prefix + "/jobs/{job_id}/files/{path:path}")
    async def upload(job_id: str, path: str, request: Request, p=Depends(principal)):
        data = await request.body()
        # Filesystem/SQLite work belongs on a worker thread, never the event loop.
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(
            jobs.upload, job_id, path, data, request.headers.get("content-range"), p
        )

    @app.post(prefix + "/jobs/{job_id}/commit")
    def commit(job_id: str, p=Depends(principal)):
        return jobs.commit(job_id, p)

    @app.post(prefix + "/jobs/{job_id}/cancel", status_code=202)
    def cancel(job_id: str, p=Depends(principal)):
        return jobs.cancel(job_id, p)

    @app.get(prefix + "/jobs/{job_id}/log")
    def log(
        job_id: str,
        p=Depends(principal),
        offset: int = Query(0, ge=0),
        limit: int = Query(65536, ge=1, le=1048576),
    ):
        jobs.authorize(job_id, p)
        path = jobs.directory(job_id) / "job.log"
        if not path.exists():
            return {"offset": offset, "next_offset": offset, "text": ""}
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            reject(400, "unsafe_file", "Linked logs are forbidden")
        with open_regular(path) as stream:
            import os

            size = os.fstat(stream.fileno()).st_size
            stream.seek(offset)
            data = stream.read(limit)
        return {
            "offset": offset,
            "next_offset": offset + len(data),
            "text": data.decode("utf-8", errors="replace"),
            "size": size,
        }

    @app.get(prefix + "/jobs/{job_id}/files")
    def outputs(job_id: str, p=Depends(principal)):
        return jobs.outputs(job_id, p)

    @app.get(prefix + "/jobs/{job_id}/files/{path:path}")
    def download(job_id: str, path: str, request: Request, p=Depends(principal)):
        jobs.authorize(job_id, p)
        target = contained(jobs.directory(job_id), path)
        digest = sha256(target)
        stream = open_regular(target)
        import os

        size = os.fstat(stream.fileno()).st_size
        start, end = 0, size - 1
        range_header = request.headers.get("range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
            if not match:
                stream.close()
                reject(416, "invalid_range", "Only a single explicit byte range is supported")
            start = int(match.group(1))
            end = min(size - 1, int(match.group(2))) if match.group(2) else size - 1
            if start >= size or end < start:
                stream.close()
                reject(416, "invalid_range", "Range is outside file")
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(max(0, end - start + 1)),
            "X-SHA256": digest,
            "ETag": '"' + digest + '"',
        }
        if range_header:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        def chunks():
            try:
                stream.seek(start)
                remaining = max(0, end - start + 1)
                while remaining:
                    data = stream.read(min(1048576, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
            finally:
                stream.close()

        return StreamingResponse(
            chunks(),
            status_code=206 if range_header else 200,
            media_type="application/octet-stream",
            headers=headers,
        )

    @app.get(prefix + "/system/resources")
    def resources(p=Depends(principal)):
        return scheduler.resources()

    @app.get(prefix + "/admin/jobs")
    def all_jobs(
        p=Depends(admin), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)
    ):
        return [
            jobs.public(job)
            for job in db.rows(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            )
        ]

    @app.get(prefix + "/admin/users")
    def users(p=Depends(admin)):
        return db.rows("SELECT id,username,role,enabled,created_at FROM users ORDER BY username")

    @app.get(prefix + "/admin/devices")
    def all_devices(p=Depends(admin)):
        return db.rows(
            "SELECT id,user_id,created_at,revoked_at,last_access FROM devices ORDER BY created_at"
        )

    @app.post(prefix + "/admin/users", status_code=201)
    def create_user(body: UserCreate, p=Depends(admin)):
        return auth.create_user(body.username, body.password, body.role, p["user_id"])

    @app.post(prefix + "/admin/users/{user_id}/disable")
    def disable_user(user_id: str, p=Depends(admin)):
        if user_id == p["user_id"]:
            reject(409, "self_disable", "Use another administrator to disable your own account")
        with db.transaction() as c:
            if not c.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                reject(404, "not_found", "User not found")
            c.execute("UPDATE users SET enabled=0 WHERE id=?", (user_id,))
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            db.event(c, "user_disabled", p["user_id"], user_id=user_id)
        return {"disabled": True}

    @app.post(prefix + "/admin/devices/{device_id}/revoke")
    def revoke_device(device_id: str, p=Depends(admin)):
        auth.revoke(device_id, p["user_id"])
        return {"revoked": True}

    @app.post(prefix + "/admin/queue/pause")
    def pause(body: Pause, p=Depends(admin)):
        with db.transaction() as c:
            c.execute("UPDATE metadata SET value=? WHERE key='paused'", (json.dumps(body.paused),))
            db.event(c, "queue_pause", p["user_id"], paused=body.paused)
        return scheduler.resources()

    @app.get(prefix + "/admin/events")
    def events(p=Depends(admin), after: int = Query(0, ge=0)):
        return db.rows("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 1000", (after,))

    @app.post(prefix + "/admin/shutdown", status_code=202)
    def shutdown(p=Depends(admin)):
        callback = getattr(app.state, "shutdown", None)
        if callback is None:
            reject(503, "shutdown_unavailable", "Process lifecycle is managed externally")
        with db.transaction() as c:
            db.event(c, "daemon_stop", p["user_id"])
        callback()
        return {"stopping": True, "running_jobs": "preserved"}

    return app
