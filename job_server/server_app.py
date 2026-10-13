import logging
import os

import uvicorn

from .api import create_app


def serve(settings):
    from .runtime import ensure_secure_dependencies

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
    server = uvicorn.Server(config)
    app.state.shutdown = lambda: setattr(server, "should_exit", True)
    server.run()
