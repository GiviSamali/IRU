import re
import tempfile
from pathlib import Path


def _safe_segment(value: str | int) -> str:
    segment = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return segment or "default"


def get_default_sandbox_root(user_id: str | int, task_id: str | None = None) -> Path:
    root = Path(tempfile.gettempdir()) / "iru_sandbox" / f"user_{_safe_segment(user_id)}"
    if task_id is not None:
        root = root / _safe_segment(task_id)
    return normalize_path(root)


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def is_path_inside(parent: str | Path, child: str | Path) -> bool:
    try:
        normalize_path(child).relative_to(normalize_path(parent))
    except ValueError:
        return False
    return True


def assert_path_inside_sandbox(sandbox_root: str | Path, target_path: str | Path) -> None:
    if not is_path_inside(sandbox_root, target_path):
        raise ValueError(f"Target path {normalize_path(target_path)} is outside sandbox {normalize_path(sandbox_root)}")


def make_sandbox_subpath(sandbox_root: str | Path, relative_path: str | Path) -> Path:
    subpath = Path(relative_path)
    if subpath.is_absolute():
        raise ValueError(f"Sandbox subpath must be relative: {relative_path}")
    target_path = normalize_path(normalize_path(sandbox_root) / subpath)
    assert_path_inside_sandbox(sandbox_root, target_path)
    return target_path
