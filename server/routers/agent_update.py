import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

try:
    from ..api_support import _is_admin, get_current_user
    from ..database import add_audit_log
except ImportError:
    from api_support import _is_admin, get_current_user
    from database import add_audit_log


def create_router(updates_dir: Path) -> APIRouter:
    router = APIRouter()

    @router.get("/api/agent/version")
    async def api_agent_version():
        version_file = updates_dir / "version.json"
        if not version_file.exists():
            return {"version": "0.0", "min_version": "0.0", "changelog": "", "download_url": "", "kind": "exe"}
        data = json.loads(version_file.read_text(encoding="utf-8-sig"))
        data["download_url"] = "/api/agent/download"
        if "kind" not in data:
            data["kind"] = "exe"
        return data

    @router.get("/api/agent/download")
    async def api_agent_download():
        version_file = updates_dir / "version.json"
        if not version_file.exists():
            raise HTTPException(status_code=404, detail="Файл версии не найден")
        data = json.loads(version_file.read_text(encoding="utf-8-sig"))
        filename = data.get("filename", "IruAgent.exe")
        kind = data.get("kind", "exe")
        file_path = updates_dir / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Файл агента не найден на сервере")
        media_type = "application/zip" if kind == "zip" else "application/octet-stream"
        return FileResponse(path=str(file_path), filename=filename, media_type=media_type)

    @router.post("/api/agent/upload")
    async def api_agent_upload(request: Request, version: str = Query(...)):
        user = get_current_user(request)
        if not _is_admin(user):
            raise HTTPException(status_code=403, detail="Только для администратора")
        body = await request.body()
        if len(body) < 1000:
            raise HTTPException(status_code=400, detail="Файл слишком маленький")
        if len(body) > 100_000_000:
            raise HTTPException(status_code=400, detail="Файл слишком большой (>100МБ)")

        updates_dir.mkdir(exist_ok=True)
        is_zip = body[:4] == b"PK\x03\x04"
        if is_zip:
            kind = "zip"
            filename = "IruAgent.zip"
        else:
            kind = "exe"
            filename = "IruAgent.exe"

        save_path = updates_dir / filename
        save_path.write_bytes(body)

        version_data = {
            "version": version,
            "min_version": "3.0",
            "changelog": "",
            "filename": filename,
            "kind": kind,
        }
        version_file = updates_dir / "version.json"
        if version_file.exists():
            try:
                old = json.loads(version_file.read_text(encoding="utf-8-sig"))
                version_data["min_version"] = old.get("min_version", "3.0")
                version_data["changelog"] = old.get("changelog", "")
            except Exception:
                pass
        version_file.write_text(json.dumps(version_data, ensure_ascii=False, indent=2), encoding="utf-8")
        add_audit_log(user["id"], user["name"], "agent_upload", f"version={version}, kind={kind}, size={len(body)}", None)
        return {"status": "ok", "version": version, "kind": kind, "size": len(body)}

    return router
