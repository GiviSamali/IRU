import asyncio
import json
import time

import httpx
import pytest
from fastapi import HTTPException

from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_pipeline import pipeline_worker_prompt, run_pipeline_worker
from server.controller_shared import build_device_profile_block, build_devices_block
from server import task_runtime


def _execute_call(command: str) -> dict:
    return {
        "id": "call-1",
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps({"command": command}),
        },
    }


def test_devices_block_does_not_copy_live_state_between_devices():
    block = build_devices_block({
        "GIVI": {
            "info": {
                "hostname": "GIVI",
                "os": "Windows",
                "os_version": "11",
                "live_snapshot": {
                    "source_device_id": "GIVI",
                    "collected_at": "2026-05-14T10:00:00Z",
                    "cpu_usage": "17%",
                    "ram_usage": "16GB",
                    "process_count": 259,
                },
            }
        },
        "DESKTOP-JFQUB4O": {
            "info": {
                "hostname": "DESKTOP-JFQUB4O",
                "os": "Windows",
                "os_version": "10",
            }
        },
    })

    assert "device_id=GIVI" in block
    assert "live_snapshot=present" in block
    assert "source_device_id=GIVI" in block
    desktop_line = next(line for line in block.splitlines() if "device_id=DESKTOP-JFQUB4O" in line)
    assert "fresh state unavailable" in desktop_line
    assert "259" not in desktop_line
    assert "16GB" not in desktop_line


def test_cross_device_live_snapshot_source_is_rejected():
    block = build_devices_block({
        "DESKTOP-JFQUB4O": {
            "info": {
                "hostname": "DESKTOP-JFQUB4O",
                "live_snapshot": {
                    "source_device_id": "GIVI",
                    "cpu_usage": "17%",
                    "process_count": 259,
                },
            }
        },
    })

    assert "live_snapshot=invalid_source:GIVI" in block
    assert "fresh state unavailable" in block
    assert "process_count=259" not in block


def test_cached_profile_is_not_marked_as_live_current_state():
    block = build_device_profile_block({
        "hostname": "GIVI",
        "cpu": "i7-13700H",
        "gpu": "RTX 4060",
        "ram_gb": 16,
        "disks": [{"drive": "C:", "total_gb": 476, "free_gb": 200}],
    })

    assert "Cached device profile" in block
    assert "not live current state" in block
    assert "cached_cpu: i7-13700H" in block
    assert "current_cpu" not in block


def test_non_pipeline_command_log_includes_target_device_id():
    async def _send_command_fn(device_id, action, params):
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    async def _chat_completion_request_fn(**kwargs):
        messages = kwargs["messages"]
        if not any(msg.get("role") == "tool" for msg in messages):
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_execute_call("whoami")]},
                }]
            }
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "ok"},
            }]
        }

    result = asyncio.run(process_non_pipeline_command(
        user_message="run",
        device_id="device-a",
        device_info={"hostname": "alpha", "os": "Windows"},
        send_command_fn=_send_command_fn,
        get_file_link_fn=lambda device_id, path: "/api/download/mock",
        chat_history=[],
        user_id=None,
        chat_id=None,
        modes={},
        poll_task_id=None,
        cfg={"model": "mock", "max_tokens": 512},
        system_msg="system",
        machine_guid=None,
        mem_user_id=None,
        non_pipeline_tools=[],
        max_iterations=3,
        pick_model_fn=lambda cfg, modes: "mock",
        chat_completion_request_fn=_chat_completion_request_fn,
    ))

    entry = result["commands"][0]
    assert entry["target_device_id"] == "device-a"
    assert entry["hostname"] == "alpha"
    assert entry["collected_at"]


