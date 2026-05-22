import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from server.device_activation import activation_state_from_receipt, runtime_status_from_receipt, validate_activation_receipt
from server.runtime_state import devices

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _windows_iru_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from core import actions

    monkeypatch.setattr(actions.platform, "system", lambda: "Windows")
    return actions


def _server_receipt(device_id="givi"):
    return {
        "activation_version": 1,
        "device_id": device_id,
        "activation_mode": "soft",
        "activation_status": "ok",
        "identity": {"hostname": "GIVI", "machine_guid": "guid"},
        "paths": {"iru_home": r"C:\Users\tester\AppData\Local\IRU"},
        "runtime": {"managed_python_status": "ok"},
        "capabilities": {"execute_cmd": "available", "python": "available"},
        "created_at": "2026-05-16T00:00:00Z",
    }


def test_soft_activation_persists_receipt_and_is_idempotent(monkeypatch, tmp_path):
    actions = _windows_iru_home(monkeypatch, tmp_path)

    first = actions.activate_device(mode="soft", device_id="givi")
    second = actions.activate_device(mode="soft", device_id="givi")

    assert first["activation_version"] == 1
    assert first["device_id"] == "givi"
    assert first["identity"]
    assert first["paths"]["iru_home"] == str(tmp_path / "IRU")
    assert first["activation_status"] == "ok"
    assert first["capabilities"]["execute_cmd"] == "available"
    assert second["activation_status"] != "failed"
    state_dir = tmp_path / "IRU" / "state"
    for name in ("activation.json", "identity.json", "capabilities.json", "python_receipt.json", "health.json"):
        assert (state_dir / name).exists()
    assert (tmp_path / "IRU" / "runtime" / "venv").exists()
    assert not (tmp_path / "IRU" / "runtime" / "venvs" / "default").exists()


def test_full_activation_without_managed_python_is_degraded(monkeypatch, tmp_path):
    actions = _windows_iru_home(monkeypatch, tmp_path)

    receipt = actions.activate_device(mode="full", device_id="givi")

    assert receipt["activation_status"] == "degraded"
    assert runtime_status_from_receipt(receipt) == "install_required"
    assert "install_managed_python" in receipt["next_actions"]


def test_ok_receipt_missing_iru_home_is_invalid_not_activated():
    receipt = {
        "activation_version": 1,
        "device_id": "givi",
        "activation_status": "ok",
        "identity": {"hostname": "GIVI"},
        "paths": {},
        "runtime": {},
        "capabilities": {},
        "created_at": "2026-05-16T00:00:00Z",
    }

    valid, reason = validate_activation_receipt(receipt)

    assert valid is False
    assert reason == "missing_iru_home"
    assert activation_state_from_receipt(receipt) == "activation_required"


def test_ok_receipt_missing_hostname_or_computer_name_is_invalid():
    receipt = {
        "activation_version": 1,
        "device_id": "givi",
        "activation_status": "ok",
        "identity": {"machine_guid": "guid"},
        "paths": {"iru_home": r"C:\Users\tester\AppData\Local\IRU"},
        "runtime": {},
        "capabilities": {},
        "created_at": "2026-05-16T00:00:00Z",
    }

    valid, reason = validate_activation_receipt(receipt)

    assert valid is False
    assert reason == "missing_identity_hostname"
    assert activation_state_from_receipt(receipt) == "activation_required"


def test_activate_device_for_user_sends_action_and_stores_summary(monkeypatch):
    from server.routers import devices as devices_router

    devices.clear()
    devices["7:givi"] = {"ws": object(), "pending": {}, "user_id": 7, "short_device_id": "givi", "info": {}}
    sent = {}
    stored = {}

    async def fake_send(device_key, action, params, user_id=None):
        sent.update({"device_key": device_key, "action": action, "params": params, "user_id": user_id})
        return _server_receipt()

    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": 7})
    monkeypatch.setattr(devices_router, "send_command_to_agent", fake_send)
    monkeypatch.setattr(devices_router, "update_device_activation_summary", lambda device_id, summary: stored.update({device_id: summary}))

    result = asyncio.run(devices_router.activate_device_for_user({"id": 7, "name": "tester"}, "givi", "soft"))

    assert sent == {
        "device_key": "7:givi",
        "action": "device.activate",
        "params": {"mode": "soft", "device_id": "givi"},
        "user_id": 7,
    }
    assert result["receipt"]["device_id"] == "givi"
    assert result["summary"]["activation_status"] == "activated"
    assert devices["7:givi"]["activation_receipt"]["device_id"] == "givi"
    assert devices["7:givi"]["activation_summary"]["receipt_hash"]
    assert stored["givi"]["activation_status"] == "activated"


def test_activate_device_for_user_rejects_invalid_receipt(monkeypatch):
    from server.routers import devices as devices_router

    devices.clear()
    devices["7:givi"] = {"ws": object(), "pending": {}, "user_id": 7, "short_device_id": "givi", "info": {}}
    receipt = _server_receipt()
    receipt["identity"] = {"machine_guid": "guid"}
    stored = {}

    async def fake_send(*args, **kwargs):
        return receipt

    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": 7})
    monkeypatch.setattr(devices_router, "send_command_to_agent", fake_send)
    monkeypatch.setattr(devices_router, "update_device_activation_summary", lambda device_id, summary: stored.update({device_id: summary}))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices_router.activate_device_for_user({"id": 7, "name": "tester"}, "givi", "soft"))

    assert exc.value.status_code == 409
    assert "missing_identity_hostname" in exc.value.detail
    assert "activation_summary" not in devices["7:givi"]
    assert stored == {}


def test_send_command_to_agent_does_not_store_invalid_activation_receipt(monkeypatch):
    from server import task_runtime

    device_key = "7:givi"
    existing_summary = {"activation_status": "activated", "receipt_hash": "old"}
    invalid_receipt = _server_receipt(device_id="")
    stored = []

    class FakeWS:
        async def send_text(self, _msg):
            pending = next(iter(task_runtime.devices[device_key]["pending"].values()))
            pending.set_result(invalid_receipt)

    task_runtime.devices.clear()
    task_runtime.devices[device_key] = {
        "ws": FakeWS(),
        "pending": {},
        "user_id": 7,
        "short_device_id": "givi",
        "info": {},
        "activation_summary": existing_summary,
    }

    monkeypatch.setattr(task_runtime, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": 7})
    monkeypatch.setattr(task_runtime, "update_device_activation_summary", lambda device_id, summary: stored.append((device_id, summary)))

    result = asyncio.run(
        task_runtime.send_command_to_agent(
            device_key,
            "device.activate",
            {"mode": "soft", "device_id": "givi"},
            user_id=7,
        )
    )

    assert result["device_id"] == ""
    assert "activation_receipt" not in task_runtime.devices[device_key]
    assert task_runtime.devices[device_key]["activation_summary"] is existing_summary
    assert stored == []


def test_activate_device_for_user_offline_returns_error(monkeypatch):
    from server.routers import devices as devices_router

    devices.clear()
    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": 7})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices_router.activate_device_for_user({"id": 7, "name": "tester"}, "givi", "soft"))

    assert exc.value.status_code == 503
    assert "offline" in exc.value.detail


def test_contributing_no_longer_differs_from_main():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "diff", "--quiet", "main", "--", "CONTRIBUTING.md"], cwd=repo)

    assert result.returncode == 0
