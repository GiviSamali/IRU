import asyncio
import json

import httpx

from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_pipeline import run_pipeline_worker


def _execute_call(call_id: str, command: str) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps({"command": command}),
        },
    }


def test_non_pipeline_dependency_error_is_structured_observation():
    captured_messages = []

    async def _send_command_fn(device_id, action, params):
        assert action == "execute_cmd"
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": "Traceback (most recent call last):\nModuleNotFoundError: No module named 'PyQt5'\n",
        }

    async def _chat_completion_request_fn(**kwargs):
        captured_messages.append(kwargs["messages"])
        if len(captured_messages) == 1:
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_execute_call("call-1", 'python -c "import PyQt5"')]},
                }]
            }

        tool_messages = [msg for msg in kwargs["messages"] if msg.get("role") == "tool"]
        assert tool_messages, "Expected execute_cmd result to be returned to the LLM"
        payload = json.loads(tool_messages[-1]["content"])
        assert payload["error_type"] == "dependency_missing"
        assert payload["missing_packages"] == ["PyQt5"]
        assert payload["command_error"]["recoverable"] is True
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "PyQt5 dependency error: missing package."},
            }]
        }

    result = asyncio.run(
        process_non_pipeline_command(
            user_message="check PyQt5",
            device_id="device-1",
            device_info={"os": "Windows", "hostname": "devbox"},
            send_command_fn=_send_command_fn,
            get_file_link_fn=lambda device_id, file_path: "/api/download/mock",
            chat_history=[],
            user_id=None,
            chat_id=None,
            modes={},
            poll_task_id=None,
            cfg={"model": "mock-model", "max_tokens": 512},
            system_msg="system",
            machine_guid=None,
            mem_user_id=None,
            non_pipeline_tools=[],
            max_iterations=4,
            pick_model_fn=lambda cfg, modes: "mock-model",
            chat_completion_request_fn=_chat_completion_request_fn,
        )
    )

    assert len(captured_messages) == 2
    assert "budget_guard" not in [cmd.get("action") for cmd in result.get("commands", [])]
    assert result["commands"][0]["result"]["error_type"] == "dependency_missing"
    assert result["answer"] == "PyQt5 dependency error: missing package."


def test_pipeline_worker_dependency_error_is_structured_observation():
    captured_messages = []

    async def _send_command_fn(device_id, action, params):
        assert action == "execute_cmd"
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": "Traceback (most recent call last):\nModuleNotFoundError: No module named 'PyQt5'\n",
        }

    async def _chat_completion_request_fn(**kwargs):
        captured_messages.append(kwargs["messages"])
        if len(captured_messages) == 1:
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [_execute_call("call-1", 'python -c "import PyQt5"')]},
                }]
            }

        tool_messages = [msg for msg in kwargs["messages"] if msg.get("role") == "tool"]
        assert tool_messages, "Expected execute_cmd result to be returned to the worker"
        payload = json.loads(tool_messages[-1]["content"])
        assert payload["error_type"] == "dependency_missing"
        assert payload["missing_packages"] == ["PyQt5"]
        assert payload["command_error"]["recoverable"] is True
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "PyQt5 dependency error: missing package."},
            }]
        }

    shared = {
        "current_device_id": "device-1",
        "current_hostname": "devbox",
        "current_os": "Windows",
        "current_os_version": "11",
        "device_profile_block": "",
        "device_memory_block": "",
        "devices_block": "",
        "other_devices_summary": "",
        "target_device_id": "device-1",
        "os_rules": "",
        "current_datetime_msk": "2026-05-14 12:00",
    }

    async def _run():
        async with httpx.AsyncClient() as client:
            return await run_pipeline_worker(
                client=client,
                cfg={"model": "mock-model"},
                model="mock-model",
                shared=shared,
                overall_goal="check PyQt5",
                step={"title": "check", "instruction": "check PyQt5", "device_id": "device-1"},
                completed_steps=[],
                chat_history=[],
                send_command_fn=_send_command_fn,
                get_file_link_fn=lambda device_id, file_path: "/api/download/mock",
                machine_guid=None,
                mem_user_id=None,
                poll_task_id=None,
                chat_completion_request_fn=_chat_completion_request_fn,
                worker_tools=[],
            )

    result = asyncio.run(_run())

    assert len(captured_messages) == 2
    assert result["status"] == "ok"
    assert "budget_guard" not in [cmd.get("action") for cmd in result.get("commands", [])]
    assert result["commands"][0]["result"]["error_type"] == "dependency_missing"
    assert result["answer"] == "PyQt5 dependency error: missing package."
