from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

try:
    from ..api_support import get_current_user
except ImportError:
    from api_support import get_current_user


def create_router(ui_dir: Path, agent_download_dir: Path) -> APIRouter:
    router = APIRouter()

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
        if not agent_download_dir.exists():
            raise HTTPException(status_code=404, detail="Файл агента не найден")

        archive = None
        for ext in ("*.zip", "*.exe"):
            files = sorted(agent_download_dir.glob(ext), key=lambda file: file.stat().st_mtime, reverse=True)
            if files:
                archive = files[0]
                break

        if not archive:
            raise HTTPException(status_code=404, detail="Файл агента не найден")

        return FileResponse(
            path=str(archive),
            filename=archive.name,
            media_type="application/octet-stream",
        )

    return router
