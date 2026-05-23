import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from server.python_runtime import (
    compact_python_runtime_summary,
    validate_python_runtime_receipt,
)
from server.runtime_state import devices


AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _runtime_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from core import actions

    monkeypatch.setattr(actions.platform, "system", lambda: "Windows")
    return actions, tmp_path / "IRU"


def _valid_runtime_receipt(device_id="givi", status="ok"):
    return {
        "runtime_receipt_version": 1,
        "device_id": device_id,
        "mode": "check",
        "status": status,
        "created_at": "2026-05-20T00:00:00Z",
        "paths": {
            "iru_home": r"C:\Users\tester\AppData\Local\IRU",
            "runtime_home": r"C:\Users\tester\AppData\Local\IRU\runtime",
            "venv_path": r"C:\Users\tester\AppData\Local\IRU\runtime\venv",
            "venv_python": r"C:\Users\tester\AppData\Local\IRU\runtime\venv\Scripts\python.exe" if status == "ok" else "",
            "pip_path": r"C:\Users\tester\AppData\Local\IRU\runtime\venv\Scripts\pip.exe" if status == "ok" else "",
        },
        "python": {
            "source": "system",
            "base_python": r"C:\Python311\python.exe",
            "base_version": "3.11.9",
            "venv_python": r"C:\Users\tester\AppData\Local\IRU\runtime\venv\Scripts\python.exe" if status == "ok" else "",
            "venv_version": "3.11.9" if status == "ok" else "",
            "architecture": "x64",
        },
        "pip": {"status": "ok" if status == "ok" else "missing", "version": "24.0" if status == "ok" else ""},
        "packages": {"checked": [], "installed": [], "missing": [], "failed": []},
        "health": {"runtime": "ok" if status == "ok" else "error", "venv": "ok" if status == "ok" else "missing", "pip": "ok" if status == "ok" else "missing"},
        "warnings": [],
        "next_actions": [],
    }


def test_check_mode_missing_returns_missing_or_install_required(monkeypatch, tmp_path):
    actions, home = _runtime_home(monkeypatch, tmp_path)

    receipt = actions.prepare_runtime(mode="check", device_id="givi")

    assert receipt["status"] in {"missing", "install_required"}
    assert receipt["status"] != "ok"
    assert receipt["device_id"] == "givi"
    saved = json.loads((home / "state" / "python_runtime_receipt.json").read_text(encoding="utf-8"))
    assert saved["stage"] == "missing"


def test_prepare_mode_with_system_python_creates_venv(monkeypatch, tmp_path):
    actions, home = _runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr(actions, "_find_base_python", lambda: sys.executable)

    receipt = actions.prepare_runtime(mode="prepare", device_id="givi", packages=["json"])

    assert receipt["status"] in {"ok", "degraded"}
    assert receipt["paths"]["venv_python"]
    assert Path(receipt["paths"]["venv_python"]).exists()
    assert receipt["pip"]["status"] in {"ok", "missing", "broken"}
    assert receipt["packages"]["checked"] == ["json"]
    assert receipt["stage"] == "completed"
    assert (home / "state" / "python_runtime_receipt.json").exists()
    assert (home / "runtime" / "receipts" / "python_runtime_receipt.json").exists()


def test_prepare_mode_without_python_returns_install_required(monkeypatch, tmp_path):
    actions, _home = _runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr(actions, "_find_base_python", lambda: None)

    receipt = actions.prepare_runtime(mode="prepare", device_id="givi")

    assert receipt["status"] == "install_required"
    assert receipt["paths"]["venv_python"] == ""
    assert "install_python" in receipt["next_actions"]


def test_repair_mode_recreates_broken_venv(monkeypatch, tmp_path):
    actions, home = _runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr(actions, "_find_base_python", lambda: sys.executable)
    broken = home / "runtime" / "venv"
    broken.mkdir(parents=True)

    receipt = actions.prepare_runtime(mode="repair", device_id="givi")

    assert receipt["status"] in {"ok", "degraded"}
    assert Path(receipt["paths"]["venv_python"]).exists()


def test_prepare_pip_upgrade_failure_is_recoverable(monkeypatch, tmp_path):
    actions, home = _runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr(actions, "_find_base_python", lambda: sys.executable)
    original_run_python = actions._run_python

    def fake_run_python(args, timeout=45):
        text = " ".join(str(part) for part in args)
        if " -m pip install --upgrade pip setuptools wheel" in text:
            return 1, "", "upgrade failed"
        return original_run_python(args, timeout=timeout)

    monkeypatch.setattr(actions, "_run_python", fake_run_python)

    receipt = actions.prepare_runtime(mode="prepare", device_id="givi", upgrade_pip=True)

    assert receipt["status"] in {"ok", "degraded"}
    assert receipt["stage"] == "completed"
    assert receipt["paths"]["venv_python"]
    assert receipt["pip"]["status"] == "ok"
    assert any("pip bootstrap upgrade failed" in warning for warning in receipt["warnings"])
    saved = json.loads((home / "state" / "python_runtime_receipt.json").read_text(encoding="utf-8"))
    assert saved["stage"] == "completed"


def test_check_after_partially_created_venv_returns_ok(monkeypatch, tmp_path):
    actions, home = _runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr(actions, "_find_base_python", lambda: sys.executable)

    prepared = actions.prepare_runtime(mode="prepare", device_id="givi")

    def fail_base_lookup():
        raise AssertionError("check must validate existing venv before probing base python")

    monkeypatch.setattr(actions, "_find_base_python", fail_base_lookup)
    checked = actions.prepare_runtime(mode="check", device_id="givi")

    assert prepared["paths"]["venv_python"]
    assert checked["status"] == "ok"
    assert checked["stage"] == "completed"
    saved = json.loads((home / "state" / "python_runtime_receipt.json").read_text(encoding="utf-8"))
    assert saved["mode"] == "check"
    assert saved["status"] == "ok"
    assert saved["stage"] == "completed"