def test_pipeline_command_log_includes_target_device_id():
    async def _send_command_fn(device_id, action, params):
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    async def _chat_completion_request_fn(**kwargs):
        messages = kwargs["messages"]
        if not any(msg.get("role") == "tool" for msg in messages):
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_execute_call("whoami")]},
                }]
            }
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "ok"},
            }]
        }

    shared = {
        "current_device_id": "device-a",
        "current_hostname": "alpha",
        "current_os": "Windows",
        "current_os_version": "11",
        "device_profile_block": "",
        "device_memory_block": "",
        "devices_block": "",
        "other_devices_summary": "",
        "target_device_id": "device-a",
        "os_rules": "",
        "current_datetime_msk": "2026-05-14 12:00",
    }

    async def _run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(
                client=client,
                cfg={"model": "mock"},
                model="mock",
                shared=shared,
                overall_goal="run",
                step={"title": "run", "instruction": "run", "device_id": "device-a"},
                completed_steps=[],
                chat_history=[],
                send_command_fn=_send_command_fn,
                get_file_link_fn=lambda device_id, path: "/api/download/mock",
                machine_guid=None,
                mem_user_id=None,
                poll_task_id=None,
                chat_completion_request_fn=_chat_completion_request_fn,
                worker_tools=[],
            )

    result = asyncio.run(_run())
    entry = result["commands"][0]
    assert entry["target_device_id"] == "device-a"
    assert entry["hostname"] == "alpha"
    assert entry["collected_at"]


def test_pipeline_completed_other_device_step_not_live_state():
    prompt = pipeline_worker_prompt(
        {
            "current_device_id": "device-b",
            "current_hostname": "bravo",
            "current_os": "Windows",
            "current_os_version": "11",
            "device_profile_block": "",
            "device_memory_block": "",
            "devices_block": "",
            "target_device_id": "device-b",
            "os_rules": "",
            "current_datetime_msk": "2026-05-14 12:00",
        },
        "describe states",
        {"title": "describe B", "instruction": "describe B", "device_id": "device-b"},
        [{
            "title": "snapshot A",
            "summary": "GIVI cpu_usage=17%, process_count=259",
            "device_id": "device-a",
            "hostname": "GIVI",
        }],
    )

    assert "OTHER DEVICE device_id=device-a hostname=GIVI" in prompt
    assert "informational only" in prompt
    assert "do not reuse paths as target-device paths" in prompt


def test_state_all_devices_uses_deterministic_fanout(monkeypatch):
    task_id = "state-task"
    user_id = 1
    device_ids = [f"{user_id}:givi", f"{user_id}:desktop"]
    task_runtime.devices[device_ids[0]] = {"user_id": user_id, "info": {"hostname": "givi"}, "pending": {}, "ws": object()}
    task_runtime.devices[device_ids[1]] = {"user_id": user_id, "info": {"hostname": "desktop"}, "pending": {}, "ws": object()}
    task_runtime.tasks[task_id] = {
        "task_id": task_id,
        "user_id": user_id,
        "chat_id": 1,
        "message": "проверь текущее состояние на всех устройствах",
        "device_ids": device_ids,
        "status": "running",
        "results": {},
        "modes": {},
        "created_at": time.time(),
    }
    calls = []

    async def fake_collect(device_id, user_id=None):
        calls.append(device_id)
        short = device_id.split(":", 1)[1]
        return {
            "device_id": device_id,
            "target_device_id": short,
            "status": "ok",
            "answer": f"target_device_id={short}; identity_status=ok",
            "commands": [{"target_device_id": short, "collected_at": "now"}],
        }

    async def fail_process(**kwargs):
        raise AssertionError("state fanout must not use LLM primary flow")

    async def fail_send(*args, **kwargs):
        raise AssertionError("state fanout test uses collect helper, not replay")

    monkeypatch.setattr(task_runtime, "collect_device_live_snapshot", fake_collect)
    monkeypatch.setattr(task_runtime, "process_nl_command", fail_process)
    monkeypatch.setattr(task_runtime, "send_command_to_agent", fail_send)
    monkeypatch.setattr(task_runtime, "add_message", lambda *args, **kwargs: None)

    asyncio.run(task_runtime.run_nl_task(task_id, user_id, "проверь текущее состояние на всех устройствах", device_ids, 1))

    assert calls == device_ids
    assert task_runtime.tasks[task_id]["status"] == "done"
    assert "target_device_id=givi" not in task_runtime.tasks[task_id]["answer"]
    assert "target_device_id=desktop" not in task_runtime.tasks[task_id]["answer"]
    assert "Состояние устройства givi" in task_runtime.tasks[task_id]["answer"]
    assert "Состояние устройства desktop" in task_runtime.tasks[task_id]["answer"]
    assert len(task_runtime.tasks[task_id]["commands"]) == 2


