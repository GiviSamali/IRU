from pathlib import Path
from typing import Dict
import uuid
import base64

DOWNLOAD_TOKENS: Dict[str, Path] = {}


def register_download(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    token = f"file-{uuid.uuid4().hex[:8]}"
    DOWNLOAD_TOKENS[token] = p

    return {
        "token": token,
        "name": p.name,
        "size": p.stat().st_size,
        "content_type": guess_content_type(p),
    }


def guess_content_type(p: Path) -> str:
    ext = p.suffix.lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext in [".png", ".jpg", ".jpeg"]:
        return "image/" + ext.lstrip(".")
    if ext in [".txt", ".log"]:
        return "text/plain"
    return "application/octet-stream"
def get_file_content(token: str) -> dict:
    p = DOWNLOAD_TOKENS.get(token)
    if p is None:
        raise KeyError(f"Unknown download token: {token}")
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")

    return {
        "name": p.name,
        "size": len(data),
        "content_type": guess_content_type(p),
        "data_base64": b64,
    }