def test_find_base_python_skips_packaged_agent_executable(monkeypatch, tmp_path):
    actions, _home = _runtime_home(monkeypatch, tmp_path)
    agent_exe = r"C:\Program Files\IRU\IruAgent.exe"
    system_python = r"C:\Python311\python.exe"
    probes = []

    monkeypatch.setattr(actions.sys, "frozen", True, raising=False)
    monkeypatch.setattr(actions.sys, "executable", agent_exe)
    monkeypatch.setattr(actions.shutil, "which", lambda name: system_python if name == "python" else None)

    def fake_run_python(args, timeout=45):
        probes.append(args)
        return 0, system_python, ""

    monkeypatch.setattr(actions, "_run_python", fake_run_python)

    assert actions._find_base_python() == system_python
    assert probes
    assert all(agent_exe not in part for command in probes for part in command)


def test_run_python_uses_create_no_window_on_windows(monkeypatch, tmp_path):
    actions, _home = _runtime_home(monkeypatch, tmp_path)
    captured = {}

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Proc()

    monkeypatch.setattr(actions.os, "name", "nt")
    monkeypatch.setattr(actions.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(actions.subprocess, "run", fake_run)

    assert actions._run_python(["python", "-V"]) == (0, "", "")
    assert captured["creationflags"] == 0x08000000


def test_validate_python_runtime_receipt_accepts_and_rejects():
    valid = _valid_runtime_receipt()
    valid["stage"] = "pip_checked"
    assert validate_python_runtime_receipt(valid) == (True, "ok")

    missing_device = dict(valid)
    missing_device["device_id"] = ""
    assert validate_python_runtime_receipt(missing_device) == (False, "missing_device_id")

    missing_status = dict(valid)
    missing_status["status"] = ""
    assert validate_python_runtime_receipt(missing_status) == (False, "invalid_status")

    fake_ok = _valid_runtime_receipt()
    fake_ok["paths"] = dict(fake_ok["paths"], venv_python="")
    assert validate_python_runtime_receipt(fake_ok) == (False, "missing_venv_python")


def test_compact_summary_excludes_raw_logs():
    receipt = _valid_runtime_receipt()
    receipt["raw_stdout"] = "x" * 1000

    summary = compact_python_runtime_summary(receipt)

    assert summary["runtime_status"] == "ok"
    assert summary["python_version"] == "3.11.9"
    assert summary["pip_status"] == "ok"
    assert summary["receipt_hash"]
    assert "raw_stdout" not in summary
    assert "x" * 100 not in str(summary)


def test_runtime_endpoint_stores_summary(monkeypatch):
    from server.routers import devices as devices_router

    devices.clear()
    devices["7:givi"] = {"ws": object(), "pending": {}, "user_id": 7, "short_device_id": "givi", "info": {"hostname": "GIVI"}}
    receipt = _valid_runtime_receipt()
    stored = {}

    async def fake_send(device_key, action, params, user_id=None):
        assert action == "device.prepare_runtime"
        assert params["mode"] == "check"
        return receipt

    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": 7})
    monkeypatch.setattr(devices_router, "send_command_to_agent", fake_send)
    monkeypatch.setattr(devices_router, "update_device_python_runtime_summary", lambda device_id, summary: stored.update({device_id: summary}))

    result = asyncio.run(devices_router.prepare_runtime_for_user({"id": 7, "name": "tester"}, "givi", "check", []))

    assert result["status"] == "ok"
    assert result["summary"]["runtime_status"] == "ok"
    assert devices["7:givi"]["python_runtime_summary"]["receipt_hash"]
    assert stored["givi"]["runtime_status"] == "ok"
    assert result["tool_log"]["tool_name"] == "device.check_runtime"


def test_runtime_endpoint_old_agent_error_is_explicit(monkeypatch):
    from server.routers import devices as devices_router

    devices.clear()
    devices["7:givi"] = {"ws": object(), "pending": {}, "user_id": 7, "short_device_id": "givi", "info": {}}

    async def fake_send(*args, **kwargs):
        return {"error": "Неизвестное действие: device.prepare_runtime"}

    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": 7})
    monkeypatch.setattr(devices_router, "send_command_to_agent", fake_send)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices_router.prepare_runtime_for_user({"id": 7, "name": "tester"}, "givi", "prepare", []))

    assert exc.value.status_code == 501
    assert "device.prepare_runtime" in exc.value.detail


def test_runtime_endpoint_disconnect_during_prepare_is_recoverable_detail(monkeypatch):
    from server.routers import devices as devices_router

    devices.clear()
    devices["7:givi"] = {"ws": object(), "pending": {}, "user_id": 7, "short_device_id": "givi", "info": {}}

    async def fake_send(*args, **kwargs):
        return {"error": "AGENT_DISCONNECTED: устройство 'givi' отключилось во время выполнения команды"}

    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": 7})
    monkeypatch.setattr(devices_router, "send_command_to_agent", fake_send)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices_router.prepare_runtime_for_user({"id": 7, "name": "tester"}, "givi", "prepare", []))

    assert exc.value.status_code == 409
    assert "runtime_prepare_interrupted" in exc.value.detail
    assert "агент отключился" in exc.value.detail
    assert "Повторите check после переподключения" in exc.value.detail