def test_collect_live_snapshot_stores_last_state_snapshot(monkeypatch):
    user_id = 1
    device_key = f"{user_id}:givi"
    task_runtime.devices[device_key] = {
        "user_id": user_id,
        "info": {"hostname": "givi", "os": "Windows"},
        "registered_identity": {"target_device_id": "givi", "registered_hostname": "givi", "registered_machine_guid": "guid"},
        "pending": {},
        "ws": object(),
    }

    async def fake_send(device_id, action, params, user_id=None):
        payload = {
            "observed_hostname": "givi",
            "observed_computer_name": "givi",
            "observed_machine_guid": "guid",
            "ram_total_gb": 16,
            "ram_free_gb": 4,
            "disks": [{"drive": "C:", "total_gb": 100, "free_gb": 25}],
            "cpu_load": 32,
            "process_count": 180,
            "uptime": "1.00:00:00",
        }
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

    monkeypatch.setattr(task_runtime, "send_command_to_agent", fake_send)
    monkeypatch.setattr(task_runtime, "get_device_profile", lambda device_id: {"device_id": device_id, "machine_guid": "guid"})

    result = asyncio.run(task_runtime.collect_device_live_snapshot(device_key, user_id=user_id))

    record = task_runtime.devices[device_key]["last_state_snapshot"]
    assert result["status"] == "ok"
    assert record["snapshot"]["process_count"] == 180
    assert record["identity_receipt"]["identity_status"] == "ok"
    assert record["health_summary"]["ram_used_pct"] == 75.0
    assert record["health_summary"]["disk_used_pct"] == 75.0


def test_devices_api_includes_state_summary_fields(monkeypatch):
    from server.routers import devices as devices_router

    device_key = "7:givi"
    task_runtime.devices[device_key] = {
        "user_id": 7,
        "short_device_id": "givi",
        "info": {"hostname": "GIVI", "os": "Windows"},
        "pending": {},
        "ws": object(),
        "last_state_snapshot": {
            "snapshot": {"process_count": 180},
            "collected_at": "2026-05-16T10:00:00Z",
            "identity_receipt": {"identity_status": "ok"},
            "health_summary": {"health_status": "warning", "identity_status": "ok", "cpu_load": 12, "ram_used_pct": 88, "disk_used_pct": 71, "process_count": 180, "uptime": "1 day"},
        },
    }

    monkeypatch.setattr(devices_router, "get_current_user", lambda request: {"id": 7, "name": "tester"})
    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: None)

    result = asyncio.run(devices_router.get_devices_api(object()))

    item = result["devices"]["givi"]
    assert item["health_status"] == "warning"
    assert item["last_snapshot_at"] == "2026-05-16T10:00:00Z"
    assert item["identity_status"] == "ok"
    assert item["cpu_load"] == 12
    assert item["ram_used_pct"] == 88
    assert item["disk_used_pct"] == 71
    assert item["process_count"] == 180
    assert item["uptime"] == "1 day"


