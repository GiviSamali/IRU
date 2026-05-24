import json
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _agent_modules(monkeypatch, tmp_path):
    from core import actions, local_state, runtime

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(local_state.platform, "system", lambda: "Windows")
    monkeypatch.setattr(actions.platform, "system", lambda: "Windows")
    return actions, local_state, runtime


def test_local_state_read_write_json_and_invalid_json(monkeypatch, tmp_path):
    _actions, local_state, _runtime = _agent_modules(monkeypatch, tmp_path)

    local_state.write_json_state("sample", {"ok": True})
    assert local_state.read_json_state("sample") == {"ok": True}

    path = tmp_path / "IRU" / "state" / "broken.json"
    path.write_text("{not-json", encoding="utf-8")
    assert local_state.read_json_state("broken") == {}


def test_agent_refresh_state_writes_snapshot_and_passport(monkeypatch, tmp_path):
    actions, local_state, _runtime = _agent_modules(monkeypatch, tmp_path)
    payload = {
        "observed_hostname": "GIVI",
        "observed_computer_name": "GIVI",
        "observed_machine_guid": "guid",
        "process_count": 123,
        "ram_total_gb": 16,
        "ram_free_gb": 4,
        "gpus": [{"name": "NVIDIA RTX 4060", "adapter_ram_mb": 8192}],
    }

    monkeypatch.setattr(actions.platform_mod, "execute_cmd", lambda *args, **kwargs: {"returncode": 0, "stdout": json.dumps(payload), "stderr": "", "error": None})
    monkeypatch.setattr(actions, "collect_system_info", lambda device_id="": {"device_id": device_id, "hostname": "GIVI", "machine_guid": "guid"})

    result = actions.refresh_device_state(device_id="givi")

    assert result["status"] == "ok"
    assert result["health_summary"]["ram_used_pct"] == 75.0
    assert result["health_summary"]["gpu_summary"] == ["NVIDIA RTX 4060"]
    saved = local_state.read_json_state("state_snapshot")
    assert saved["snapshot"]["process_count"] == 123
    passport = local_state.read_json_state("device_passport")
    assert passport["state_snapshot_summary"]["gpu_count"] == 1
    assert passport["hardware_summary"]["gpus"][0]["name"] == "NVIDIA RTX 4060"


def test_agent_get_cached_passport_returns_saved_state_without_collecting(monkeypatch, tmp_path):
    actions, local_state, _runtime = _agent_modules(monkeypatch, tmp_path)
    local_state.update_device_passport_cache({"device_id": "givi", "state_snapshot_summary": {"process_count": 5}})

    def fail_collect(*args, **kwargs):
        raise AssertionError("cached passport must not collect a new snapshot")

    monkeypatch.setattr(actions.platform_mod, "execute_cmd", fail_collect)
    result = actions.get_cached_passport(device_id="givi")

    assert result["status"] == "ok"
    assert result["source"] == "agent_local_cache"
    assert result["passport"]["state_snapshot_summary"]["process_count"] == 5


def test_registration_payload_includes_cached_passport(monkeypatch, tmp_path):
    actions, local_state, runtime = _agent_modules(monkeypatch, tmp_path)
    local_state.update_device_passport_cache({
        "device_id": "givi",
        "activation_summary": {"activation_status": "activated"},
        "runtime_summary": {"runtime_status": "ok"},
        "state_snapshot_summary": {"process_count": 9},
        "hardware_summary": {"gpus": [{"name": "Intel UHD"}]},
    })
    monkeypatch.setattr(runtime, "collect_system_info", lambda device_id="": {"device_id": device_id, "hostname": "GIVI"})

    payload = runtime.build_registration_payload("givi", "1.2.3")

    assert payload["cached_passport"]["device_id"] == "givi"
    assert payload["activation_summary"]["activation_status"] == "activated"
    assert payload["runtime_summary"]["runtime_status"] == "ok"
    assert payload["state_snapshot_summary"]["process_count"] == 9
    assert payload["hardware_summary"]["gpus"][0]["name"] == "Intel UHD"
    assert payload["agent_version"] == "1.2.3"
