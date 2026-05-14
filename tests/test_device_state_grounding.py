import asyncio
import json

import httpx

from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_pipeline import pipeline_worker_prompt, run_pipeline_worker
from server.controller_shared import build_device_profile_block, build_devices_block


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
