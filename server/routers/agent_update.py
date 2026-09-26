import hashlib
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

try:
    from ..api_support import _is_admin, get_current_user
    from ..database import add_audit_log
    from ..agent_release import read_release, validate_zip, validate_version, atomic_write
except ImportError:
    from api_support import _is_admin, get_current_user
    from database import add_audit_log
    from agent_release import read_release, validate_zip, validate_version, atomic_write


NO_CACHE = {"Cache-Control": "no-store, max-age=0"}
RELEASE_ERRORS = (ValueError, OSError, KeyError, zipfile.BadZipFile, RuntimeError, AttributeError)


def checked_release(directory):
    try:
        return read_release(directory)
    except RELEASE_ERRORS as exc:
        raise HTTPException(503, "Agent release is unavailable or inconsistent; publish a verified ZIP", headers=NO_CACHE) from exc


def download_release(directory, version=None, sha256=None):
    data = checked_release(directory)
    if (version and version != data["version"]) or (sha256 and sha256 != data["sha256"]):
        raise HTTPException(409, "Release changed; check the version again", headers=NO_CACHE)
    return FileResponse(directory / data["filename"], filename="IruAgent.zip", media_type="application/zip",
                        headers=dict(NO_CACHE, **{"X-Agent-Version": data["version"], "X-Agent-SHA256": data["sha256"]}))


def create_router(updates_dir: Path) -> APIRouter:
    router = APIRouter()

    @router.get("/api/agent/version")
    async def api_agent_version():
        data = checked_release(updates_dir)
        data["download_url"] = "/api/agent/download?" + urlencode({"version": data["version"], "sha256": data["sha256"]})
        return JSONResponse(data, headers=NO_CACHE)

    @router.get("/api/agent/download")
    async def api_agent_download(version: str | None = None, sha256: str | None = None):
        return download_release(updates_dir, version, sha256)

    @router.post("/api/agent/upload")
    async def api_agent_upload(request: Request, version: str = Query(...)):
        user = get_current_user(request)
        if not _is_admin(user):
            raise HTTPException(403, "Только для администратора")
        body = await request.body()
        if not 1000 <= len(body) <= 100_000_000:
            raise HTTPException(400, "Размер ZIP должен быть от 1000 байт до 100 МБ")
        try:
            validate_version(version)
            validate_zip(io.BytesIO(body), version)
        except RELEASE_ERRORS as exc:
            raise HTTPException(400, f"Invalid agent release: {exc}") from exc
        digest = hashlib.sha256(body).hexdigest()
        updates_dir.mkdir(parents=True, exist_ok=True)
        try:
            old = read_release(updates_dir)
        except RELEASE_ERRORS:
            old = {}  # an inconsistent legacy release can be repaired by a verified upload
        if old:
            def parts(value):
                numbers = tuple(map(int, value.split(".")))
                return numbers + (0,) * (4 - len(numbers))
            if parts(version) < parts(old["version"]):
                raise HTTPException(409, "Release downgrade refused")
            if parts(version) == parts(old["version"]) and old["sha256"] != digest:
                raise HTTPException(409, "Different archive for an existing version; increment version")
        filename = f"IruAgent-{version}-{digest}.zip"
        atomic_write(updates_dir / filename, body)
        data = {"version": version, "min_version": old.get("min_version", "3.0"),
                "changelog": old.get("changelog", ""), "kind": "zip", "filename": filename,
                "sha256": digest, "size": len(body)}
        atomic_write(updates_dir / "release.json", json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        add_audit_log(user["id"], user["name"], "agent_upload", f"version={version}, sha256={digest}, size={len(body)}", None)
        return dict(data, status="ok")

    return router
