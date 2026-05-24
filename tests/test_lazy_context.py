import json

from server.controller_prompts import SYSTEM_PROMPT_TEMPLATE
from server.device_activation import compact_activation_summary
from server.device_context import activation_markers_for_task, build_minimal_llm_context, get_context_handle


def _receipt(status="ok", runtime_status="ok", python="available"):
    return {
        "activation_version": 1,
        "device_id": "givi",
        "activation_mode": "soft",
        "activation_status": status,
        "identity": {"hostname": "GIVI", "machine_guid": "guid"},
        "paths": {"iru_home": r"C:\Users\tester\AppData\Local\IRU"},
        "runtime": {"managed_python_status": runtime_status},
        "capabilities": {"execute_cmd": "available", "write_content": "available", "python": python},
        "health": {"agent": "ok"},
        "warnings": [],
        "next_actions": [],
        "created_at": "2026-05-16T00:00:00Z",
    }


def test_minimal_context_has_handles_without_full_receipts():
    receipt = _receipt()
    context = build_minimal_llm_context(
        "givi",
        {"givi": {"info": {"hostname": "GIVI"}, "ws": object(), "activation_receipt": receipt}},
    )
    raw = json.dumps(context, ensure_ascii=False)
    handles = context["current_device"]["context_handles"]
    assert handles["activation_receipt"] == "ctx://device/givi/activation"
    assert handles["python_runtime"] == "ctx://device/givi/python"
    assert handles["device_state"] == "ctx://device/givi/state"
    assert handles["artifacts"] == "ctx://device/givi/artifacts"
    assert handles["recent_traces"] == "ctx://device/givi/traces"
    assert "python_receipt" not in raw
    assert "raw_evidence" not in raw


def test_full_activation_receipt_only_by_handle():
    receipt = _receipt()
    response = get_context_handle(
        "ctx://device/givi/activation",
        all_devices={"givi": {"ws": object(), "activation_receipt": receipt}},
    )

    assert response == {"status": "ok", "source": "agent_live", "data": receipt}


def test_offline_handle_returns_stale_cache(monkeypatch):
    summary = compact_activation_summary(_receipt())
    monkeypatch.setattr("server.device_context.db.get_device_profile", lambda device_id: {"activation_summary": summary})
    response = get_context_handle("ctx://device/givi/activation", all_devices={})

    assert response["status"] == "stale"
    assert response["source"] == "server_cache"
    assert response["data"]["receipt_hash"]


def test_state_handle_and_manifest_include_fresh_last_snapshot():
    state_record = {
        "snapshot": {"process_count": 180},
        "collected_at": "2026-05-16T10:00:00Z",
        "identity_receipt": {"identity_status": "ok"},
        "health_summary": {"health_status": "ok", "identity_status": "ok", "process_count": 180},
        "status": "ok",
    }
    all_devices = {"givi": {"info": {"hostname": "GIVI"}, "ws": object(), "last_state_snapshot": state_record}}

    context = build_minimal_llm_context("givi", all_devices)
    state_summary = context["current_device"]["state_summary"]
    handle = get_context_handle("ctx://device/givi/state", all_devices=all_devices)

    assert state_summary["state_snapshot_fresh"] is True
    assert state_summary["last_snapshot_at"] == "2026-05-16T10:00:00Z"
    assert context["current_device"]["context_handles"]["device_state"] == "ctx://device/givi/state"
    assert handle == {"status": "ok", "source": "agent_live", "data": state_record}


def test_state_manifest_can_use_agent_cached_snapshot():
    cached_record = {
        "snapshot": {"process_count": 180, "gpus": [{"name": "RTX 4060"}]},
        "collected_at": "2026-05-16T10:00:00Z",
        "identity_receipt": {"identity_status": "ok"},
        "health_summary": {"health_status": "ok", "identity_status": "ok", "process_count": 180, "gpu_summary": ["RTX 4060"], "gpu_count": 1},
        "status": "ok",
    }
    all_devices = {"givi": {"info": {"hostname": "GIVI"}, "ws": object(), "agent_cached_passport": {"state_snapshot": cached_record}}}

    context = build_minimal_llm_context("givi", all_devices)
    state_summary = context["current_device"]["state_summary"]
    handle = get_context_handle("ctx://device/givi/state", all_devices=all_devices)

    assert state_summary["state_snapshot_source"] == "agent_cache"
    assert state_summary["state_snapshot_fresh"] is False
    assert state_summary["gpu_summary"] == ["RTX 4060"]
    assert handle == {"status": "stale", "source": "agent_cache", "data": cached_record}


def test_prompt_includes_context_budget_rule():
    assert "Context budget rule:" in SYSTEM_PROMPT_TEMPLATE
    assert "Lazy context rule:" in SYSTEM_PROMPT_TEMPLATE


def test_activation_and_runtime_markers_are_separate():
    missing = build_minimal_llm_context("givi", {"givi": {"info": {"hostname": "GIVI"}, "ws": object()}})
    soft_missing_runtime = build_minimal_llm_context(
        "givi",
        {"givi": {"info": {"hostname": "GIVI"}, "ws": object(), "activation_receipt": _receipt(runtime_status="missing", python="missing")}},
    )
    missing_markers = activation_markers_for_task("create python app", missing)
    runtime_markers = activation_markers_for_task("create python app", soft_missing_runtime)

    assert "target_device_not_activated" in missing_markers
    assert "target_device_runtime_not_ready" in missing_markers
    assert "target_device_not_activated" not in runtime_markers
    assert "target_device_runtime_not_ready" in runtime_markers


def test_managed_runtime_ok_overrides_activation_python_missing():
    activation_summary = compact_activation_summary(_receipt(runtime_status="missing", python="missing"))
    context = build_minimal_llm_context(
        "givi",
        {
            "givi": {
                "info": {"hostname": "GIVI"},
                "ws": object(),
                "activation_summary": activation_summary,
                "python_runtime_summary": {
                    "runtime_status": "ok",
                    "python_source": "system",
                    "venv_python": r"C:\Users\tester\AppData\Local\IRU\runtime\venv\Scripts\python.exe",
                    "python_version": "3.11.9",
                    "pip_status": "ok",
                    "last_runtime_check": "2026-05-22T00:00:00Z",
                    "receipt_hash": "abc",
                },
            }
        },
    )

    current = context["current_device"]
    markers = activation_markers_for_task("create python app", context)

    assert current["runtime_status"] == "ok"
    assert "python" in current["capabilities_summary"]
    assert "target_device_runtime_not_ready" not in markers
