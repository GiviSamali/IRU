import sys
from pathlib import Path

from server.device_activation import activation_state_from_receipt, runtime_status_from_receipt, validate_activation_receipt

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _windows_iru_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from core import actions

    monkeypatch.setattr(actions.platform, "system", lambda: "Windows")
    return actions


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
        "identity": {},
        "paths": {},
        "runtime": {},
        "capabilities": {},
    }

    valid, reason = validate_activation_receipt(receipt)

    assert valid is False
    assert reason == "missing_iru_home"
    assert activation_state_from_receipt(receipt) == "activation_required"
