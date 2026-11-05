import logging
import os
import asyncio
import signal
import ssl
import threading
from contextlib import contextmanager

import uvicorn

from .api import create_app
from .listeners import listener_sockets, tls_listener_sockets


class CoordinatedServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        yield


async def run_listeners(app, servers, groups):
    async def run(server, streams):
        try:
            await server.serve(sockets=streams)
        except SystemExit as exc:
            raise RuntimeError("A server listener failed to start") from exc

    async with app.router.lifespan_context(app):
        tasks = [asyncio.create_task(run(server, streams)) for server, streams in zip(servers, groups)]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for server in servers:
                server.should_exit = True
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result


def run_https(app, config, settings, http_streams):
    from .tls import ensure_certificates

    material = ensure_certificates(settings)
    config.lifespan = "off"
    secure = uvicorn.Config(
        app, host=settings.lan_bind_address, port=settings.tls_port, lifespan="off",
        proxy_headers=False, access_log=False, limit_concurrency=settings.max_connections,
        timeout_keep_alive=5, timeout_graceful_shutdown=10,
        ssl_certfile=material["certfile"], ssl_keyfile=material["keyfile"],
        ssl_keyfile_password=material["password"], ssl_version=ssl.PROTOCOL_TLS_SERVER,
        ssl_ciphers="ECDHE+AESGCM:ECDHE+CHACHA20",
    )
    secure.load()
    secure.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
    servers = [CoordinatedServer(config), CoordinatedServer(secure)]
    app.state.listeners_ready = lambda: all(server.started for server in servers)

    def stop(*args):
        for server in servers:
            server.should_exit = True

    app.state.shutdown = stop
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, stop)
    try:
        with tls_listener_sockets(settings) as secure_streams:
            asyncio.run(run_listeners(app, servers, [http_streams, secure_streams]))
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def serve(settings):
    from .runtime import ensure_secure_dependencies

    settings.validate()
    ensure_secure_dependencies()
    if not settings.allow_privileged:
        if os.name == "nt":
            import ctypes

            privileged = bool(ctypes.windll.shell32.IsUserAnAdmin())
        else:
            privileged = os.geteuid() == 0
        if privileged:
            raise RuntimeError(
                "Run under a low-privilege service account (allow_privileged is for isolated CI only)"
            )
    settings.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (settings.root / "logs").mkdir(exist_ok=True, mode=0o700)
    logging.basicConfig(
        filename=settings.root / "logs" / "daemon.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=settings.port,
        proxy_headers=False,
        access_log=False,
        limit_concurrency=settings.max_connections,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=10,
    )
    try:
        with listener_sockets(settings) as streams:
            if settings.lan_tls:
                run_https(app, config, settings, streams)
            else:
                server = uvicorn.Server(config)
                app.state.shutdown = lambda: setattr(server, "should_exit", True)
                server.run(sockets=streams)
    finally:
        app.state.db.close()
