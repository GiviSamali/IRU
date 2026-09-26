from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

try:
    from .agent_update import download_release
    from ..agent_release import read_release
    from ..database import PLAN_LIMITS
    from ..api_support import get_current_user
except ImportError:
    from routers.agent_update import download_release
    from agent_release import read_release
    from database import PLAN_LIMITS
    from api_support import get_current_user


def create_router(ui_dir: Path, agent_download_dir: Path, updates_dir: Path | None = None) -> APIRouter:
    router = APIRouter()
    updates_dir = updates_dir or Path(__file__).resolve().parent.parent / "updates"

    @router.get("/api/info")
    async def api_info():
        info = {
            "name": "IRU",
            "server": "fastapi",
        }
        version_file = updates_dir / "version.json"
        if version_file.exists() or (updates_dir / "release.json").exists():
            try:
                version_data = read_release(updates_dir)
                version = version_data.get("version")
                if version:
                    info["version"] = version
            except Exception:
                pass
        return info

    @router.get("/api/plans")
    async def api_plans():
        return {
            "plans": PLAN_LIMITS,
        }

    @router.get("/", response_class=HTMLResponse)
    async def root():
        index = ui_dir / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>ИРУ v3.5 — UI не найден</h1>")

    @router.get("/instruction")
    async def instruction_page():
        return FileResponse(ui_dir / "install.html", media_type="text/html")

    @router.get("/about")
    async def about_page():
        return FileResponse(ui_dir / "about.html", media_type="text/html")

    @router.get("/terms")
    async def terms_page():
        return FileResponse(ui_dir / "terms.html", media_type="text/html")

    @router.get("/api/download_agent")
    async def download_agent(request: Request):
        get_current_user(request)
        return download_release(updates_dir)

    return router
