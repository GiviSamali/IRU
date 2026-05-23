import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _actions(monkeypatch):
    from core import actions

    monkeypatch.setattr(actions.os, "name", "nt")
    return actions


def _window(**overrides):
    window = {
        "handle": 1001,
        "pid": 4321,
        "title": "Untitled - Notepad",
        "class_name": "Notepad",
        "process_name": "notepad.exe",
        "visible": True,
        "minimized": False,
        "foreground": False,
        "bounds": {"left": 0, "top": 0, "right": 800, "bottom": 600},
        "width": 800,
        "height": 600,
    }
    window.update(overrides)
    return window


def test_window_list_returns_structured_data(monkeypatch):
    actions = _actions(monkeypatch)
    monkeypatch.setattr(actions, "_list_windows_internal", lambda **kwargs: [_window()])

    result = actions.window_list()

    assert result["status"] == "ok"
    assert result["windows"][0]["title"] == "Untitled - Notepad"
    assert result["windows"][0]["pid"] == 4321
    assert result["windows"][0]["bounds"]["right"] == 800


def test_window_find_filters_by_title_process_and_pid(monkeypatch):
    actions = _actions(monkeypatch)
    windows = [
        _window(pid=1111, title="Other", process_name="other.exe"),
        _window(pid=4321, title="Untitled - Notepad", process_name="notepad.exe"),
    ]
    monkeypatch.setattr(actions, "_list_windows_internal", lambda **kwargs: windows)

    result = actions.window_find(pid=4321, title_contains="note", process_name="notepad.exe", timeout_sec=0)

    assert result["status"] == "found"
    assert result["match"]["pid"] == 4321


def test_window_verify_returns_verified_for_visible_match(monkeypatch):
    actions = _actions(monkeypatch)
    monkeypatch.setattr(actions, "_list_windows_internal", lambda **kwargs: [_window()])
    monkeypatch.setattr(actions, "_process_alive", lambda pid: True)

    result = actions.window_verify(pid=4321, title_contains="Notepad", timeout_sec=0)

    assert result["status"] == "verified"
    assert result["verified"] is True
    assert result["window_visible"] is True


def test_window_verify_process_alive_no_window(monkeypatch):
    actions = _actions(monkeypatch)
    monkeypatch.setattr(actions, "_list_windows_internal", lambda **kwargs: [])
    monkeypatch.setattr(actions, "_process_alive", lambda pid: True)

    result = actions.window_verify(pid=4321, timeout_sec=0)

    assert result["status"] == "process_alive_no_window"
    assert result["verified"] is False


def test_window_close_refuses_ambiguous_matches(monkeypatch):
    actions = _actions(monkeypatch)
    monkeypatch.setattr(actions, "_list_windows_internal", lambda **kwargs: [
        _window(handle=1, pid=10, title="Editor"),
        _window(handle=2, pid=11, title="Editor"),
    ])

    result = actions.window_close(title_contains="Editor")

    assert result["status"] == "ambiguous"
    assert result["error"] == "ambiguous_window_match"
