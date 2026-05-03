"""
FastAPI server composition root for IRU.

This module assembles the app, lifecycle hooks, static mounts, and domain routers.
Business logic lives in support/runtime/router modules.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

try:
    from .database import cleanup_expired_refresh_tokens, init_db
    from .routers.admin import router as admin_router
    from .routers.agent_update import create_router as create_agent_update_router
    from .routers.auth import router as auth_router
    from .routers.chats import router as chats_router
    from .routers.devices import router as devices_router
    from .routers.public import create_router as create_public_router
    from .routers.tasks import router as tasks_router
    from .routers.ws import router as ws_router
except ImportError:
    from database import cleanup_expired_refresh_tokens, init_db
    from routers.admin import router as admin_router
    from routers.agent_update import create_router as create_agent_update_router
    from routers.auth import router as auth_router
    from routers.chats import router as chats_router
    from routers.devices import router as devices_router
    from routers.public import create_router as create_public_router
    from routers.tasks import router as tasks_router
    from routers.ws import router as ws_router


UI_DIR = Path(__file__).parent.parent / "ui"
STATIC_DIR = UI_DIR
AGENT_DOWNLOAD_DIR = Path("/opt/iru/app/exe")
UPDATES_DIR = Path(__file__).parent / "updates"


async def _cleanup_tokens_loop():
    """Periodically clean up expired refresh tokens."""
    while True:
        await asyncio.sleep(3600)
        try:
            cleanup_expired_refresh_tokens()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cleanup_expired_refresh_tokens()
    task = asyncio.create_task(_cleanup_tokens_loop())
    print("[server] ИРУ v3.5 запущен")
    try:
        yield
    finally:
        task.cancel()
        print("[server] ИРУ v3.5 остановлен")


def create_app() -> FastAPI:
    app = FastAPI(title="ИРУ v3.5", lifespan=lifespan)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(create_public_router(UI_DIR, AGENT_DOWNLOAD_DIR))
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(devices_router)
    app.include_router(chats_router)
    app.include_router(tasks_router)
    app.include_router(create_agent_update_router(UPDATES_DIR))
    app.include_router(ws_router)

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui_root")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
