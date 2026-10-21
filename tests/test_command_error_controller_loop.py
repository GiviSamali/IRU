import asyncio
import json

import pytest

from server.controller_non_pipeline import process_non_pipeline_command
from server.runtime_state import tasks
from server.controller_tools import NON_PIPELINE_TOOLS


def _execute_call(call_id: str, command: str) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "execute_cmd",
            "arguments": json.dumps({"command": command}),
        },
    }


def _answer_call(call_id: str, text: str) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "answer_text",
            "arguments": json.dumps({
                "answer_type": "pure_text",
                "text": text,
                "basis": [],
                "self_check": {
                    "depends_on_current_external_state": False,
                    "claims_completed_action": False,
                    "has_sufficient_evidence": True,
                    "missing_evidence_question": "",
                },
            }, ensure_ascii=False),
        },
    }


@pytest.mark.parametrize("goal_member", [False, True])
def test_worker_dependency_error_is_structured_observation(monkeypatch, goal_member):
    task_id = "dependency-error-goal" if goal_member else None
    if goal_member:
        monkeypatch.setitem(tasks, task_id, {"task_id": task_id, "goal_id": "goal",
            "original_request": "check PyQt5", "proposed_objective": "check PyQt5",
            "execution_scope": "general", "window_effects": []})
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
        result = payload["result"]
        assert result["error_type"] == "dependency_missing"
        assert result["missing_packages"] == ["PyQt5"]
        assert result["command_error"]["recoverable"] is True
        return {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": [_answer_call("call-answer", "PyQt5 dependency error: missing package.")]},
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
            poll_task_id=task_id,
            cfg={"model": "mock-model", "max_tokens": 512},
            system_msg="system",
            machine_guid=None,
            mem_user_id=None,
            non_pipeline_tools=NON_PIPELINE_TOOLS,
            max_iterations=4,
            pick_model_fn=lambda cfg, modes: "mock-model",
            chat_completion_request_fn=_chat_completion_request_fn,
        )
    )

    assert len(captured_messages) == 2
    assert "budget_guard" not in [cmd.get("action") for cmd in result.get("commands", [])]
    assert result["commands"][0]["result"]["error_type"] == "dependency_missing"
    assert result["answer"] == "PyQt5 dependency error: missing package."