def test_device_state_endpoint_returns_structured_snapshot(monkeypatch):
    from server.routers import devices as devices_router

    class Request:
        async def json(self):
            return {"mode": "snapshot"}

    user_id = 7
    device_key = f"{user_id}:givi"
    task_runtime.devices[device_key] = {
        "user_id": user_id,
        "short_device_id": "givi",
        "info": {"hostname": "givi", "os": "Windows"},
        "registered_identity": {"target_device_id": "givi", "registered_hostname": "givi", "registered_machine_guid": "guid"},
        "pending": {},
        "ws": object(),
    }

    async def fake_send(device_id, action, params, user_id=None):
        payload = {"observed_hostname": "givi", "observed_machine_guid": "guid", "process_count": 200, "ram_total_gb": 8, "ram_free_gb": 2}
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

    monkeypatch.setattr(devices_router, "get_current_user", lambda request: {"id": user_id, "name": "tester"})
    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": user_id, "machine_guid": "guid"})
    monkeypatch.setattr(task_runtime, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": user_id, "machine_guid": "guid"})
    monkeypatch.setattr(task_runtime, "send_command_to_agent", fake_send)

    result = asyncio.run(devices_router.api_device_state("givi", Request()))

    assert result["status"] == "ok"
    assert result["snapshot"]["process_count"] == 200
    assert result["health_summary"]["ram_used_pct"] == 75.0
    assert result["last_state_snapshot"]["snapshot"]["process_count"] == 200


def test_agent_control_endpoints_ack_or_explicit_501(monkeypatch):
    from server.routers import devices as devices_router

    device_key = "7:givi"
    task_runtime.devices[device_key] = {"user_id": 7, "short_device_id": "givi", "info": {}, "pending": {}, "ws": object()}

    monkeypatch.setattr(devices_router, "get_current_user", lambda request: {"id": 7, "name": "tester"})
    monkeypatch.setattr(devices_router, "get_device_profile", lambda device_id: {"device_id": device_id, "user_id": 7})

    async def fake_send(device_id, action, params, user_id=None):
        if action == "agent.shutdown":
            return {"ack": True, "action": action}
        return {"error": "unknown action"}

    monkeypatch.setattr(devices_router, "send_command_to_agent", fake_send)

    shutdown = asyncio.run(devices_router.api_shutdown_device("givi", object()))
    assert shutdown["ack"]["ack"] is True
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices_router.api_disconnect_device("givi", object()))
    assert exc.value.status_code == 501


def test_collect_live_snapshot_identity_mismatch(monkeypatch):
    user_id = 1
    device_key = f"{user_id}:givi"
    task_runtime.devices[device_key] = {
        "user_id": user_id,
        "info": {"hostname": "givi", "os": "Windows"},
        "registered_identity": {"target_device_id": "givi", "registered_hostname": "givi", "registered_machine_guid": "reg-guid"},
        "pending": {},
        "ws": object(),
    }

    async def fake_send(device_id, action, params, user_id=None):
        payload = {
            "observed_hostname": "DESKTOP-JFQUB4O",
            "observed_computer_name": "DESKTOP-JFQUB4O",
            "observed_username": "user",
            "process_count": 259,
        }
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

    monkeypatch.setattr(task_runtime, "send_command_to_agent", fake_send)

    result = asyncio.run(task_runtime.collect_device_live_snapshot(device_key, user_id=user_id))

    assert result["status"] == "routing_mismatch"
    assert result["identity_receipt"]["identity_status"] == "mismatch"
    assert "устройство givi ответило как DESKTOP-JFQUB4O" in result["answer"]
    assert "target_device_id=" not in result["answer"]
    assert result["snapshot"] is None
    assert result["commands"][0]["target_device_id"] == "givi"
    assert result["commands"][0]["collected_at"]
    assert result["commands"][0]["result"]["identity_receipt"]["identity_status"] == "mismatch"


def test_identical_resource_values_do_not_imply_same_device():
    answer = task_runtime._format_live_snapshot_summary([
        {
            "target_device_id": "givi",
            "status": "ok",
            "answer": "target_device_id=givi; identity_status=ok; cpu=i7; ram_total_gb=16",
        },
        {
            "target_device_id": "desktop",
            "status": "ok",
            "answer": "target_device_id=desktop; identity_status=ok; cpu=i7; ram_total_gb=16",
        },
    ])

    lowered = answer.lower()
    assert "same physical" not in lowered
    assert "одно физическое" not in lowered
    assert "target_device_id=givi" not in answer
    assert "target_device_id=desktop" not in answer
    assert "Состояние устройства givi" in answer
    assert "Состояние устройства desktop" in answer
