import hashlib
import re
from urllib.parse import urlsplit
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

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

    def asset_path(url: str, base: Path) -> Path | None:
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or not parts.path:
            return None
        path = (ui_dir / parts.path.lstrip("/") if parts.path.startswith("/") else base / parts.path).resolve()
        if not path.is_relative_to(ui_dir.resolve()) or not path.is_file():
            return None
        return path

    def version_url(url: str, base: Path) -> str:
        path = asset_path(url, base)
        if path is None or path.suffix not in {".js", ".css"}:
            return url
        # Aggregate CSS hash also invalidates entry URLs when an imported sheet changes.
        content = path.read_bytes()
        if path.suffix == ".css":
            content += b"".join(p.relative_to(ui_dir).as_posix().encode() + p.read_bytes()
                               for p in sorted(ui_dir.rglob("*.css")))
        revision = hashlib.sha256(content).hexdigest()[:16]
        parts = urlsplit(url)
        return parts.path + "?v=" + revision + ("#" + parts.fragment if parts.fragment else "")

    @router.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/", response_class=HTMLResponse)
    async def root():
        index = ui_dir / "index.html"
        headers = {"Cache-Control": "no-cache"}
        if index.exists():
            html = index.read_text(encoding="utf-8")
            html = re.sub(r'(\b(?:src|href)=)(["\'])([^"\']+)\2',
                          lambda m: m[1] + m[2] + version_url(m[3], ui_dir) + m[2], html)
            headers["X-IRU-UI-Build"] = hashlib.sha256(html.encode()).hexdigest()[:16]
            return HTMLResponse(html, headers=headers)
        return HTMLResponse("<h1>ИРУ v3.5 — UI не найден</h1>", headers=headers)

    @router.get("/style.css", include_in_schema=False)
    @router.get("/css/{name:path}", include_in_schema=False)
    async def stylesheet(name: str = ""):
        path = asset_path("css/" + name if name else "style.css", ui_dir)
        if path is None or path.suffix != ".css":
            raise HTTPException(404, "Stylesheet not found")
        text = path.read_text(encoding="utf-8")
        text = re.sub(r'(@import\s+url\(["\'])([^"\']+)(["\']\))',
                      lambda m: m[1] + version_url(m[2], path.parent) + m[3], text)
        return Response(text, media_type="text/css", headers={"Cache-Control": "no-cache"})

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
