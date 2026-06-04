import builtins
import json
import subprocess
import sys

from agent.shell import status as shell_status
from agent.shell import tray as shell_tray
from agent.shell.config import get_shell_config_path


def test_tray_module_imports_without_top_level_pystray_requirement():
    source = shell_tray.Path(shell_tray.__file__).read_text(encoding="utf-8")

    assert "import pystray" not in source.split("def create_tray_icon", 1)[0]


def test_status_payload_includes_shell_fields(monkeypatch, tmp_path):
    monkeypatch.delenv("IRU_WEB_URL", raising=False)
    config_path = tmp_path / "state" / "shell_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"web_url": "http://status.local"}), encoding="utf-8")

    payload = shell_status.build_status_payload(
        config_path=config_path,
        pywebview_available=False,
        tray_available=False,
    )

    assert payload == {
        "web_url": "http://status.local",
        "config_path": str(config_path),
        "pywebview_available": False,
        "tray_available": False,
    }
    formatted = shell_status.format_status(payload)
    assert "web_url: http://status.local" in formatted
    assert "config_path:" in formatted


def test_settings_path_resolver_returns_shell_config_path(monkeypatch, tmp_path):
    monkeypatch.setenv("IRU_HOME", str(tmp_path))

    assert get_shell_config_path() == tmp_path / "state" / "shell_config.json"


def test_open_config_location_uses_explorer_select_on_windows(monkeypatch, tmp_path):
    config_path = tmp_path / "state" / "shell_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")
    calls = []

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda args: calls.append(args))

    shell_tray.open_config_location(config_path)

    assert calls == [["explorer", "/select,", str(config_path)]]


def test_create_tray_icon_returns_none_when_pystray_missing(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "pystray":
            raise ImportError("no pystray")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    controller = shell_tray.ShellTrayController(
        web_url="http://127.0.0.1:8000",
        config_path=tmp_path / "state" / "shell_config.json",
    )

    assert shell_tray.create_tray_icon(controller) is None
    assert controller.tray_icon is None
