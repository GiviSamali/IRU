import asyncio
import json

import httpx

from server.controller_pipeline import run_pipeline_worker


def _make_completion_fn(responses):
    queue = list(responses)

    async def _chat_completion_request_fn(**kwargs):
        assert queue, "No more mocked LLM responses left"
        return queue.pop(0)

    return _chat_completion_request_fn


def _execute_call(call_id, command, device_id=None):
    args = {"command": command}
    if device_id:
        args["device_id"] = device_id
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps(args),
        },
    }


def _shared_context(device_id="device-1"):
    return {
        "current_device_id": device_id,
        "current_hostname": "devbox",
        "current_os": "Windows",
        "current_os_version": "11",
        "device_profile_block": "",
        "device_memory_block": "",
        "devices_block": "",
        "os_rules": "",
        "current_datetime_msk": "2026-05-02 12:00",
    }


def _run_worker_case(responses, send_command_fn=None, step=None):
    async def _noop_send_command_fn(device_id, action, params):
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    def _noop_get_file_link_fn(device_id, file_path):
        return "/api/download/mock"

    async def _run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(
                client=client,
                cfg={"model": "mock-model"},
                model="mock-model",
                shared=_shared_context(),
                overall_goal="goal",
                step=step or {"title": "step", "instruction": "do it", "device_id": "device-1"},
                completed_steps=[],
                chat_history=[],
                send_command_fn=send_command_fn or _noop_send_command_fn,
                get_file_link_fn=_noop_get_file_link_fn,
                machine_guid=None,
                mem_user_id=None,
                poll_task_id=None,
                chat_completion_request_fn=_make_completion_fn(responses),
                worker_tools=[],
            )

    return asyncio.run(_run())


def test_pipeline_worker_stops_when_execute_command_budget_is_exceeded():
    calls = [_execute_call(f"call-{idx}", f"whoami {idx}") for idx in range(6)]
    executed = []

    async def _send_command_fn(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = _run_worker_case(
        responses=[{
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": calls},
            }]
        }],
        send_command_fn=_send_command_fn,
    )

    assert result["status"] == "error"
    assert len(executed) == 5
    assert result["commands"][-1]["action"] == "budget_guard"
    assert result["commands"][-1]["result"]["error"] == result["answer"]


def test_pipeline_worker_stops_repeated_similar_execute_commands():
    calls = [
        _execute_call("call-1", "Start-Process calc.exe"),
        _execute_call("call-2", 'Start-Process -FilePath "calc.exe"'),
        _execute_call("call-3", "Start-Process calc"),
        _execute_call("call-4", "Start-Process -FilePath calc.exe"),
    ]
    executed = []

    async def _send_command_fn(device_id, action, params):
        executed.append(params["command"])
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    result = _run_worker_case(
        responses=[{
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": calls},
            }]
        }],
        send_command_fn=_send_command_fn,
    )

    assert executed == [
        "Start-Process calc.exe",
        'Start-Process -FilePath "calc.exe"',
        "Start-Process calc",
    ]
    assert result["commands"][-1]["action"] == "budget_guard"


def test_pipeline_worker_single_execute_command_is_not_blocked():
    executed = []

    async def _send_command_fn(device_id, action, params):
        executed.append((device_id, action, params["command"]))
        return {"returncode": 0, "stdout": "user", "stderr": ""}

    result = _run_worker_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_execute_call("call-1", "whoami")]},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "ok"},
                }]
            },
        ],
        send_command_fn=_send_command_fn,
    )

    assert result["status"] == "ok"
    assert executed == [("device-1", "execute_cmd", "whoami")]
    assert result["answer"] == "ok"


def test_pipeline_worker_ignores_llm_device_override():
    seen_devices = []

    async def _send_command_fn(device_id, action, params):
        seen_devices.append(device_id)
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    _run_worker_case(
        responses=[
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_execute_call("call-1", "whoami", device_id="device-2")]},
                }]
            },
            {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "ok"},
                }]
            },
        ],
        send_command_fn=_send_command_fn,
    )

    assert seen_devices == ["device-1"]
