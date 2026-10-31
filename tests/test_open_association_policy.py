"""File associations: documents open through the shell, code never does.

The shell association for .py/.pyw/.pyc/.lnk/.hta executes code, so only an
explicit allowlist of document formats may reach os.startfile. Executables keep
the existing process-launch path and its authority checks.
"""
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from core import actions  # noqa: E402


@pytest.mark.parametrize("name", ["a.py", "b.pyw", "c.pyc", "d.lnk", "e.hta", "f.vbs",
                                  "g.js", "h.wsf", "i.sh", "j.dat", "k.reg", "l.scr"])
def test_code_bearing_or_unknown_association_is_refused(name):
    assert actions._open_object_kind(f"C:/tmp/{name}") == "unsafe"


@pytest.mark.parametrize("name", ["a.pptx", "b.docx", "c.xlsx", "d.pdf", "e.txt", "f.md",
                                  "g.csv", "h.png", "i.jpg"])
def test_document_association_is_openable(name):
    assert actions._open_object_kind(f"C:/tmp/{name}") == "file"


@pytest.mark.parametrize("name", ["a.exe", "b.cmd", "c.bat", "d.ps1"])
def test_executables_keep_the_process_launch_path(name):
    """Not classified as an object: app_launch falls through to Popen."""
    assert actions._open_object_kind(f"C:/tmp/{name}") is None


def test_command_line_is_not_treated_as_a_document():
    assert actions._open_object_kind("notepad C:/tmp/report.txt") is None


class _Proc:
    pid = 4321


@pytest.fixture
def no_shell(monkeypatch):
    opened = []
    monkeypatch.setattr(actions, "_shell_open_object", lambda path: opened.append(path))
    monkeypatch.setattr(actions.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(actions, "_process_alive", lambda pid: True)
    monkeypatch.setattr(actions, "window_find", lambda **k: {"match": None})
    return opened


def test_executable_argument_is_not_opened_through_the_shell(no_shell):
    actions.app_launch(executable="C:/Windows/notepad.exe")
    assert no_shell == []


def test_executable_command_is_not_opened_through_the_shell(no_shell):
    actions.app_launch(command="C:/Windows/notepad.exe")
    assert no_shell == []


def test_unsafe_association_never_reaches_the_shell(no_shell):
    result = actions.app_launch(command="C:/tmp/setup.lnk")
    assert no_shell == []
    assert result["status"] == "failed"
    assert result["error_code"] == "unsafe_open_association"
    assert result["object_open_verified"] is False


def test_document_is_opened_through_the_shell_but_not_verified_blindly(monkeypatch, no_shell):
    monkeypatch.setattr(actions, "_observe_opened_object", lambda *a, **k: None)
    result = actions.app_launch(command='C:/My Docs/report.pptx')
    assert no_shell == ["C:/My Docs/report.pptx"]
    assert result["requested_object"] == {"type": "file", "path": "C:/My Docs/report.pptx"}
    assert result["object_open_verified"] is False
    assert result["status"] == "launched"


def test_verified_observation_is_reported(monkeypatch, no_shell):
    observed = {"observed_path": "C:/My Docs/report.pptx", "kind": "file",
                "title": "report - PowerPoint", "process": "POWERPNT.EXE",
                "class_name": "PPTFrameClass", "matched_name": "report.pptx"}
    monkeypatch.setattr(actions, "_observe_opened_object", lambda *a, **k: observed)
    result = actions.app_launch(command='C:/My Docs/report.pptx')
    assert result["object_open_verified"] is True
    assert result["status"] == "opened_verified"
    assert result["observed_object"]["observed_path"] == "C:/My Docs/report.pptx"
