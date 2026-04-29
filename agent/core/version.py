from __future__ import annotations

import sys
from pathlib import Path


def read_agent_version() -> str:
    """Read the agent version from VERSION.txt near the executable or source tree."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "VERSION.txt")
    candidates.append(Path(__file__).resolve().parents[1] / "VERSION.txt")
    candidates.append(Path(__file__).resolve().parents[2] / "VERSION.txt")
    for path in candidates:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8-sig").strip()
        except Exception:
            continue
    return "3.11"

