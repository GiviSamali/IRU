"""Window-only preferences. No site, account or runtime state."""
import json
import os
from pathlib import Path


def rectangle(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(n) is not int or abs(n) > 100000 for n in value)
            or not 240 <= value[2] <= 10000 or not 160 <= value[3] <= 10000):
        return None
    return value


def load_window_state(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {mode: rectangle(data.get(mode)) for mode in ("compact", "expanded")}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_window_state(path: Path, data):
    safe = {mode: rectangle(data.get(mode)) for mode in ("compact", "expanded")}
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(safe), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        # Window preferences must never prevent starting or closing IRU.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
