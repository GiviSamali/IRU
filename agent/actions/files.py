# agent/actions/files.py
import os
from pathlib import Path

def get_default_desktop() -> Path:
    home = Path.home()
    one_drive_desktop = home / "OneDrive" / "Desktop"
    if one_drive_desktop.exists():
        return one_drive_desktop
    classic_desktop = home / "Desktop"
    if classic_desktop.exists():
        return classic_desktop
    return home

def find_file(name_part: str, base: str | None = None, max_results: int = 20) -> dict:
    if base is None:
        base_path = get_default_desktop()
    else:
        base_path = Path(base).expanduser()

    if not base_path.exists():
        raise FileNotFoundError(f"Base path not found: {base_path}")

    pattern = (name_part or "").lower()
    found: list[str] = []

    for root, dirs, files in os.walk(base_path):
        for f in files:
            fname = f.lower()
            if not pattern or pattern in fname:
                full = str(Path(root) / f)
                found.append(full)
                if len(found) >= max_results:
                    break
        if len(found) >= max_results:
            break

    return {
        "base": str(base_path),
        "query": name_part,
        "count": len(found),
        "files": found,
    }

def list_dir(path: str | None = None) -> dict:
    """
    Вернуть содержимое директории: списки файлов и папок.
    Если path не задан, используем дефолтный Desktop.
    """
    if path is None or not path.strip():
        base_path = get_default_desktop()
    else:
        base_path = Path(path).expanduser()

    if not base_path.exists() or not base_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {base_path}")

    entries = list(base_path.iterdir())
    files = [str(p) for p in entries if p.is_file()]
    dirs = [str(p) for p in entries if p.is_dir()]

    return {
        "path": str(base_path),
        "files": files,
        "dirs": dirs,
        "files_count": len(files),
        "dirs_count": len(dirs),
    }

def open_path(path: str) -> dict:
    """
    Открыть файл/папку стандартным способом в Windows.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {p}")

    # Windows-путь: открываем через os.startfile
    os.startfile(str(p))

    return {
        "message": "opened",
        "path": str(p),
    }
